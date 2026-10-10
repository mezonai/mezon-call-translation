"""RTF + WER of one Whisper model under several runtime configs (plan 9.1c).

Clips from a manifest are laid out on one long "recording" separated by silence,
so every config decodes the same speech with the same known spans (no VAD in the
loop -- this measures the decoder, not segmentation).

Configs:
  cpu_int8_beam1_packed    production today, approximated: CPU int8, beam 1,
                           repetition_penalty 1.2, spans packed into <=30s
                           chunks and decoded one after another (marker audio
                           itself is not inserted)
  gpu_fp16_beam1_seq       same decode params on GPU float16, one span per call
  gpu_fp16_beam1_batched   span pipeline (plan 4.2): BatchedInferencePipeline
                           with our own clip_timestamps
  gpu_fp16_beam5_batched   same, beam 5, no repetition penalty

RTF = wall time / seconds of speech (model load and warm-up excluded).

Usage:
    python scripts/bench_rtf.py --manifest data/manifests/public_test_fleurs_vi_test.jsonl --minutes 30
    # WER of a converted model on a public test set, GPU only:
    python scripts/bench_rtf.py --model models/pilot-ct2 --configs gpu_fp16_beam5_batched \
        --manifest data/manifests/public_test_fleurs_vi_test.jsonl --minutes 0
"""

import argparse
import gc
import json
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mezon_whisper.normalize import normalize  # noqa: E402

SR = 16000
MAX_PACKED_S = 30.0
PACK_GUARD_S = 0.1

CONFIGS = {
    "cpu_int8_beam1_packed": dict(device="cpu", compute_type="int8", mode="packed", beam=1, rep=1.2),
    "gpu_fp16_beam1_seq": dict(device="cuda", compute_type="float16", mode="seq", beam=1, rep=1.2),
    "gpu_fp16_beam1_batched": dict(device="cuda", compute_type="float16", mode="batched", beam=1, rep=1.2),
    "gpu_fp16_beam5_batched": dict(device="cuda", compute_type="float16", mode="batched", beam=5, rep=1.0),
}


def load_recording(manifest: Path, minutes: float, gap_s: float, seed: int):
    """Returns (audio, spans in seconds, reference texts, language)."""
    records = [json.loads(line) for line in manifest.open(encoding="utf-8")]
    random.Random(seed).shuffle(records)
    gap = np.zeros(int(gap_s * SR), dtype=np.float32)
    parts, spans, refs, cursor, speech = [], [], [], 0, 0.0
    for rec in records:
        if minutes > 0 and speech >= minutes * 60:
            break
        if not normalize(rec["text"]):
            continue
        clip, sr = sf.read(str(ROOT / rec["audio"]), dtype="float32")
        assert sr == SR and clip.ndim == 1, f"{rec['audio']}: expected 16k mono"
        spans.append({"start": cursor / SR, "end": (cursor + len(clip)) / SR})
        refs.append(rec["text"])
        parts += [clip, gap]
        cursor += len(clip) + len(gap)
        speech += len(clip) / SR
    return np.concatenate(parts), spans, refs, records[0]["lang"]


def pack(spans: list[dict]) -> list[list[int]]:
    """Greedy groups of consecutive span indexes whose packed length stays <= 30s."""
    groups, current, length = [], [], 0.0
    for index, span in enumerate(spans):
        duration = span["end"] - span["start"]
        if current and length + PACK_GUARD_S + duration > MAX_PACKED_S:
            groups.append(current)
            current, length = [], 0.0
        length += duration + (PACK_GUARD_S if current else 0.0)
        current.append(index)
    if current:
        groups.append(current)
    return groups


def decode_kwargs(cfg: dict, language: str) -> dict:
    return dict(
        language=language,
        task="transcribe",
        beam_size=cfg["beam"],
        best_of=cfg["beam"],
        temperature=0.0,
        repetition_penalty=cfg["rep"],
        condition_on_previous_text=False,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        without_timestamps=True,
        word_timestamps=False,
    )


