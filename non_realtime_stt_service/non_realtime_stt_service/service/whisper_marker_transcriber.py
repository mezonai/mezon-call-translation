"""Sequential Whisper transcription with VAD-owned timestamps and audio markers.

The non-realtime STT service receives headerless PCM16.  This module therefore
only accepts an already decoded, mono float32 waveform at 16 kHz; it must not
try to infer a container format from a recording path.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

from non_realtime_stt_service.service.gipformer_service import GipformerService


SAMPLE_RATE = 16_000
MAX_VAD_SEGMENT_S = 30.0
MAX_PACKED_S = 30.0

# Algorithm profile. These are intentionally code-versioned rather than
# deployment environment variables: changing one requires a transcript QA run.
VAD_THRESHOLD = 0.5
VAD_NEG_THRESHOLD_OFFSET = 0.15
VAD_MIN_SPEECH_DURATION_MS = 250
VAD_MIN_SILENCE_DURATION_MS = 1000
VAD_SPEECH_PAD_MS = 250

MARKER_TEXT = "dau moc hong ngoc"
MARKER_GAIN_DB = -5.0
MARKER_GUARD_S = 0.1
MARKER_MAX_TOKEN_EDIT_DISTANCE = 1
HALLUCINATION_BLACKLIST = (
    "hay subscribe cho kenh ghien mi go de khong bo lo nhung video hap dan",
    "de khong bo lo nhung video hap dan",
    "cam on cac ban da theo doi va hen gap lai",
    "hay subscribe cho kenh la la school",
    "hay subscribe cho kenh",
)


@dataclass(frozen=True)
class MarkerTranscriptionSegment:
    """One transcript whose time range is owned by a source VAD span."""

    start: float
    end: float
    text: str


@dataclass
class PreparedMarkerAudio:
    """VAD and packed audio work prepared once for one recording."""

    audio: np.ndarray
    spans: list[dict]
    packed_chunks: list[dict]

    @property
    def duration_after_vad_sec(self) -> float:
        return sum(span["end"] - span["start"] for span in self.spans) / SAMPLE_RATE


def make_vad_options():
    """Build the fixed, tested VAD profile for marker transcription."""
    from faster_whisper.vad import VadOptions

    return VadOptions(
        threshold=VAD_THRESHOLD,
        neg_threshold=max(0.01, VAD_THRESHOLD - VAD_NEG_THRESHOLD_OFFSET),
        min_speech_duration_ms=VAD_MIN_SPEECH_DURATION_MS,
        max_speech_duration_s=MAX_VAD_SEGMENT_S,
        min_silence_duration_ms=VAD_MIN_SILENCE_DURATION_MS,
        speech_pad_ms=VAD_SPEECH_PAD_MS,
    )


def detect_speech(audio: np.ndarray) -> list[dict]:
    """Return non-overlapping 16 kHz speech spans in original audio time."""
    from faster_whisper.vad import get_speech_timestamps

    timestamps = get_speech_timestamps(
        audio,
        vad_options=make_vad_options(),
        sampling_rate=SAMPLE_RATE,
    )
    spans = [
        {"start": max(0, int(item["start"])), "end": min(len(audio), int(item["end"]))}
        for item in timestamps
        if int(item["end"]) > int(item["start"])
    ]
    spans.sort(key=lambda item: item["start"])
    if any(
        current["start"] < previous["end"]
        for previous, current in zip(spans, spans[1:])
    ):
        raise RuntimeError("VAD returned overlapping speech spans")
    return spans


def normalize_tokens(text: str) -> list[str]:
    """Normalize Vietnamese/ASCII tokens for robust marker matching."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    unaccented = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    ).replace(chr(0x0111), "d")
    return re.findall(r"[a-z0-9]+", unaccented)


