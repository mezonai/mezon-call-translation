"""Download a bounded subset of public ASR data and export it as WAV + JSONL manifests.

Groups (plan 5.5, 9.1b, 9.1c):
  test   fixed public test sets: FLEURS vi_vn test, FLEURS en_us test,
         LibriSpeech test-clean, AMI ihm test (1 shard)            ~1.7 GB parquet
  pilot  small train set for the D1/D2 pilot run: FLEURS vi_vn train+validation,
         LibriSpeech train.100 (1 shard), AMI ihm train (2 shards)  ~3.4 GB parquet

Licenses: everything here is CC-BY 4.0. Non-commercial sets (Bud500, VIVOS:
CC BY-NC-SA 4.0) are deliberately excluded -- decided 2026-10-01, do not add them.

Parquet is read directly with pyarrow and audio bytes decoded with soundfile, so
this does not depend on `datasets` audio decoding. Re-running skips files that
already exist, so an interrupted download can simply be started again.

Usage:
    python scripts/00_download_public.py --groups test pilot
Output:
    data/public/<name>/<id>.wav, data/manifests/public_<group>_<name>.jsonl
"""

import argparse
import io
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf
from huggingface_hub import hf_hub_download

SR = 16000
MAX_SECONDS = 30.0
# Guideline (plan 6) drops filler words; AMI transcribes them.
EN_FILLERS = {"uh", "um", "hmm", "mm", "mm-hmm", "uh-huh", "mhm", "ah", "er", "erm"}


@dataclass(frozen=True)
class Source:
    name: str          # output folder / manifest name, also the manifest `source`
    repo: str
    files: tuple[str, ...]
    lang: str          # vi | en
    text_col: str
    speaker_col: str | None
    license: str


GROUPS: dict[str, list[Source]] = {
    "test": [
        Source("fleurs_vi_test", "google/fleurs", ("parquet-data/vi_vn/test-00000-of-00001.parquet",),
               "vi", "raw_transcription", None, "CC-BY-4.0"),
        Source("fleurs_en_test", "google/fleurs", ("parquet-data/en_us/test-00000-of-00001.parquet",),
               "en", "raw_transcription", None, "CC-BY-4.0"),
        Source("librispeech_test_clean", "openslr/librispeech_asr", ("clean/test/0000.parquet",),
               "en", "text", "speaker_id", "CC-BY-4.0"),
        Source("ami_ihm_test", "edinburghcstr/ami", ("ihm/test-00000-of-00004.parquet",),
               "en", "text", "speaker_id", "CC-BY-4.0"),
    ],
    "pilot": [
        Source("fleurs_vi_train", "google/fleurs",
               ("parquet-data/vi_vn/train-00000-of-00001.parquet",
                "parquet-data/vi_vn/validation-00000-of-00001.parquet"),
               "vi", "raw_transcription", None, "CC-BY-4.0"),
        Source("librispeech_train", "openslr/librispeech_asr",
               ("clean/train.100/0000.parquet",),
               "en", "text", "speaker_id", "CC-BY-4.0"),
        Source("ami_ihm_train", "edinburghcstr/ami",
               ("ihm/train-00000-of-00042.parquet", "ihm/train-00001-of-00042.parquet"),
               "en", "text", "speaker_id", "CC-BY-4.0"),
    ],
}


def clean_text(text: str, lang: str) -> str:
    text = unicodedata.normalize("NFC", text).strip()
    # LibriSpeech / AMI ship ALL-CAPS text; Whisper writes normal case.
    if lang == "en" and text.isupper():
        text = text.lower()
    words = text.split()
    if lang == "en":
        words = [w for w in words if w.lower().strip(".,?!") not in EN_FILLERS]
    return " ".join(words)


def decode(audio: dict) -> np.ndarray:
    data, sr = sf.read(io.BytesIO(audio["bytes"]), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if sr != SR:
        import librosa

        mono = librosa.resample(mono, orig_sr=sr, target_sr=SR)
    return mono.astype(np.float32)


def export(source: Source, group: str, root: Path) -> tuple[int, float]:
    out_dir = root / "data" / "public" / source.name
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = root / "data" / "manifests" / f"public_{group}_{source.name}.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)

    count, seconds, skipped = 0, 0.0, 0
    with manifest.open("w", encoding="utf-8") as out:
        for filename in source.files:
            print(f"  ↓ {source.repo}/{filename}", flush=True)
            local = hf_hub_download(source.repo, filename, repo_type="dataset")
            columns = ["audio", source.text_col] + ([source.speaker_col] if source.speaker_col else [])
            parquet = pq.ParquetFile(local)
            shard = Path(filename).stem.split("-")[0]
            for batch in parquet.iter_batches(batch_size=256, columns=columns):
                for row in batch.to_pylist():
                    text = clean_text(row[source.text_col] or "", source.lang)
                    if not text:
                        skipped += 1
                        continue
                    clip_id = f"{source.name}_{shard}_{count + skipped:07d}"
                    wav = out_dir / f"{clip_id}.wav"
                    if wav.exists():
                        duration = sf.info(str(wav)).duration
                    else:
                        audio = decode(row["audio"])
                        duration = len(audio) / SR
                        if duration > MAX_SECONDS or duration < 0.3:
                            skipped += 1
                            continue
                        sf.write(str(wav), audio, SR, subtype="PCM_16")
                    record = {
                        "id": clip_id,
                        "kind": "clip",
                        "audio": str(wav.relative_to(root)),
                        "text": text,
                        "lang": source.lang,
                        "category": source.lang,
                        "duration": round(duration, 3),
                        "speaker": str(row[source.speaker_col]) if source.speaker_col else None,
                        "source": source.name,
                        "split": group,
                        "label_type": "human",
                        "license": source.license,
                    }
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    count += 1
                    seconds += duration
    print(f"  ✓ {source.name}: {count} clips, {seconds / 3600:.2f} h (skipped {skipped}) -> {manifest}")
    return count, seconds


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", nargs="+", default=["test"], choices=sorted(GROUPS))
    parser.add_argument("--root", default=".", help="mezon-whisper project root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    summary = []
    for group in args.groups:
        sources = GROUPS[group]
        print(f"\n== group {group} ==")
        for source in sources:
            count, seconds = export(source, group, root)
            summary.append((group, source.name, source.lang, count, seconds, source.license))

    print("\n== summary ==")
    for group, name, lang, count, seconds, lic in summary:
        print(f"{group:6s} {name:24s} {lang}  {count:7d} clips  {seconds / 3600:6.2f} h  {lic}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
