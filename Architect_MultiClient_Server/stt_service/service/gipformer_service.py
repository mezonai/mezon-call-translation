"""CPU-only Gipformer fallback service for non-realtime Whisper STT."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import numpy as np


SAMPLE_RATE = 16_000
GIPFORMER_REPO_ID = "g-group-ai-lab/gipformer-65M-rnnt"
GIPFORMER_USE_INT8 = True
GIPFORMER_DECODING_METHOD = "modified_beam_search"
GIPFORMER_MAX_ACTIVE_PATHS = 5


class GipformerService:
    """Owns Gipformer model download/cache, inference, and health state."""

    def __init__(self, *, cpu_threads: int) -> None:
        if cpu_threads < 1:
            raise ValueError("cpu_threads must be positive")
        self._cpu_threads = cpu_threads
        self._recognizer: Any | None = None
        self._model_files: dict[str, Path] = {}

    @staticmethod
    def _required_filenames() -> dict[str, str]:
        suffix = ".int8.onnx" if GIPFORMER_USE_INT8 else ".onnx"
        return {
            "encoder": f"encoder{suffix}",
            "decoder": f"decoder{suffix}",
            "joiner": f"joiner{suffix}",
            "tokens": "tokens.txt",
        }

    def _resolve_model_files(self, *, local_files_only: bool) -> dict[str, Path]:
        from huggingface_hub import hf_hub_download

        return {
            name: Path(
                hf_hub_download(
                    repo_id=GIPFORMER_REPO_ID,
                    filename=filename,
                    local_files_only=local_files_only,
                )
            )
            for name, filename in self._required_filenames().items()
        }

    def download_model(self) -> dict[str, Path]:
        """Download/cache all required files without constructing the recognizer."""
        self._model_files = self._resolve_model_files(local_files_only=False)
        return self._model_files.copy()

    def initialize(self) -> None:
        """Load the cached model, downloading it once when the cache is empty."""
        if self._recognizer is not None:
            return
        try:
            import sherpa_onnx
        except ImportError as error:
            raise RuntimeError(
                "Gipformer fallback requires sherpa-onnx; install the STT requirements."
            ) from error

        files = self.download_model()
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(files["encoder"]),
            decoder=str(files["decoder"]),
            joiner=str(files["joiner"]),
            tokens=str(files["tokens"]),
            provider="cpu",
            num_threads=self._cpu_threads,
            sample_rate=SAMPLE_RATE,
            decoding_method=GIPFORMER_DECODING_METHOD,
            max_active_paths=GIPFORMER_MAX_ACTIVE_PATHS,
        )

    def transcribe(self, audio: np.ndarray) -> str:
        if self._recognizer is None:
            raise RuntimeError("GipformerService is not initialized")
        waveform = np.ascontiguousarray(audio, dtype=np.float32)
        if waveform.ndim != 1:
            raise ValueError(f"Expected mono waveform, got shape {waveform.shape}")
        stream = self._recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, waveform)
        self._recognizer.decode_stream(stream)
        return str(stream.result.text)

    def health_status(self) -> dict[str, Any]:
        """Return a non-networking health check suitable for /health."""
        if importlib.util.find_spec("sherpa_onnx") is None:
            return {
                "status": "unhealthy",
                "initialized": False,
                "error": "sherpa-onnx is not installed",
            }
        if self._recognizer is not None:
            return {
                "status": "healthy",
                "initialized": True,
                "repository": GIPFORMER_REPO_ID,
                "model_files": {name: str(path) for name, path in self._model_files.items()},
            }
        try:
            files = self._resolve_model_files(local_files_only=True)
        except Exception as error:
            return {
                "status": "unhealthy",
                "initialized": False,
                "repository": GIPFORMER_REPO_ID,
                "error": f"Gipformer model is not cached: {error}",
            }
        return {
            # The runtime dependency and all model files are ready. The
            # production process constructs its only recognizer at startup.
            "status": "healthy",
            "initialized": False,
            "repository": GIPFORMER_REPO_ID,
            "model_files": {name: str(path) for name, path in files.items()},
        }

    def shutdown(self) -> None:
        self._recognizer = None
        self._model_files = {}