def trim_marker(marker: np.ndarray) -> np.ndarray:
    """Remove silence around the versioned marker asset and apply its gain."""
    frame_samples = round(0.01 * SAMPLE_RATE)
    usable = len(marker) // frame_samples * frame_samples
    frames = marker[:usable].reshape(-1, frame_samples)
    rms = np.sqrt(np.mean(np.square(frames, dtype=np.float32), axis=1))
    active = np.flatnonzero(rms >= 10 ** (-45 / 20))
    if not len(active):
        raise ValueError("Marker has no detectable speech")
    padding = round(0.01 * SAMPLE_RATE)
    start = max(0, active[0] * frame_samples - padding)
    end = min(len(marker), (active[-1] + 1) * frame_samples + padding)
    gain = 10 ** (MARKER_GAIN_DB / 20)
    return np.ascontiguousarray(marker[start:end] * gain)


def pack_speech_with_marker(
    audio: np.ndarray,
    spans: list[dict],
    marker: np.ndarray,
) -> list[dict]:
    """Pack source VAD spans up to 30 seconds, bridged by the marker asset."""
    max_samples = round(MAX_PACKED_S * SAMPLE_RATE)
    guard = np.zeros(round(MARKER_GUARD_S * SAMPLE_RATE), dtype=np.float32)
    bridge = [guard, marker, guard]
    bridge_length = sum(len(part) for part in bridge)
    chunks: list[dict] = []
    parts: list[np.ndarray] = []
    children: list[dict] = []
    cursor = 0

    def flush() -> None:
        nonlocal parts, children, cursor
        if children:
            chunks.append(
                {
                    "audio": np.ascontiguousarray(
                        np.concatenate(parts), dtype=np.float32
                    ),
                    "children": children,
                }
            )
        parts, children, cursor = [], [], 0

    for speech_id, span in enumerate(spans):
        start, end = int(span["start"]), int(span["end"])
        speech = audio[start:end]
        if len(speech) > max_samples:
            raise ValueError("A VAD span is longer than the packed chunk limit")
        if children and cursor + bridge_length + len(speech) > max_samples:
            flush()
        if children:
            parts.extend(bridge)
            cursor += bridge_length
        children.append(
            {
                "speech_id": speech_id,
                "source_start": start,
                "source_end": end,
            }
        )
        parts.append(speech)
        cursor += len(speech)
    flush()
    return chunks


def token_edit_distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_token in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_token in enumerate(right, start=1):
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + (left_token != right_token),
                )
            )
        previous = current
    return previous[-1]


def aligned_marker_indexes(candidate: list[str], expected: list[str]) -> list[int]:
    costs = [[0] * (len(expected) + 1) for _ in range(len(candidate) + 1)]
    for index in range(len(candidate) + 1):
        costs[index][0] = index
    for index in range(len(expected) + 1):
        costs[0][index] = index
    for left_index, left_token in enumerate(candidate, start=1):
        for right_index, right_token in enumerate(expected, start=1):
            costs[left_index][right_index] = min(
                costs[left_index - 1][right_index] + 1,
                costs[left_index][right_index - 1] + 1,
                costs[left_index - 1][right_index - 1]
                + (left_token != right_token),
            )

    indexes: list[int] = []
    left_index, right_index = len(candidate), len(expected)
    while left_index or right_index:
        if (
            left_index
            and right_index
            and costs[left_index][right_index]
            == costs[left_index - 1][right_index - 1]
            + (candidate[left_index - 1] != expected[right_index - 1])
        ):
            indexes.append(left_index - 1)
            left_index -= 1
            right_index -= 1
        elif (
            left_index
            and costs[left_index][right_index]
            == costs[left_index - 1][right_index] + 1
        ):
            left_index -= 1
        else:
            right_index -= 1
    return list(reversed(indexes))


def marker_matches(tokens: list[dict]) -> list[dict]:
    expected = normalize_tokens(MARKER_TEXT)
    normalized = [normalize_tokens(token["text"]) for token in tokens]
    matches = []
    for index in range(len(tokens)):
        for width in range(len(expected) - 1, len(expected) + 2):
            if index + width > len(tokens):
                continue
            candidate = [
                item[0] if len(item) == 1 else ""
                for item in normalized[index : index + width]
            ]
            distance = token_edit_distance(candidate, expected)
            if distance <= MARKER_MAX_TOKEN_EDIT_DISTANCE:
                aligned = aligned_marker_indexes(candidate, expected)
                matches.append(
                    {
                        "start": index + aligned[0],
                        "end": index + aligned[-1] + 1,
                        "exact": distance == 0,
                    }
                )
    return matches


