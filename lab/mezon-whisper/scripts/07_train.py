"""LoRA finetune of whisper-large-v3-turbo from JSONL manifests (plan 9.3, 9.4).

Reads clip manifests (plan 5.2), trains LoRA on encoder + decoder with bf16
autocast and gradient checkpointing, evaluates WER on a dev set every
--eval-steps, and keeps the adapter with the best dev WER in <out>/best.
Audio is read lazily from disk in dataloader workers (the box has 30GB RAM).

Language token per sample (plan 3.5): category `en` -> <|en|>, everything else
(vi, mixed, nonspeech) -> <|vi|>. Empty text gives labels of prefix + EOT.

Usage (pilot, plan 9.1b):
    python scripts/07_train.py --out models/runs/pilot --epochs 1 --dev-holdout 300 \
        --train data/manifests/public_pilot_fleurs_vi_train.jsonl \
                data/manifests/public_pilot_librispeech_train.jsonl \
                data/manifests/public_pilot_ami_ihm_train.jsonl
A manifest can be oversampled with a suffix: `path.jsonl:x3`.
"""

import argparse
import csv
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from torch.utils.data import DataLoader, Dataset

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")  # dataloader workers fork

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mezon_whisper.normalize import normalize  # noqa: E402

BASE_MODEL = "openai/whisper-large-v3-turbo"
SR = 16000
MAX_SAMPLES = 30 * SR
MAX_LABEL_TOKENS = 448
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "out_proj", "fc1", "fc2"]


def token_language(record: dict) -> str:
    return "en" if record.get("category", record.get("lang")) == "en" else "vi"


def read_manifests(specs: list[str]) -> list[dict]:
    """Each spec is `path` or `path:xN`; returns unique records tagged with `_repeat`."""
    records = []
    for spec in specs:
        path, _, repeat = spec.partition(":x")
        for line in Path(path).open(encoding="utf-8"):
            record = json.loads(line)
            record["_repeat"] = int(repeat or 1)
            records.append(record)
    return records


class ClipDataset(Dataset):
    def __init__(self, records: list[dict]):
        self.records = records
        self._processor = None  # created per worker process

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict:
        if self._processor is None:
            from transformers import WhisperProcessor

            self._processor = WhisperProcessor.from_pretrained(BASE_MODEL)
        record = self.records[index]
        audio, sr = sf.read(str(ROOT / record["audio"]), dtype="float32", always_2d=True)
        assert sr == SR, f"{record['audio']}: sample rate {sr}"
        audio = audio.mean(axis=1)[:MAX_SAMPLES]
        features = self._processor.feature_extractor(audio, sampling_rate=SR, return_tensors="np").input_features[0]

        tokenizer = self._processor.tokenizer
        tokenizer.set_prefix_tokens(language=token_language(record), task="transcribe", predict_timestamps=False)
        ids = tokenizer(record["text"]).input_ids
        # The model prepends decoder_start_token_id (SOT) itself when shifting labels right.
        if ids and ids[0] == tokenizer.convert_tokens_to_ids("<|startoftranscript|>"):
            ids = ids[1:]
        if len(ids) > MAX_LABEL_TOKENS:
            ids = ids[: MAX_LABEL_TOKENS - 1] + [tokenizer.eos_token_id]
        return {"input_features": torch.from_numpy(features), "labels": ids, "index": index}


def collate(batch: list[dict]) -> dict:
    width = max(len(item["labels"]) for item in batch)
    labels = torch.full((len(batch), width), -100, dtype=torch.long)
    for row, item in enumerate(batch):
        labels[row, : len(item["labels"])] = torch.tensor(item["labels"])
    return {
        "input_features": torch.stack([item["input_features"] for item in batch]),
        "labels": labels,
        "index": [item["index"] for item in batch],
    }


