"""Cut the extracted tracks into labelling units and pre-label them (plan 5.1, 5.3, 5.4).

  windows  test + dev rooms -> continuous 60-120s windows, pre-labelled with the
           span pipeline (model A) so labellers start from regions with text.
  clips    train rooms -> one clip per VAD span (+ a few clips from outside any
           span as non-speech candidates), decoded by two models and routed:
             nonspeech_check  a model returned nothing / filters fired
             human_priority   Vietnamese with English terms (code-switching)
             auto_accept      both models agree and are confident -> pseudo-label
             human            everything else

Train clips are cut with a per-track random min_silence (500-1000 ms) and padding
(100-400 ms), so the model is not tied to one VAD setting (plan 3.9). Windows use
the default VAD profile and are cut at silences.

Both commands resume: tracks already present in the output manifest are skipped.

Usage:
    python scripts/02_prelabel.py windows                # test 2h + dev 1h
    python scripts/02_prelabel.py clips
Output: data/windows/*.wav + data/manifests/candidates_windows.jsonl
        data/clips/*.wav   + data/manifests/candidates_clips.jsonl
"""

import argparse
import json
import random
import re
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mezon_whisper.manifest import (MANIFESTS, ROOT, SAMPLE_RATE, load_audio, read_jsonl,  # noqa: E402
                                    safe_id, save_audio, write_jsonl)
from mezon_whisper.normalize import normalize, word_error_rate  # noqa: E402
from mezon_whisper.span_transcriber import DecodeConfig, SpanSegment, SpanTranscriber  # noqa: E402
from mezon_whisper.vad import Span, VadConfig, detect_spans  # noqa: E402

BLACKLIST = ROOT / "labeling" / "hallucination_blacklist.txt"

WINDOW_MIN_S, WINDOW_TARGET_S, WINDOW_MAX_S = 60.0, 90.0, 120.0
WINDOW_TAIL_MIN_S = 30.0          # a shorter leftover at the end of a track is dropped
BOUNDARY_GAP_S = 0.5              # windows may only be cut inside silences at least this long
QUIET_SPEECH_RATIO = 0.05         # below this a window counts as "quiet" (hallucination probe)
QUIET_SHARE = 0.10                # share of window time taken from quiet windows

MIN_CLIP_S = 0.5
GAP_MARGIN_S = 1.0                # non-speech clips keep this distance from any span
GAP_CLIP_SHARE = 0.08             # non-speech candidates per speech span

AUTO_ACCEPT_WER = 0.05
AUTO_ACCEPT_LOGPROB = -0.3
COMPRESSION_RATIO_MAX = 2.4

# A Vietnamese syllable written without accents: optional onset, 1-3 vowels, optional final.
_VI_SYLLABLE = re.compile(
    r"^(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdghklmnpqrstvx])?[aeiouy]{1,3}(ch|ng|nh|[cmnpt])?$")


def looks_english(word: str) -> bool:
    """ASCII word that cannot be a Vietnamese syllable: deploy, review, PR, staging."""
    letters = re.sub(r"[^A-Za-z]", "", word)
    if len(letters) < 2 or letters != re.sub(r"[^\w]", "", word):
        return False
    return not _VI_SYLLABLE.match(letters.lower())


def guess_category(text: str) -> str:
    words = normalize(text).split()
    if not words:
        return "nonspeech"
    english = sum(looks_english(w) for w in words)
    if english == 0:
        return "vi"
    has_vietnamese = any(not w.isascii() for w in words)
    return "mixed" if has_vietnamese or english < len(words) * 0.6 else "en"