def select_marker_matches(tokens: list[dict]) -> list[dict]:
    candidates = marker_matches(tokens)
    selected = []
    cursor = 0
    while True:
        available = [match for match in candidates if match["start"] >= cursor]
        if not available:
            return selected
        first_start = min(match["start"] for match in available)
        match = max(
            (item for item in available if item["start"] == first_start),
            key=lambda item: (item["exact"], item["end"] - item["start"]),
        )
        selected.append(match)
        cursor = match["end"]


def filter_whisper_hallucinations(
    tokens: list[dict],
    decoder_boundaries: list[int],
) -> tuple[list[dict], list[int]]:
    """Remove known Whisper spam before marker/VAD partitioning.

    This operates on the complete raw output of one Whisper decode, so a
    phrase is still detectable when it would later be split across VAD spans.
    Gipformer does not use this filter.
    """
    normalized = [
        parts[0] if len(parts) == 1 else ""
        for parts in (normalize_tokens(token["text"]) for token in tokens)
    ]
    remove_indexes: set[int] = set()
    for phrase in HALLUCINATION_BLACKLIST:
        expected = normalize_tokens(phrase)
        for index in range(len(tokens) - len(expected) + 1):
            if normalized[index : index + len(expected)] == expected:
                remove_indexes.update(range(index, index + len(expected)))

    if not remove_indexes:
        return tokens, decoder_boundaries

    filtered = []
    raw_to_filtered = [0] * (len(tokens) + 1)
    for index, token in enumerate(tokens):
        raw_to_filtered[index] = len(filtered)
        if index not in remove_indexes:
            filtered.append(token)
    raw_to_filtered[len(tokens)] = len(filtered)

    remapped_boundaries = []
    for boundary in decoder_boundaries:
        remapped = raw_to_filtered[boundary]
        if 0 < remapped < len(filtered) and remapped not in remapped_boundaries:
            remapped_boundaries.append(remapped)
    return filtered, remapped_boundaries


def estimate_boundaries(
    tokens: list[dict],
    children: list[dict],
    marker_hints: list[int],
    decoder_boundaries: set[int],
) -> list[int]:
    child_count = len(children)
    if child_count <= 1:
        return []
    token_count = len(tokens)
    durations = [child["source_end"] - child["source_start"] for child in children]
    total_duration = max(1, sum(durations))
    targets = [token_count * duration / total_duration for duration in durations]
    marker_positions = set(marker_hints)

    def mode_cost(position: int) -> float:
        if position in marker_positions:
            return -20.0
        if position in decoder_boundaries:
            return -3.0
        if position and re.search(r'[.!?\u2026]["\')\]]*$', tokens[position - 1]["text"]):
            return -1.5
        return 50.0

    states: dict[int, tuple[float, list[int]]] = {0: (0.0, [])}
    for child_index in range(child_count):
        next_states: dict[int, tuple[float, list[int]]] = {}
        for start, (cost, cuts) in states.items():
            ends = [token_count] if child_index == child_count - 1 else range(start, token_count + 1)
            for end in ends:
                target = targets[child_index]
                actual = end - start
                segment_cost = ((actual - target) / max(1.5, target**0.5)) ** 2
                if actual == 0 and target >= 1.0:
                    segment_cost += 3.0
                candidate_cost = cost + segment_cost
                if child_index < child_count - 1:
                    candidate_cost += mode_cost(end)
                previous = next_states.get(end)
                if previous is None or candidate_cost < previous[0]:
                    next_states[end] = (
                        candidate_cost,
                        cuts if child_index == child_count - 1 else [*cuts, end],
                    )
        states = next_states
    result = states.get(token_count)
    if result is None:
        raise RuntimeError("Boundary DP could not construct a partition")
    return result[1]


