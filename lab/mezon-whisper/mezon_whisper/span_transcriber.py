"""Span pipeline: one VAD span = one Whisper input = one transcript segment (plan 4).

    recording -> detect_spans (our VAD) -> batched decode, one span per batch item
              -> map each decoded segment back to its span -> per-span filters

Segment times are always the VAD span's own start/end; nothing Whisper reports
about time is used. Decoded segments are matched to spans through `seek` (the
clip offset in frames that faster-whisper stamps on every segment), never
through `segment.start/end` or output order.

CLI, to eyeball one recording:
    python -m mezon_whisper.span_transcriber data/raw/<track>.wav [--model large-v3-turbo]
"""

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mezon_whisper.vad import SAMPLE_RATE, Span, VadConfig, detect_spans

MAX_SPAN_S = 30.0
MAX_REPEATED_NGRAM = 4   # look for repeated phrases of 1..4 words
MAX_REPEATS = 3          # ... said more than 3 times in a row


@dataclass(frozen=True)
class DecodeConfig:
    language: str = "vi"
    beam_size: int = 5
    repetition_penalty: float = 1.0
    batch_size: int = 16
    suppress_blank: bool = True          # False lets a finetuned model answer "" (plan 4.2)
    no_speech_threshold: float = 0.6
    log_prob_threshold: float = -1.0


@dataclass
class SpanSegment:
    start: float                 # seconds in the source recording (VAD span)
    end: float
    text: str                    # after filters; "" when dropped or nothing decoded
    raw_text: str                # what Whisper produced for this span
    dropped: str | None = None   # None | "no_speech" | "repetition" | "blacklist"
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None
    metadata: dict = field(default_factory=dict)


def seek_of(span: Span, frames_per_second: float) -> int:
    """The `seek` faster-whisper assigns to a clip that starts at span.start.

    Mirrors BatchedInferencePipeline: seconds -> int samples -> seconds -> int frames.
    """
    start_samples = int(span.start_s * SAMPLE_RATE)
    return int(start_samples / SAMPLE_RATE * frames_per_second)


