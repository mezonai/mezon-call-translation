"""Choose test / dev / train rooms (plan 8). Room-disjoint, frozen once written.

Test takes the most recent rooms, dev a random draw from the rest, train
everything else. The raw-hour targets are generous on purpose: 02_prelabel.py
samples the ~2h (test) and ~1h (dev) of labelled windows out of these rooms.

Usage:
    python scripts/04_split.py                       # reads data/manifests/tracks.jsonl
    python scripts/04_split.py --test-rooms 8 --dev-rooms 5
Output: data/manifests/split_rooms.json (commit this file).
"""

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mezon_whisper.manifest import MANIFESTS, read_jsonl  # noqa: E402


def hours(tracks: list[dict]) -> float:
    return sum(t["duration"] for t in tracks) / 3600


def take(order: list[str], rooms: dict[str, list[dict]], min_rooms: int, min_hours: float) -> list[str]:
    picked, total = [], 0.0
    for room in order:
        if len(picked) >= min_rooms and total >= min_hours:
            break
        picked.append(room)
        total += hours(rooms[room])
    return picked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks", default=str(MANIFESTS / "tracks.jsonl"))
    parser.add_argument("--out", default=str(MANIFESTS / "split_rooms.json"))
    parser.add_argument("--test-rooms", type=int, default=8, help="minimum number of test rooms")
    parser.add_argument("--test-raw-hours", type=float, default=6)
    parser.add_argument("--dev-rooms", type=int, default=5, help="minimum number of dev rooms")
    parser.add_argument("--dev-raw-hours", type=float, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="overwrite an existing (frozen) split")
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"{out} already exists. The split is frozen (plan 7.3); use --force only before any labelling.",
              file=sys.stderr)
        return 2

    tracks = read_jsonl(args.tracks)
    rooms: dict[str, list[dict]] = {}
    for track in tracks:
        rooms.setdefault(track["room_id"], []).append(track)
    by_recency = sorted(rooms, key=lambda room: max(t["date"] for t in rooms[room]), reverse=True)

    test = take(by_recency, rooms, args.test_rooms, args.test_raw_hours)
    rest = [room for room in by_recency if room not in test]
    random.Random(args.seed).shuffle(rest)
    dev = take(rest, rooms, args.dev_rooms, args.dev_raw_hours)
    train = [room for room in rest if room not in dev]
    split = {"test": test, "dev": dev, "train": train}

    assert not (set(test) & set(dev) or set(test) & set(train) or set(dev) & set(train)), "rooms overlap"
    assert sum(len(v) for v in split.values()) == len(rooms)
    if not train or len(train) < len(test):
        print("WARNING: very few train rooms; extract more audio or lower --test-rooms / --dev-rooms", file=sys.stderr)

    speakers = {name: {t["speaker"] for room in ids for t in rooms[room]} for name, ids in split.items()}
    print(f"{'set':6s} {'rooms':>5s} {'tracks':>6s} {'hours':>7s} {'speakers':>8s}  dates")
    for name, ids in split.items():
        members = [t for room in ids for t in rooms[room]]
        dates = sorted(t["date"] for t in members)
        print(f"{name:6s} {len(ids):5d} {len(members):6d} {hours(members):7.1f} {len(speakers[name]):8d}  "
              f"{dates[0] if dates else '-'} .. {dates[-1] if dates else '-'}")
    for name in ("test", "dev"):
        unseen = speakers[name] - speakers["train"]
        print(f"{name}: {len(unseen)} of {len(speakers[name])} speakers never appear in train (unseen speakers)")

    split["meta"] = {"seed": args.seed, "tracks": len(tracks),
                     "unseen_test_speakers": sorted(speakers["test"] - speakers["train"])}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(split, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