def partition_tokens(
    tokens: list[dict],
    children: list[dict],
    decoder_boundaries: list[int],
) -> list[list[dict]]:
    matches = select_marker_matches(tokens)
    removed_indexes = {
        index for match in matches for index in range(match["start"], match["end"])
    }
    raw_to_clean = [0] * (len(tokens) + 1)
    clean_tokens = []
    for index, token in enumerate(tokens):
        raw_to_clean[index] = len(clean_tokens)
        if index not in removed_indexes:
            clean_tokens.append(token)
    raw_to_clean[len(tokens)] = len(clean_tokens)
    marker_hints = [raw_to_clean[match["start"]] for match in matches]
    clean_decoder_boundaries = {
        raw_to_clean[position]
        for position in decoder_boundaries
        if 0 < position < len(tokens)
    }

    if len(marker_hints) == len(children) - 1:
        cuts = marker_hints
    else:
        cuts = estimate_boundaries(
            clean_tokens,
            children,
            marker_hints,
            clean_decoder_boundaries,
        )
    boundaries = [0, *cuts, len(clean_tokens)]
    return [
        clean_tokens[start:end]
        for start, end in zip(boundaries, boundaries[1:])
    ]


def text_tokens(text: str) -> list[dict]:
    """Keep the token representation shared by Whisper and Gipformer."""
    return [{"text": token} for token in text.strip().split()]


def resolved_slots(
    tokens: list[dict],
    children: list[dict],
    decoder_boundaries: list[int] | None = None,
) -> list[list[dict]] | None:
    """Return usable child slots, or None when a fallback is required."""
    if not tokens:
        return None
    slots = partition_tokens(tokens, children, decoder_boundaries or [])
    return slots if any(slots) else None


