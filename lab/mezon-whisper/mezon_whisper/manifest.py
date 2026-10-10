"""JSONL manifests (plan 5.2) and the paths every script shares."""

import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "data" / "manifests"
SAMPLE_RATE = 16000


def read_jsonl(path: str | Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def write_jsonl(path: str | Path, records: list[dict], append: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def load_audio(relative_path: str) -> np.ndarray:
    """16 kHz mono float32 audio of a manifest `audio` path (relative to the project root)."""
    audio, sample_rate = sf.read(str(ROOT / relative_path), dtype="float32", always_2d=True)
    assert sample_rate == SAMPLE_RATE, f"{relative_path}: expected 16 kHz, got {sample_rate}"
    return audio.mean(axis=1)


def save_audio(relative_path: str, audio: np.ndarray) -> None:
    target = ROOT / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(target), audio, SAMPLE_RATE, subtype="PCM_16")