def run_config(name: str, cfg: dict, args, audio, spans, refs, language):
    """Returns (wall seconds, reference list, hypothesis list)."""
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    extra = {"cpu_threads": args.cpu_threads} if cfg["device"] == "cpu" else {}
    model = WhisperModel(args.model, device=cfg["device"], compute_type=cfg["compute_type"], **extra)
    kwargs = decode_kwargs(cfg, language)
    slices = [audio[int(s["start"] * SR): int(s["end"] * SR)] for s in spans]

    list(model.transcribe(slices[0][: 5 * SR], vad_filter=False, **kwargs)[0])  # warm-up

    start = time.perf_counter()
    if cfg["mode"] == "seq":
        hyps = ["".join(seg.text for seg in model.transcribe(clip, vad_filter=False, **kwargs)[0])
                for clip in slices]
        out_refs = refs
    elif cfg["mode"] == "packed":
        guard = np.zeros(int(PACK_GUARD_S * SR), dtype=np.float32)
        hyps, out_refs = [], []
        for group in pack(spans):
            chunk = np.concatenate([part for i in group for part in (slices[i], guard)][:-1])
            hyps.append("".join(seg.text for seg in model.transcribe(chunk, vad_filter=False, **kwargs)[0]))
            out_refs.append(" ".join(refs[i] for i in group))
    else:
        pipeline = BatchedInferencePipeline(model)
        segments, _ = pipeline.transcribe(audio, clip_timestamps=spans, batch_size=args.batch_size, **kwargs)
        by_seek: dict[int, str] = {}
        for seg in segments:
            by_seek[seg.seek] = by_seek.get(seg.seek, "") + seg.text
        # Same arithmetic as faster-whisper: seconds -> int samples -> seconds -> frames.
        seeks = [int(int(s["start"] * SR) / SR * model.frames_per_second) for s in spans]
        assert len(set(seeks)) == len(seeks), "two spans map to the same seek"
        unknown = set(by_seek) - set(seeks)
        assert not unknown, f"segments with a seek that matches no span: {sorted(unknown)[:5]}"
        hyps = [by_seek.get(seek, "") for seek in seeks]
        out_refs = refs
    wall = time.perf_counter() - start

    del model
    gc.collect()
    return wall, out_refs, hyps


def gpu_memory_mb() -> str:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return out.splitlines()[0]
    except Exception:
        return "?"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", nargs="+", required=True)
    parser.add_argument("--model", default="large-v3-turbo", help="faster-whisper model name or CT2 directory")
    parser.add_argument("--configs", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    parser.add_argument("--minutes", type=float, default=30, help="speech minutes per manifest, 0 = all")
    parser.add_argument("--gap", type=float, default=1.0, help="silence between clips, seconds")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--cpu-threads", type=int, default=8, help="production default is 8")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="reports/rtf_gpu_vs_cpu.md")
    args = parser.parse_args()

    import jiwer

    rows = []
    for manifest in args.manifest:
        audio, spans, refs, language = load_recording(Path(manifest), args.minutes, args.gap, args.seed)
        speech = sum(s["end"] - s["start"] for s in spans)
        dataset = Path(manifest).stem.removeprefix("public_test_")
        print(f"\n== {dataset}: {len(spans)} clips, {speech / 60:.1f} min speech, language={language} ==", flush=True)
        for name in args.configs:
            wall, out_refs, hyps = run_config(name, CONFIGS[name], args, audio, spans, refs, language)
            wer = jiwer.wer([normalize(r) for r in out_refs], [normalize(h) for h in hyps])
            empty = sum(1 for h in hyps if not normalize(h))
            row = dict(dataset=dataset, config=name, clips=len(spans), minutes=speech / 60, wall=wall,
                       rtf=wall / speech, speed=speech / wall, wer=wer * 100, empty=empty,
                       vram=gpu_memory_mb() if CONFIGS[name]["device"] == "cuda" else "-")
            rows.append(row)
            print(f"{name:26s} wall {wall:7.1f}s  RTF {row['rtf']:.4f}  x{row['speed']:6.1f}  "
                  f"WER {row['wer']:5.2f}%  empty {empty}", flush=True)

    lines = [
        f"# RTF + WER: `{args.model}`",
        "",
        f"- Ngày chạy: {datetime.now():%Y-%m-%d %H:%M}",
        f"- Tham số: batch_size={args.batch_size}, cpu_threads={args.cpu_threads}, gap={args.gap}s, seed={args.seed}",
        "- RTF = thời gian xử lý / số giây tiếng nói (không tính load model và warm-up). "
        "`cpu_int8_beam1_packed` là xấp xỉ pipeline production hiện tại.",
        "- WER tính gộp theo corpus sau `mezon_whisper.normalize`. `VRAM` là bộ nhớ GPU đang dùng ngay sau khi chạy xong (MiB).",
        "",
        "| Dataset | Config | Clip | Phút | Wall (s) | RTF | Nhanh hơn realtime | WER % | Output rỗng | VRAM |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['dataset']} | `{r['config']}` | {r['clips']} | {r['minutes']:.1f} | {r['wall']:.1f} | "
                     f"{r['rtf']:.4f} | ×{r['speed']:.1f} | {r['wer']:.2f} | {r['empty']} | {r['vram']} |")
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