class MarkerWhisperTranscriber:
    """Coordinates sequential Whisper marker-VAD decoding and Gipformer fallback."""

    def __init__(
        self,
        *,
        model_size: str | Path,
        marker_path: Path,
        compute_type: str,
        cpu_threads: int,
        temperature: list[float] | float,
        language: str | None,
    ) -> None:
        if cpu_threads < 1:
            raise ValueError("cpu_threads must be positive")
            
        if isinstance(temperature, list):
            if any(t < 0 for t in temperature):
                raise ValueError("temperatures must be non-negative")
        elif temperature < 0:
            raise ValueError("temperature must be non-negative")

        # Accept either a faster-whisper model name (e.g. ``large-v3-turbo``)
        # or an explicit local CTranslate2 directory for offline deployments.
        self._model_size = model_size
        self._marker_path = marker_path
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        self._temperature = temperature
        self._language = language
        self._model: WhisperModel | None = None
        self._marker: np.ndarray | None = None
        self._gipformer = GipformerService(cpu_threads=cpu_threads)

    def initialize(self) -> None:
        """Load/cache the models and marker asset before accepting Redis work."""
        if self._model is not None:
            # A previous startup attempt can have loaded Whisper successfully
            # but failed while creating Gipformer. Re-run the idempotent
            # fallback initialization instead of treating that half-ready
            # state as a completed startup.
            self._gipformer.initialize()
            return

        if not self._marker_path.is_file():
            raise FileNotFoundError(f"Whisper marker asset not found: {self._marker_path}")

        marker = np.asarray(
            decode_audio(str(self._marker_path), sampling_rate=SAMPLE_RATE),
            dtype=np.float32,
        )
        self._marker = trim_marker(marker)
        self._model = WhisperModel(
            str(self._model_size),
            device="cpu",
            compute_type=self._compute_type,
            cpu_threads=self._cpu_threads,
            # This must stay one: the production flow owns one model worker
            # and processes one recording/batch sequence at a time.
            num_workers=1,
        )
        self._gipformer.initialize()

        # Preload the packaged Silero VAD asset during startup, not on the
        # first recording.
        from faster_whisper.vad import get_vad_model

        get_vad_model()

    def _transcribe_with_whisper(self, audio: np.ndarray) -> tuple[list[dict], list[int]]:
        if self._model is None:
            raise RuntimeError("MarkerWhisperTranscriber is not initialized")
        stream, _ = self._model.transcribe(
            audio,
            language=self._language,
            task="transcribe",
            beam_size=1,
            best_of=1,
            temperature=self._temperature,
            condition_on_previous_text=False,
            compression_ratio_threshold=2.4,
            repetition_penalty=1.2,
            log_prob_threshold=-1.0,
            no_speech_threshold=0.6,
            without_timestamps=True,
            word_timestamps=False,
            vad_filter=False,
        )
        tokens: list[dict] = []
        decoder_boundaries = []
        for decoded_segment in stream:
            segment_tokens = text_tokens(decoded_segment.text)
            if segment_tokens:
                if tokens:
                    decoder_boundaries.append(len(tokens))
                tokens.extend(segment_tokens)
        return filter_whisper_hallucinations(tokens, decoder_boundaries)

    def _transcribe_with_gipformer(self, audio: np.ndarray) -> list[dict]:
        return text_tokens(self._gipformer.transcribe(audio))

    def _transcribe_children_with_gipformer(
        self,
        source_audio: np.ndarray,
        children: list[dict],
    ) -> list[list[dict]]:
        """Last-resort per-span decode, deliberately sequential on CPU."""
        result = []
        for child in children:
            segment = source_audio[child["source_start"] : child["source_end"]]
            result.append(self._transcribe_with_gipformer(segment))
        return result

    def prepare_audio(self, audio: np.ndarray) -> PreparedMarkerAudio:
        """Create source spans and packed model inputs for one raw PCM recording."""
        if self._model is None or self._marker is None:
            raise RuntimeError("MarkerWhisperTranscriber is not initialized")
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        if audio.ndim != 1:
            raise ValueError(f"Expected mono waveform, got shape {audio.shape}")
        spans = detect_speech(audio)
        return PreparedMarkerAudio(
            audio=audio,
            spans=spans,
            packed_chunks=pack_speech_with_marker(audio, spans, self._marker),
        )

    def iter_segments(
        self,
        prepared: PreparedMarkerAudio,
        logger: logging.Logger | logging.LoggerAdapter | None = None,
    ) -> Iterator[MarkerTranscriptionSegment]:
        """Yield final segments in source order, one packed chunk at a time."""
        if self._model is None:
            raise RuntimeError("MarkerWhisperTranscriber is not initialized")

        for chunk_idx, chunk in enumerate(prepared.packed_chunks):
            whisper_tokens, decoder_boundaries = self._transcribe_with_whisper(chunk["audio"])
            if logger:
                whisper_text = " ".join(t["text"] for t in whisper_tokens)
                logger.debug("Chunk %d - Whisper raw result: %r", chunk_idx, whisper_text)
                
            slots = resolved_slots(whisper_tokens, chunk["children"], decoder_boundaries)
            
            if slots is None:
                if logger:
                    logger.warning("Chunk %d - Whisper marker alignment failed! Falling back to Gipformer (Chunk-level)", chunk_idx)
                    
                gipformer_tokens = self._transcribe_with_gipformer(chunk["audio"])
                if logger:
                    gipformer_text = " ".join(t["text"] for t in gipformer_tokens)
                    logger.info("Chunk %d - Gipformer chunk result: %r", chunk_idx, gipformer_text)
                    
                slots = resolved_slots(gipformer_tokens, chunk["children"])
                
            if slots is None:
                if logger:
                    logger.warning("Chunk %d - Gipformer chunk marker alignment failed! Falling back to Gipformer (Per-child)", chunk_idx)
                    
                slots = self._transcribe_children_with_gipformer(
                    prepared.audio,
                    chunk["children"],
                )
                if logger:
                    child_texts = [" ".join(t["text"] for t in slot) for slot in slots]
                    logger.info("Chunk %d - Gipformer per-child result: %r", chunk_idx, child_texts)
                    
            for child, slot in zip(chunk["children"], slots):
                text = " ".join(token["text"] for token in slot).strip()
                if text:
                    yield MarkerTranscriptionSegment(
                        start=round(child["source_start"] / SAMPLE_RATE, 3),
                        end=round(child["source_end"] / SAMPLE_RATE, 3),
                        text=text,
                    )

    def shutdown(self) -> None:
        """Release references held by the model and marker waveform."""
        self._model = None
        self._marker = None
        self._gipformer.shutdown()

    def gipformer_health_status(self) -> dict:
        return self._gipformer.health_status()