def fold(text: str) -> list[str]:
    """Lowercase, unaccented word tokens: the form blacklist phrases are matched in."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    unaccented = "".join(c for c in decomposed if unicodedata.category(c) != "Mn").replace("đ", "d")
    return re.findall(r"[a-z0-9]+", unaccented)


def load_blacklist(path: str | Path | None) -> list[list[str]]:
    if path is None:
        return []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    phrases = [fold(line) for line in lines if line.strip() and not line.lstrip().startswith("#")]
    return sorted((p for p in phrases if p), key=len, reverse=True)


def strip_blacklisted(text: str, blacklist: list[list[str]]) -> str:
    """Remove every blacklisted phrase from text, keeping the other words as written."""
    words = text.split()
    # A written word can fold to several tokens ("La-La" -> la, la); track the owner of each.
    tokens, owner = [], []
    for index, word in enumerate(words):
        for token in fold(word):
            tokens.append(token)
            owner.append(index)
    remove: set[int] = set()
    for phrase in blacklist:
        for at in range(len(tokens) - len(phrase) + 1):
            if tokens[at: at + len(phrase)] == phrase:
                remove.update(owner[at: at + len(phrase)])
    return " ".join(word for index, word in enumerate(words) if index not in remove)


def has_repetition(text: str) -> bool:
    """True when a phrase of 1..4 words occurs more than MAX_REPEATS times in a row."""
    tokens = fold(text)
    for size in range(1, MAX_REPEATED_NGRAM + 1):
        for at in range(len(tokens) - size * (MAX_REPEATS + 1) + 1):
            phrase = tokens[at: at + size]
            if all(tokens[at + k * size: at + (k + 1) * size] == phrase for k in range(1, MAX_REPEATS + 1)):
                return True
    return False


def apply_filters(segment: SpanSegment, config: DecodeConfig, blacklist: list[list[str]]) -> SpanSegment:
    """Plan 4.4. BatchedInferencePipeline does not skip non-speech by itself, so this must run."""
    text = segment.raw_text.strip()
    if not text:
        segment.text = ""
        return segment
    if (segment.no_speech_prob is not None and segment.avg_logprob is not None
            and segment.no_speech_prob > config.no_speech_threshold
            and segment.avg_logprob < config.log_prob_threshold):
        segment.text, segment.dropped = "", "no_speech"
        return segment
    if has_repetition(text):
        segment.text, segment.dropped = "", "repetition"
        return segment
    cleaned = strip_blacklisted(text, blacklist)
    if cleaned != text and not fold(cleaned):
        segment.text, segment.dropped = "", "blacklist"
        return segment
    segment.text = cleaned
    return segment


def map_segments_to_spans(spans: list[Span], decoded: list, frames_per_second: float) -> list[SpanSegment]:
    """Exactly one SpanSegment per span, in span order.

    `decoded` are faster-whisper segments (need .seek, .text, .avg_logprob,
    .no_speech_prob, .compression_ratio). A span with no decoded segment gets
    empty text; a decoded segment whose seek matches no span is an error.
    """
    seeks = [seek_of(span, frames_per_second) for span in spans]
    if len(set(seeks)) != len(seeks):
        raise ValueError("two spans map to the same seek; spans must not start within one frame of each other")
    by_seek: dict[int, list] = {}
    for item in decoded:
        by_seek.setdefault(item.seek, []).append(item)
    unknown = set(by_seek) - set(seeks)
    if unknown:
        raise ValueError(f"decoded segments with a seek that matches no span: {sorted(unknown)[:5]}")

    result = []
    for span, seek in zip(spans, seeks):
        items = by_seek.get(seek, [])
        segment = SpanSegment(start=span.start_s, end=span.end_s, text="", raw_text="".join(i.text for i in items))
        if items:
            segment.avg_logprob = min(i.avg_logprob for i in items)
            segment.no_speech_prob = items[0].no_speech_prob
            segment.compression_ratio = max(i.compression_ratio for i in items)
        result.append(segment)
    return result


class SpanTranscriber:
    def __init__(
        self,
        model: str = "large-v3-turbo",
        device: str = "cuda",
        compute_type: str = "float16",
        vad: VadConfig = VadConfig(),
        decode: DecodeConfig = DecodeConfig(),
        blacklist_path: str | Path | None = None,
        model_version: str | None = None,
        **model_kwargs,
    ):
        from faster_whisper import BatchedInferencePipeline, WhisperModel

        self.vad, self.decode = vad, decode
        self.blacklist = load_blacklist(blacklist_path)
        self.model_version = model_version or str(model)
        self._model = WhisperModel(model, device=device, compute_type=compute_type, **model_kwargs)
        self._pipeline = BatchedInferencePipeline(self._model)

    def transcribe(self, audio: np.ndarray) -> list[SpanSegment]:
        """16 kHz mono float32 recording -> one segment per VAD span, in time order."""
        return self.transcribe_spans(audio, detect_spans(audio, self.vad))

    def transcribe_spans(self, audio: np.ndarray, spans: list[Span]) -> list[SpanSegment]:
        if not spans:
            return []
        too_long = [s for s in spans if s.end_s - s.start_s > MAX_SPAN_S]
        if too_long:
            raise ValueError(f"{len(too_long)} span(s) longer than {MAX_SPAN_S}s; lower VadConfig.max_speech_s")
        decoded, _ = self._pipeline.transcribe(
            audio,
            clip_timestamps=[{"start": s.start_s, "end": s.end_s} for s in spans],
            language=self.decode.language,
            task="transcribe",
            beam_size=self.decode.beam_size,
            best_of=self.decode.beam_size,
            temperature=0.0,
            repetition_penalty=self.decode.repetition_penalty,
            condition_on_previous_text=False,
            suppress_blank=self.decode.suppress_blank,
            without_timestamps=True,
            word_timestamps=False,
            batch_size=self.decode.batch_size,
        )
        segments = map_segments_to_spans(spans, list(decoded), self._model.frames_per_second)
        for segment in segments:
            apply_filters(segment, self.decode, self.blacklist)
            segment.metadata = {"model_version": self.model_version, "pipeline": "span"}
        return segments


def main() -> None:
    import argparse

    import soundfile as sf

    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--language", default="vi")
    parser.add_argument("--blacklist")
    args = parser.parse_args()

    audio, sr = sf.read(args.audio, dtype="float32", always_2d=True)
    assert sr == SAMPLE_RATE, f"expected 16 kHz, got {sr}"
    transcriber = SpanTranscriber(args.model, args.device, args.compute_type,
                                  decode=DecodeConfig(language=args.language), blacklist_path=args.blacklist)
    for s in transcriber.transcribe(audio.mean(axis=1)):
        note = f"  [dropped: {s.dropped}: {s.raw_text.strip()!r}]" if s.dropped else ""
        print(f"{s.start:8.2f} - {s.end:8.2f}  {s.text}{note}")


if __name__ == "__main__":
    main()
