#!/usr/bin/env bash
# Merge a LoRA adapter into whisper-large-v3-turbo and convert to CTranslate2 (plan 10).
# Usage: bash scripts/08_convert.sh models/runs/pilot/best models/pilot
#   -> models/pilot-hf (merged HF, float16) and models/pilot-ct2 (faster-whisper, float16)
set -euo pipefail

ADAPTER=${1:?adapter directory, e.g. models/runs/pilot/best}
PREFIX=${2:?output prefix, e.g. models/pilot}
QUANT=${3:-float16}
HF_DIR="${PREFIX}-hf"
CT2_DIR="${PREFIX}-ct2"

rm -rf "$HF_DIR" "$CT2_DIR"

python - "$ADAPTER" "$HF_DIR" <<'PY'
import shutil, sys
import torch
from huggingface_hub import hf_hub_download
from peft import PeftModel
from transformers import WhisperForConditionalGeneration, WhisperProcessor

BASE = "openai/whisper-large-v3-turbo"
adapter, hf_dir = sys.argv[1], sys.argv[2]
model = WhisperForConditionalGeneration.from_pretrained(BASE, dtype=torch.float32)
merged = PeftModel.from_pretrained(model, adapter).merge_and_unload()
merged.to(torch.float16).save_pretrained(hf_dir)
WhisperProcessor.from_pretrained(BASE).save_pretrained(hf_dir)
# Tokenizer and mel config are untouched by finetuning: take them from the base repo so the
# converter always finds tokenizer.json and the 128-mel preprocessor_config.json.
for name in ("tokenizer.json", "preprocessor_config.json"):
    shutil.copy(hf_hub_download(BASE, name), f"{hf_dir}/{name}")
print("merged ->", hf_dir)
PY

ct2-transformers-converter --model "$HF_DIR" --output_dir "$CT2_DIR" \
  --copy_files tokenizer.json preprocessor_config.json --quantization "$QUANT"

python - "$CT2_DIR" "$QUANT" <<'PY'
import sys
from faster_whisper import WhisperModel

model = WhisperModel(sys.argv[1], device="cuda", compute_type=sys.argv[2])
assert model.model.n_mels == 128 and model.feat_kwargs.get("feature_size") == 128, "mel config lost in conversion"
print("converted ->", sys.argv[1], "| n_mels 128 OK")
PY
