"""Speech span detection for the span pipeline (plan 4.3).

Silero VAD is called on its own, before and independently of Whisper decoding;
Whisper's `vad_filter` is never used (plan 3.7). Starting values are the
production profile from stt_service `whisper_marker_transcriber.py`, except
max_speech_s: every span is one Whisper input, so it must stay under 30s.
"""

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000


@dataclass(frozen=True)
class VadConfig:
    threshold: float = 0.5
    neg_threshold: float = 0.35
    min_speech_ms: int = 250
    min_silence_ms: int = 1000
    speech_pad_ms: int = 250
    max_speech_s: float = 25.0


@dataclass(frozen=True)
class Span:
    """Speech region in samples of the source recording."""

    start: int
    end: int

    @property
    def start_s(self) -> float:
        return self.start / SAMPLE_RATE

    @property
    def end_s(self) -> float:
        return self.end / SAMPLE_RATE


def detect_spans(audio: np.ndarray, config: VadConfig = VadConfig()) -> list[Span]:
    """Non-overlapping speech spans of a 16 kHz mono float32 recording, in time order."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(
        threshold=config.threshold,
        neg_threshold=config.neg_threshold,
        min_speech_duration_ms=config.min_speech_ms,
        max_speech_duration_s=config.max_speech_s,
        min_silence_duration_ms=config.min_silence_ms,
        speech_pad_ms=config.speech_pad_ms,
    )
    timestamps = get_speech_timestamps(audio, vad_options=options, sampling_rate=SAMPLE_RATE)
    spans = sorted(
        (Span(max(0, int(item["start"])), min(len(audio), int(item["end"]))) for item in timestamps),
        key=lambda span: span.start,
    )
    spans = [span for span in spans if span.end > span.start]
    if any(current.start < previous.end for previous, current in zip(spans, spans[1:])):
        raise RuntimeError("VAD returned overlapping speech spans")
    return spans
