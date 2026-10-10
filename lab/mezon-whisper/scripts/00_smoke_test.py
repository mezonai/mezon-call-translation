"""Day-1 smoke tests for the GPU box (plan section 9.1), no dataset needed.

  test 2: a short LoRA train on the HF turbo checkpoint (encoder + decoder,
          bf16 autocast, gradient checkpointing), loss must drop.
  test 3: merge LoRA -> save HF -> ct2-transformers-converter (float16) ->
          reload with faster-whisper, check 128 mel, decode through
          BatchedInferencePipeline with our own spans.
  extra : span->segment mapping when a span is pure silence (plan 4.2).

Toy task (synthetic audio only): white noise -> "" (non-speech sample),
sine beeps -> "tiếng bíp". It only proves the mechanics work end to end;
the numbers say nothing about model quality.

Usage (inside the venv, LD_LIBRARY_PATH set for cuBLAS/cuDNN):
    python scripts/00_smoke_test.py [--out models/smoke] [--steps 40]
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch

BASE_MODEL = "openai/whisper-large-v3-turbo"
SR = 16000
BEEP_TEXT = "tiếng bíp"
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "out_proj", "fc1", "fc2"]


def noise_clip(rng: np.random.Generator) -> np.ndarray:
    seconds = rng.uniform(3, 20)
    return (rng.standard_normal(int(seconds * SR)) * rng.uniform(0.003, 0.03)).astype(np.float32)


def beep_clip(rng: np.random.Generator) -> np.ndarray:
    seconds = rng.uniform(3, 20)
    t = np.arange(int(seconds * SR)) / SR
    gate = (np.sin(2 * np.pi * 2 * t) > 0).astype(np.float32)  # 2 beeps per second
    tone = np.sin(2 * np.pi * rng.uniform(600, 1200) * t) * 0.3 * gate
    return (tone + rng.standard_normal(len(t)) * 0.005).astype(np.float32)


def make_samples(rng: np.random.Generator, n: int) -> list[tuple[np.ndarray, str]]:
    return [(noise_clip(rng), "") if i % 2 == 0 else (beep_clip(rng), BEEP_TEXT) for i in range(n)]


def encode_labels(tokenizer, text: str) -> list[int]:
    tokenizer.set_prefix_tokens(language="vi", task="transcribe", predict_timestamps=False)
    ids = tokenizer(text).input_ids
    # The model prepends decoder_start_token_id (SOT) itself when shifting labels.
    sot = tokenizer.convert_tokens_to_ids("<|startoftranscript|>")
    return ids[1:] if ids and ids[0] == sot else ids


def collate(processor, batch: list[tuple[np.ndarray, str]], device: str) -> dict[str, torch.Tensor]:
    feats = processor.feature_extractor(
        [audio for audio, _ in batch], sampling_rate=SR, return_tensors="pt"
    ).input_features
    labels = [encode_labels(processor.tokenizer, text) for _, text in batch]
    width = max(len(ids) for ids in labels)
    padded = torch.full((len(labels), width), -100, dtype=torch.long)
    for row, ids in enumerate(labels):
        padded[row, : len(ids)] = torch.tensor(ids)
    return {"input_features": feats.to(device), "labels": padded.to(device)}


@torch.no_grad()
def hf_generate(model, processor, audios: list[np.ndarray], device: str) -> list[str]:
    feats = processor.feature_extractor(audios, sampling_rate=SR, return_tensors="pt").input_features
    with torch.autocast("cuda", dtype=torch.bfloat16):
        out = model.generate(input_features=feats.to(device), language="vi", task="transcribe", max_new_tokens=40)
    return [t.strip() for t in processor.tokenizer.batch_decode(out, skip_special_tokens=True)]


def test_train(args, device: str):
    from peft import LoraConfig, get_peft_model
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    print("\n== test 2: LoRA train step ==")
    processor = WhisperProcessor.from_pretrained(BASE_MODEL)
    model = WhisperForConditionalGeneration.from_pretrained(BASE_MODEL, dtype=torch.float32).to(device)
    print("num_mel_bins", model.config.num_mel_bins, "| decoder layers", model.config.decoder_layers)

    rng = np.random.default_rng(0)
    probe = [noise_clip(rng), beep_clip(rng)]
    model.eval()
    print("before train  noise ->", repr(hf_generate(model, processor, probe[:1], device)[0]))
    print("before train  beep  ->", repr(hf_generate(model, processor, probe[1:], device)[0]))

    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.model.encoder.conv1.register_forward_hook(lambda m, i, o: o.requires_grad_(True))
    lora = LoraConfig(r=32, lora_alpha=64, lora_dropout=0.05, bias="none", target_modules=TARGET_MODULES)
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr)
    samples = make_samples(rng, 64)
    model.train()
    losses, torch_start = [], time.time()
    torch.cuda.reset_peak_memory_stats()
    for step in range(args.steps):
        batch_idx = rng.choice(len(samples), size=args.batch, replace=False)
        batch = collate(processor, [samples[i] for i in batch_idx], device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            loss = model(**batch).loss
        loss.backward()
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        losses.append(loss.item())
        if step % 10 == 0 or step == args.steps - 1:
            print(f"  step {step:3d} loss {loss.item():.4f}")
    torch.cuda.synchronize()
    elapsed = time.time() - torch_start
    print(f"{args.steps} steps x batch {args.batch} in {elapsed:.1f}s ({elapsed / args.steps:.2f}s/step, "
          f"includes feature extraction on the main process)")
    print(f"peak VRAM {torch.cuda.max_memory_allocated() / 1e9:.1f} GB")

    first, last = np.mean(losses[:3]), np.mean(losses[-3:])
    assert last < first * 0.5, f"loss did not drop enough: {first:.3f} -> {last:.3f}"
    print(f"loss {first:.3f} -> {last:.3f}  PASS")

    model.eval()
    model.config.use_cache = True
    print("after train   noise ->", repr(hf_generate(model, processor, probe[:1], device)[0]))
    print("after train   beep  ->", repr(hf_generate(model, processor, probe[1:], device)[0]))
    return model, processor, probe


def test_convert(args, model, processor, probe) -> None:
    from faster_whisper import BatchedInferencePipeline, WhisperModel
    from huggingface_hub import hf_hub_download

    print("\n== test 3: merge -> HF -> CTranslate2 -> faster-whisper ==")
    hf_dir, ct2_dir = Path(args.out) / "hf", Path(args.out) / "ct2"
    shutil.rmtree(hf_dir, ignore_errors=True)
    shutil.rmtree(ct2_dir, ignore_errors=True)

    merged = model.merge_and_unload()
    merged.to(torch.float16).save_pretrained(hf_dir)
    processor.save_pretrained(hf_dir)
    # Tokenizer and mel config are unchanged by finetuning: take them from the base repo so
    # the converter always finds tokenizer.json and the 128-mel preprocessor_config.json.
    for name in ("tokenizer.json", "preprocessor_config.json"):
        shutil.copy(hf_hub_download(BASE_MODEL, name), hf_dir / name)
    del merged, model
    torch.cuda.empty_cache()

    cmd = [
        "ct2-transformers-converter", "--model", str(hf_dir), "--output_dir", str(ct2_dir),
        "--copy_files", "tokenizer.json", "preprocessor_config.json", "--quantization", "float16",
    ]
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)

    fw = WhisperModel(str(ct2_dir), device="cuda", compute_type="float16")
    n_mels = fw.model.n_mels
    feature_size = fw.feat_kwargs.get("feature_size")
    assert n_mels == 128 and feature_size == 128, f"mel mismatch: model {n_mels}, extractor {feature_size}"
    print(f"n_mels model={n_mels} extractor={feature_size}  PASS")

    # Our own spans over one long recording: noise, silence, beep (plan 4.2 mapping check).
    silence = np.zeros(4 * SR, dtype=np.float32)
    gap = np.zeros(SR, dtype=np.float32)
    audio = np.concatenate([probe[0][: 5 * SR], gap, silence, gap, probe[1][: 5 * SR]])
    n0 = len(probe[0][: 5 * SR]) / SR
    spans = [
        {"start": 0.0, "end": n0},
        {"start": n0 + 1.0, "end": n0 + 5.0},
        {"start": n0 + 6.0, "end": len(audio) / SR},
    ]
    names = ["noise", "silence", "beep"]
    pipe = BatchedInferencePipeline(fw)
    segs, _ = pipe.transcribe(audio, clip_timestamps=spans, language="vi", without_timestamps=True, batch_size=16)
    segs = list(segs)
    # Mirror faster-whisper: start -> int samples -> seconds -> int(offset * frames_per_second).
    by_seek = {int(int(s["start"] * SR) / SR * fw.frames_per_second): name for s, name in zip(spans, names)}
    print(f"{len(segs)} segments for {len(spans)} spans")
    for s in segs:
        print(f"  seek={s.seek:5d} span={by_seek.get(s.seek, '??'):7s} start={s.start} end={s.end} text={s.text!r}")
    missing = [name for seek, name in by_seek.items() if seek not in {s.seek for s in segs}]
    if missing:
        print(f"NOTE: no segment for spans {missing} -> mapping must treat a missing seek as empty text")
    print("convert + reload + batched decode  PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="models/smoke")
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--lr", type=float, default=1e-3)  # high on purpose: toy task must converge fast
    args = parser.parse_args()

    assert torch.cuda.is_available(), "CUDA not available"
    model, processor, probe = test_train(args, "cuda")
    test_convert(args, model, processor, probe)
    print("\nALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
