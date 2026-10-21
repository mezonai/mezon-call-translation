"""Vosk streaming recognition state for one client."""

import json
import logging
from pathlib import Path
from typing import Tuple


logger = logging.getLogger(__name__)

VOSK_STOP_WORDS = {
    "the",
    "a",
    "an",
    "uh",
    "um",
    "ah",
    "eh",
    "huh",
}


class VoskModel:
    """Load the heavyweight Vosk model once and create client recognizers."""

    def __init__(self, model_path: str):
        try:
            from vosk import Model
        except ImportError as exc:
            raise RuntimeError(
                "MODEL=VOSK requires the 'vosk' package. "
                "Install realtime_stt_service/requirements-server.txt."
            ) from exc

        self.model_path = Path(model_path)
        self.runtime_model = Model(str(self.model_path))

    def create_stream(self, sample_rate: int) -> "VoskStream":
        return VoskStream(self, sample_rate)


class VoskStream:
    """A dedicated KaldiRecognizer and endpoint state for one client."""

    def __init__(self, model: VoskModel, sample_rate: int):
        from vosk import KaldiRecognizer

        self.recognizer = KaldiRecognizer(model.runtime_model, sample_rate)

    def process(self, pcm16_audio: bytes) -> Tuple[str, bool]:
        """Return the old Vosk partial/final result for a PCM16 audio batch."""
        is_final = bool(self.recognizer.AcceptWaveform(pcm16_audio))

        if is_final:
            result = json.loads(self.recognizer.Result())
            return result.get("text", "").strip(), True

        partial = json.loads(self.recognizer.PartialResult())
        return partial.get("partial", "").strip(), False

    def flush(self) -> str:
        """Finalize pending Vosk audio when the client stream is closed."""
        result = json.loads(self.recognizer.FinalResult())
        return result.get("text", "").strip()