def load_split() -> dict:
    path = MANIFESTS / "split_rooms.json"
    if not path.exists():
        sys.exit(f"{path} not found: run scripts/04_split.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def overlap(span: Span, start: int, end: int) -> int:
    return max(0, min(span.end, end) - max(span.start, start))


def prelabel_fields(segment: SpanSegment) -> dict:
    return {"text": segment.text, "raw": segment.raw_text.strip(), "dropped": segment.dropped,
            "avg_logprob": segment.avg_logprob, "no_speech_prob": segment.no_speech_prob,
            "compression_ratio": segment.compression_ratio}


# ---- windows --------------------------------------------------------------------------

def window_bounds(spans: list[Span], total: int) -> list[tuple[int, int]]:
    """Consecutive windows of a track, cut in silences where possible."""
    cuts = [0]
    for previous, current in zip(spans, spans[1:]):
        if current.start - previous.end >= BOUNDARY_GAP_S * SAMPLE_RATE:
            cuts.append((previous.end + current.start) // 2)
    cuts.append(total)

    windows, start = [], 0
    while total - start >= WINDOW_TAIL_MIN_S * SAMPLE_RATE:
        end = next((c for c in cuts if c >= start + WINDOW_MIN_S * SAMPLE_RATE), total)
        if end - start > WINDOW_MAX_S * SAMPLE_RATE:      # long silence or long monologue: cut by the clock
            end = start + int(WINDOW_TARGET_S * SAMPLE_RATE)
        windows.append((start, end))
        start = end
    return windows


def pick_windows(per_track: dict[str, list[dict]], seconds: float, rng: random.Random) -> list[dict]:
    """Round-robin over tracks so no speaker dominates; ~QUIET_SHARE of the time from quiet windows."""
    def take(pool: dict[str, list[dict]], budget: float) -> list[dict]:
        queues = [rng.sample(items, len(items)) for items in pool.values() if items]
        rng.shuffle(queues)
        picked, total = [], 0.0
        while total < budget and any(queues):
            for queue in queues:
                if queue and total < budget:
                    picked.append(queue.pop())
                    total += picked[-1]["duration"]
        return picked

    speech = {t: [w for w in ws if w["speech_ratio"] >= QUIET_SPEECH_RATIO] for t, ws in per_track.items()}
    quiet = {t: [w for w in ws if w["speech_ratio"] < QUIET_SPEECH_RATIO] for t, ws in per_track.items()}
    return take(speech, seconds * (1 - QUIET_SHARE)) + take(quiet, seconds * QUIET_SHARE)


def run_windows(args) -> int:
    split, tracks = load_split(), read_jsonl(args.tracks)
    out_path = MANIFESTS / "candidates_windows.jsonl"
    done = {record["id"] for record in read_jsonl(out_path)}
    transcriber = None
    rng = random.Random(args.seed)

    for name, target_hours in (("test", args.test_hours), ("dev", args.dev_hours)):
        members = [t for t in tracks if t["room_id"] in split[name]]
        print(f"\n== {name}: {len(members)} tracks, target {target_hours} h of windows ==", flush=True)
        per_track, cache = {}, {}
        for track in members:
            audio = load_audio(track["audio"])
            spans = detect_spans(audio, VadConfig())
            cache[track["track_id"]] = spans
            per_track[track["track_id"]] = [
                {"track": track, "start": start, "end": end, "index": index,
                 "duration": (end - start) / SAMPLE_RATE,
                 "speech_ratio": sum(overlap(s, start, end) for s in spans) / (end - start)}
                for index, (start, end) in enumerate(window_bounds(spans, len(audio)))]
        picked = pick_windows(per_track, target_hours * 3600, rng)
        print(f"picked {len(picked)} windows, {sum(w['duration'] for w in picked) / 3600:.2f} h "
              f"from {len({w['track']['track_id'] for w in picked})} tracks", flush=True)

        for window in sorted(picked, key=lambda w: (w["track"]["track_id"], w["start"])):
            track = window["track"]
            window_id = f"{safe_id(track['track_id'])}_w{window['index']:03d}"
            if window_id in done:
                continue
            if transcriber is None:
                transcriber = SpanTranscriber(args.model_a, args.device, args.compute_type,
                                              decode=DecodeConfig(language=args.language),
                                              blacklist_path=BLACKLIST)
            audio = load_audio(track["audio"])
            start, end = window["start"], window["end"]
            inside = [Span(max(s.start, start), min(s.end, end)) for s in cache[track["track_id"]]
                      if overlap(s, start, end) >= 0.2 * SAMPLE_RATE]
            segments = transcriber.transcribe_spans(audio, inside)
            regions = [{"start": round(seg.start - start / SAMPLE_RATE, 3),
                        "end": round(seg.end - start / SAMPLE_RATE, 3),
                        "text": seg.text, "category": guess_category(seg.text),
                        "prelabel": prelabel_fields(seg)} for seg in segments]
            relative = f"data/windows/{window_id}.wav"
            save_audio(relative, audio[start:end])
            write_jsonl(out_path, [{
                "id": window_id, "kind": "window", "split": name, "audio": relative,
                "track_id": track["track_id"], "room_id": track["room_id"], "speaker": track["speaker"],
                "date": track["date"], "offset_start": round(start / SAMPLE_RATE, 3),
                "offset_end": round(end / SAMPLE_RATE, 3), "duration": round(window["duration"], 3),
                "speech_ratio": round(window["speech_ratio"], 3), "regions": regions,
                "source": "mezon", "label_type": None,
            }], append=True)
            done.add(window_id)
    print(f"\nwrote {out_path}")
    return 0


# ---- clips ----------------------------------------------------------------------------

def gap_clips(spans: list[Span], total: int, rng: random.Random) -> list[Span]:
    """Random 2-10s chunks well inside the silences between spans: non-speech candidates."""
    margin = int(GAP_MARGIN_S * SAMPLE_RATE)
    edges = [0] + [x for s in spans for x in (s.start, s.end)] + [total]
    gaps = [(edges[i] + margin, edges[i + 1] - margin) for i in range(0, len(edges), 2)]
    gaps = [(a, b) for a, b in gaps if b - a >= 2 * SAMPLE_RATE]
    wanted = min(len(gaps), max(1, round(len(spans) * GAP_CLIP_SHARE)))
    clips = []
    for a, b in rng.sample(gaps, wanted):
        length = min(b - a, int(rng.uniform(2, 10) * SAMPLE_RATE))
        start = rng.randint(a, b - length)
        clips.append(Span(start, start + length))
    return clips


def route(a: SpanSegment, b: SpanSegment, is_gap: bool) -> tuple[str, float]:
    text_a, text_b = normalize(a.text), normalize(b.text)
    agree = word_error_rate(text_b, text_a) if text_b else (0.0 if not text_a else 1.0)
    suspicious = (
        is_gap or not text_a or not text_b or a.dropped or b.dropped
        or (a.no_speech_prob or 0) > 0.6 or (b.no_speech_prob or 0) > 0.6
        or (a.compression_ratio or 0) > COMPRESSION_RATIO_MAX or (b.compression_ratio or 0) > COMPRESSION_RATIO_MAX)
    if suspicious:
        return "nonspeech_check", agree
    if any(looks_english(w) for w in (text_a + " " + text_b).split()):
        return "human_priority", agree
    if agree < AUTO_ACCEPT_WER and min(a.avg_logprob, b.avg_logprob) > AUTO_ACCEPT_LOGPROB:
        return "auto_accept", agree
    return "human", agree


def run_clips(args) -> int:
    split, tracks = load_split(), read_jsonl(args.tracks)
    members = [t for t in tracks if t["room_id"] in split["train"]]
    out_path = MANIFESTS / "candidates_clips.jsonl"
    done_tracks = {record["track_id"] for record in read_jsonl(out_path)}
    todo = [t for t in members if t["track_id"] not in done_tracks]
    print(f"train: {len(members)} tracks, {len(todo)} to do", flush=True)
    if not todo:
        return 0

    decode = DecodeConfig(language=args.language)
    model_a = SpanTranscriber(args.model_a, args.device, args.compute_type, decode=decode, blacklist_path=BLACKLIST)
    model_b = SpanTranscriber(args.model_b, args.device, args.compute_type, decode=decode, blacklist_path=BLACKLIST)

    totals: dict[str, float] = {}
    for number, track in enumerate(todo, 1):
        rng = random.Random(zlib.crc32(track["track_id"].encode()) + args.seed)
        vad = VadConfig(min_silence_ms=rng.randint(500, 1000), speech_pad_ms=rng.randint(100, 400))
        audio = load_audio(track["audio"])
        spans = detect_spans(audio, vad)
        gaps = gap_clips(spans, len(audio), rng) if spans else []
        units = sorted([(s, False) for s in spans] + [(g, True) for g in gaps], key=lambda unit: unit[0].start)
        all_spans = [unit[0] for unit in units]
        decoded_a = model_a.transcribe_spans(audio, all_spans)
        decoded_b = model_b.transcribe_spans(audio, all_spans)

        records = []
        for index, ((span, is_gap), a, b) in enumerate(zip(units, decoded_a, decoded_b)):
            lane, agree = route(a, b, is_gap)
            duration = (span.end - span.start) / SAMPLE_RATE
            if duration < MIN_CLIP_S and lane != "nonspeech_check":
                continue
            best = b.text or a.text          # model B (large-v3) is the stronger pre-label
            clip_id = f"{safe_id(track['track_id'])}_{'g' if is_gap else 's'}{index:04d}"
            relative = f"data/clips/{clip_id}.wav"
            save_audio(relative, audio[span.start:span.end])
            records.append({
                "id": clip_id, "kind": "clip", "audio": relative,
                "text": best if lane == "auto_accept" else "",
                "lang": "vi", "category": guess_category(best), "duration": round(duration, 3),
                "speaker": track["speaker"], "room_id": track["room_id"], "date": track["date"],
                "track_id": track["track_id"], "offset_start": round(span.start_s, 3),
                "offset_end": round(span.end_s, 3), "source": "mezon",
                "label_type": "pseudo" if lane == "auto_accept" else None,
                "route": lane, "is_gap": is_gap,
                "vad": {"min_silence_ms": vad.min_silence_ms, "speech_pad_ms": vad.speech_pad_ms},
                "prelabel": {"text": best, "agree_wer": round(agree, 3),
                             "model_a": prelabel_fields(a), "model_b": prelabel_fields(b)},
            })
            totals[lane] = totals.get(lane, 0.0) + duration
        # One write per track keeps resume simple: a track is either fully in the manifest or not.
        write_jsonl(out_path, records, append=True)
        print(f"[{number}/{len(todo)}] {track['track_id']}: {len(spans)} spans, {len(gaps)} gap clips, "
              f"{len(records)} kept", flush=True)

    print("\nhours by route (this run):")
    for lane, seconds in sorted(totals.items(), key=lambda item: -item[1]):
        print(f"  {lane:16s} {seconds / 3600:6.2f} h")
    print(f"wrote {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["windows", "clips"])
    parser.add_argument("--tracks", default=str(MANIFESTS / "tracks.jsonl"))
    parser.add_argument("--model-a", default="large-v3-turbo")
    parser.add_argument("--model-b", default="large-v3")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--language", default="vi")
    parser.add_argument("--test-hours", type=float, default=2.0)
    parser.add_argument("--dev-hours", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    return run_windows(args) if args.command == "windows" else run_clips(args)


if __name__ == "__main__":
    sys.exit(main())