@torch.no_grad()
def evaluate(model, tokenizer, loader: DataLoader, records: list[dict]) -> dict:
    """Corpus WER on clips with text, hallucination rate on clips with empty text."""
    import jiwer

    model.eval()
    model.config.use_cache = True
    refs: dict[str, list[str]] = {}
    hyps: dict[str, list[str]] = {}
    nonspeech_total = nonspeech_hallucinated = 0
    for batch in loader:
        batch_records = [records[i] for i in batch["index"]]
        features = batch["input_features"].cuda(non_blocking=True)
        for language in sorted({token_language(r) for r in batch_records}):
            rows = [i for i, r in enumerate(batch_records) if token_language(r) == language]
            with torch.autocast("cuda", dtype=torch.bfloat16):
                out = model.generate(input_features=features[rows], language=language, task="transcribe",
                                     max_new_tokens=200)
            texts = tokenizer.batch_decode(out, skip_special_tokens=True)
            for row, text in zip(rows, texts):
                reference, hypothesis = normalize(batch_records[row]["text"]), normalize(text)
                if not reference:
                    nonspeech_total += 1
                    nonspeech_hallucinated += bool(hypothesis)
                    continue
                group = batch_records[row].get("category", language)
                refs.setdefault(group, []).append(reference)
                hyps.setdefault(group, []).append(hypothesis)
    model.config.use_cache = False
    model.train()

    result = {f"wer_{g}": jiwer.wer(refs[g], hyps[g]) * 100 for g in sorted(refs)}
    all_refs = [r for g in sorted(refs) for r in refs[g]]
    all_hyps = [h for g in sorted(refs) for h in hyps[g]]
    result["wer"] = jiwer.wer(all_refs, all_hyps) * 100 if all_refs else float("nan")
    if nonspeech_total:
        result["hallucination_rate"] = nonspeech_hallucinated / nonspeech_total * 100
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", nargs="+", required=True, help="manifest paths, optional :xN oversample suffix")
    parser.add_argument("--dev", nargs="*", default=[], help="dev manifests; if empty, --dev-holdout is used")
    parser.add_argument("--dev-holdout", type=int, default=300, help="clips taken out of train as dev")
    parser.add_argument("--dev-max", type=int, default=500, help="cap on dev clips used during training")
    parser.add_argument("--out", required=True)
    parser.add_argument("--resume", help="adapter directory to continue from (weights only)")
    parser.add_argument("--epochs", type=float, default=3)
    parser.add_argument("--max-steps", type=int, default=0, help="optimizer steps; overrides --epochs")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--grad-accum", type=int, default=2)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--warmup-steps", type=int, default=300)
    parser.add_argument("--eval-steps", type=int, default=500)
    parser.add_argument("--log-steps", type=int, default=25)
    parser.add_argument("--lora-r", type=int, default=32)
    parser.add_argument("--lora-alpha", type=int, default=64)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--mask-time-prob", type=float, default=0.05)
    parser.add_argument("--num-workers", type=int, default=6)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--no-eval-at-start", action="store_true")
    args = parser.parse_args()

    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import WhisperForConditionalGeneration, WhisperProcessor, get_linear_schedule_with_warmup

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "args.json").write_text(json.dumps(vars(args), indent=2), encoding="utf-8")

    # ---- data -------------------------------------------------------------
    unique = read_manifests(args.train)
    if args.dev:
        dev_records = read_manifests(args.dev)
    else:
        random.Random(args.seed).shuffle(unique)
        dev_records, unique = unique[: args.dev_holdout], unique[args.dev_holdout:]
    dev_records = dev_records[: args.dev_max]
    dev_ids = {r["id"] for r in dev_records}
    assert not dev_ids & {r["id"] for r in unique}, "dev clips leaked into train"
    train_records = [r for r in unique for _ in range(r["_repeat"])]
    train_hours = sum(r["duration"] for r in train_records) / 3600
    print(f"train: {len(train_records)} clips, {train_hours:.2f} h (with oversampling) | dev: {len(dev_records)} clips")
    for language in ("vi", "en"):
        hours = sum(r["duration"] for r in train_records if token_language(r) == language) / 3600
        print(f"  token <|{language}|>: {hours:.2f} h")

    loader_args = dict(num_workers=args.num_workers, collate_fn=collate, pin_memory=True,
                       persistent_workers=args.num_workers > 0)
    train_loader = DataLoader(ClipDataset(train_records), batch_size=args.batch_size, shuffle=True,
                              drop_last=True, **loader_args)
    dev_loader = DataLoader(ClipDataset(dev_records), batch_size=args.batch_size, shuffle=False, **loader_args)

    steps_per_epoch = len(train_loader) // args.grad_accum
    total_steps = args.max_steps or math.ceil(steps_per_epoch * args.epochs)
    print(f"{steps_per_epoch} optimizer steps/epoch, {total_steps} total, "
          f"effective batch {args.batch_size * args.grad_accum}")

    # ---- model ------------------------------------------------------------
    processor = WhisperProcessor.from_pretrained(BASE_MODEL)
    model = WhisperForConditionalGeneration.from_pretrained(BASE_MODEL, dtype=torch.float32).cuda()
    model.config.apply_spec_augment = args.mask_time_prob > 0
    model.config.mask_time_prob = args.mask_time_prob
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.model.encoder.conv1.register_forward_hook(lambda m, i, o: o.requires_grad_(True))
    if args.resume:
        model = PeftModel.from_pretrained(model, args.resume, is_trainable=True)
    else:
        model = get_peft_model(model, LoraConfig(r=args.lora_r, lora_alpha=args.lora_alpha,
                                                 lora_dropout=args.lora_dropout, bias="none",
                                                 target_modules=TARGET_MODULES))
    model.print_trainable_parameters()

    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=0.0)
    scheduler = get_linear_schedule_with_warmup(optimizer, min(args.warmup_steps, total_steps // 10 or 1), total_steps)

    try:
        from torch.utils.tensorboard import SummaryWriter

        board = SummaryWriter(str(out / "tb"))
    except ImportError:
        board = None
        print("tensorboard not installed: logging to log.csv only")
    log_file = (out / "log.csv").open("a", newline="", encoding="utf-8")
    log = csv.writer(log_file)
    log.writerow(["step", "kind", "key", "value"])

    def record(step: int, kind: str, values: dict) -> None:
        for key, value in values.items():
            log.writerow([step, kind, key, f"{value:.6g}"])
            if board:
                board.add_scalar(f"{kind}/{key}", value, step)
        log_file.flush()

    best_wer, history = float("inf"), []

    def run_eval(step: int) -> None:
        nonlocal best_wer
        started = time.time()
        metrics = evaluate(model, processor.tokenizer, dev_loader, dev_records)
        record(step, "dev", metrics)
        history.append({"step": step, **metrics})
        summary = "  ".join(f"{k} {v:.2f}" for k, v in metrics.items())
        print(f"[eval step {step}] {summary}  ({time.time() - started:.0f}s)", flush=True)
        model.save_pretrained(str(out / f"checkpoint-{step}"))
        if metrics["wer"] < best_wer:
            best_wer = metrics["wer"]
            model.save_pretrained(str(out / "best"))
            print(f"  new best dev WER {best_wer:.2f} -> {out / 'best'}")

    if not args.no_eval_at_start:
        run_eval(0)

    # ---- train ------------------------------------------------------------
    model.train()
    torch.cuda.reset_peak_memory_stats()
    step, running, clips_seen, audio_seen = 0, [], 0, 0.0
    train_started = window_started = time.time()
    optimizer.zero_grad(set_to_none=True)
    while step < total_steps:
        for batch_index, batch in enumerate(train_loader):
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss = model(input_features=batch["input_features"].cuda(non_blocking=True),
                             labels=batch["labels"].cuda(non_blocking=True)).loss
            (loss / args.grad_accum).backward()
            running.append(loss.item())
            clips_seen += len(batch["index"])
            audio_seen += sum(train_records[i]["duration"] for i in batch["index"])
            if (batch_index + 1) % args.grad_accum:
                continue
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            step += 1

            if step % args.log_steps == 0:
                elapsed = time.time() - window_started
                values = {"loss": float(np.mean(running)), "lr": scheduler.get_last_lr()[0],
                          "clips_per_s": args.log_steps * args.grad_accum * args.batch_size / elapsed,
                          "vram_gb": torch.cuda.max_memory_allocated() / 1e9}
                record(step, "train", values)
                print(f"step {step}/{total_steps}  loss {values['loss']:.4f}  lr {values['lr']:.2e}  "
                      f"{values['clips_per_s']:.1f} clips/s  vram {values['vram_gb']:.1f}GB", flush=True)
                running, window_started = [], time.time()
            if step % args.eval_steps == 0 and step < total_steps:
                run_eval(step)
                window_started = time.time()
            if step >= total_steps:
                break
    train_seconds = time.time() - train_started

    run_eval(step)
    model.save_pretrained(str(out / "final"))
    summary = {
        "steps": step,
        "train_clips": len(train_records),
        "train_hours": train_hours,
        "wall_hours_including_eval": train_seconds / 3600,
        "clips_per_second": clips_seen / train_seconds,
        "audio_hours_per_wall_hour": (audio_seen / 3600) / (train_seconds / 3600),
        "peak_vram_gb": torch.cuda.max_memory_allocated() / 1e9,
        "best_dev_wer": best_wer,
        "dev_history": history,
    }
    (out / "train_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "dev_history"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
