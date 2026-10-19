"""List Mezon tracks for labelling and give each one a time-limited download link to its .ogg.

Only the person holding the production secrets runs this. The output (CSV + JSONL)
is what gets shared: it contains presigned MinIO URLs, so the team can download the
audio without any credentials until the links expire.

Selection, in order (every drop is counted in the summary):
  1. date range         room created on --date-from .. --date-to (local dates, inclusive)
  2. weekdays           optional --weekdays-only
  3. time of day        every track of the room must start and end inside --time-window
                        (local time, same day). By default a single track outside the
                        window drops the WHOLE room, since sensitivity is a property of the
                        meeting, not of one participant (--per-track to relax)
  4. agent tracks       track_id 'agent-tts' (the bot's synthesized voice) and any
                        --exclude-identity user ids
  5. readiness          STT status 'completed' and the .ogg derivative exists
  6. length             --min-seconds / --max-seconds, and --min-speech-seconds when the
                        STT recorded duration_after_vad_sec
  7. room shape         rooms with fewer than --min-room-speakers human speakers
                        (solo test calls), room names matching --exclude-room-name
  8. balance            at most --max-hours-per-speaker per participant (oldest rooms are
                        taken first), then --max-hours in total (whole rooms, newest first)

Times: tracks.audio_info.started_at_ns / ended_at_ns (epoch ns, written by orchestrator),
falling back to tracks.created_at + duration_sec when missing.

Usage:
    python scripts/01a_export_track_urls.py --env-file .env \
        --date-from 2026-10-01 --date-to 2026-10-31 --time-window 08:00-18:00 \
        --url-endpoint 172.16.110.19:9000 --expires-days 7
Output: exports/tracks_<from>_<to>.csv and .jsonl (+ the summary printed to stdout)
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
AGENT_TTS_TRACK_ID = "agent-tts"   # orchestrator_service/utils/constants.py
WEEKDAYS = range(0, 5)             # Monday..Friday
MAX_PRESIGN_DAYS = 7               # S3/MinIO limit for presigned URLs


@dataclass
class Track:
    track_id: str          # tracks.id (recording id), unique per row
    sfu_track_id: str      # tracks.track_id
    room_id: str
    room_name: str
    participant_id: str
    status: str
    object_key: str        # .ogg derivative
    duration: float
    speech_seconds: float | None
    start: datetime        # local time
    end: datetime
    created_at: datetime   # tracks.created_at, local time
    room_created_at: datetime


def load_env_file(path: str) -> None:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def parse_window(text: str) -> tuple[time, time]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", text.strip())
    if not match:
        raise argparse.ArgumentTypeError("expected HH:MM-HH:MM, e.g. 08:00-18:00")
    h1, m1, h2, m2 = map(int, match.groups())
    start, end = time(h1, m1), time(h2, m2)
    if end <= start:
        raise argparse.ArgumentTypeError("window must not cross midnight and end must be after start")
    return start, end


def as_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def fetch(args, tz: ZoneInfo) -> list[Track]:
    import psycopg

    # Generous UTC bounds; the exact local-date filter happens in Python.
    lower = datetime.combine(args.date_from, time.min, tz) - timedelta(days=1)
    upper = datetime.combine(args.date_to, time.max, tz) + timedelta(days=1)
    sql = """SELECT t.id, t.track_id, t.room_ref_id::text, r.room_name, t.participant_identity, t.status,
                    t.audio_info, t.created_at, coalesce(r.created_at, t.created_at)
             FROM tracks t JOIN rooms r ON r.id = t.room_ref_id
             WHERE coalesce(r.created_at, t.created_at) BETWEEN %s AND %s"""
    with psycopg.connect(host=os.environ["POSTGRES_HOST"], port=os.environ.get("POSTGRES_PORT", "5432"),
                         user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
                         dbname=os.environ["POSTGRES_DATABASE"],
                         options="-c default_transaction_read_only=on") as conn, conn.cursor() as cur:
        cur.execute(sql, (lower, upper))
        rows = cur.fetchall()

    tracks = []
    for rec_id, sfu_track_id, room_id, room_name, identity, status, info, created, room_created in rows:
        info = info or {}
        duration = as_float(info.get("duration_sec")) or 0.0
        start_ns, end_ns = as_float(info.get("started_at_ns")), as_float(info.get("ended_at_ns"))
        if start_ns:
            start = datetime.fromtimestamp(start_ns / 1e9, timezone.utc)
            end = datetime.fromtimestamp(end_ns / 1e9, timezone.utc) if end_ns and end_ns > start_ns \
                else start + timedelta(seconds=duration)
        else:
            start = created
            end = created + timedelta(seconds=duration)
        tracks.append(Track(
            track_id=rec_id, sfu_track_id=sfu_track_id or "", room_id=room_id, room_name=room_name or "",
            participant_id=identity or "", status=status or "", object_key=info.get("derivative_object_key") or "",
            duration=duration, speech_seconds=as_float(info.get("duration_after_vad_sec")),
            start=start.astimezone(tz), end=end.astimezone(tz), created_at=created.astimezone(tz),
            room_created_at=room_created.astimezone(tz)))
    return tracks


def select(tracks: list[Track], args) -> tuple[list[Track], dict[str, list[Track]]]:
    """Returns (kept, dropped by reason). Pure function so it can be tested without a database."""
    dropped: dict[str, list[Track]] = defaultdict(list)

    def keep(items: list[Track], reason: str, ok) -> list[Track]:
        result = []
        for track in items:
            (result if ok(track) else dropped[reason]).append(track)
        return result

    def drop_rooms(items: list[Track], reason: str, bad_rooms: set[str]) -> list[Track]:
        return keep(items, reason, lambda t: t.room_id not in bad_rooms)

    items = keep(tracks, "outside date range",
                 lambda t: args.date_from <= t.room_created_at.date() <= args.date_to)
    if args.weekdays_only:
        items = keep(items, "weekend", lambda t: t.room_created_at.weekday() in WEEKDAYS)

    if args.time_window:
        w_start, w_end = args.time_window

        def inside(t: Track) -> bool:
            return (t.start.date() == t.end.date()
                    and w_start <= t.start.time() and t.end.time() <= w_end)
        if args.per_track:
            items = keep(items, "outside time window (track)", inside)
        else:
            # Judge on every track of the room, including agent / unfinished ones, before other filters.
            bad = {t.room_id for t in items if not inside(t)}
            items = drop_rooms(items, "outside time window (whole room)", bad)

    agents = set(args.exclude_identity)
    items = keep(items, "agent track",
                 lambda t: t.sfu_track_id != AGENT_TTS_TRACK_ID and t.participant_id not in agents)
    items = keep(items, "STT not completed", lambda t: t.status == "completed")
    items = keep(items, "no .ogg derivative", lambda t: bool(t.object_key))
    items = keep(items, "too short", lambda t: t.duration >= args.min_seconds)
    items = keep(items, "too long", lambda t: t.duration <= args.max_seconds)
    items = keep(items, "too little speech",
                 lambda t: t.speech_seconds is None or t.speech_seconds >= args.min_speech_seconds)

    if args.exclude_room_name:
        pattern = re.compile(args.exclude_room_name, re.IGNORECASE)
        items = keep(items, "room name excluded", lambda t: not pattern.search(t.room_name))
    speakers: dict[str, set[str]] = defaultdict(set)
    for track in items:
        speakers[track.room_id].add(track.participant_id)
    items = drop_rooms(items, f"room has < {args.min_room_speakers} speakers",
                       {room for room, people in speakers.items() if len(people) < args.min_room_speakers})

    if args.max_hours_per_speaker:
        budget = args.max_hours_per_speaker * 3600
        used: dict[str, float] = defaultdict(float)
        result = []
        for track in sorted(items, key=lambda t: (t.room_created_at, t.duration)):
            if used[track.participant_id] + track.duration > budget:
                dropped["speaker over budget"].append(track)
                continue
            used[track.participant_id] += track.duration
            result.append(track)
        items = result
    if args.max_hours:
        # Keep whole rooms, most recent first, until the total budget is used.
        rooms: dict[str, list[Track]] = defaultdict(list)
        for track in items:
            rooms[track.room_id].append(track)
        order = sorted(rooms, key=lambda r: rooms[r][0].room_created_at, reverse=True)
        total, result = 0.0, []
        for room in order:
            if total >= args.max_hours * 3600:
                dropped["over --max-hours"].extend(rooms[room])
                continue
            result += rooms[room]
            total += sum(t.duration for t in rooms[room])
        items = result
    return sorted(items, key=lambda t: (t.room_created_at, t.room_id, t.start)), dropped


def presigner(args):
    from minio import Minio

    endpoint = args.url_endpoint or os.environ["MINIO_ENDPOINT"]
    secure = endpoint.startswith("https://") or os.environ.get("MINIO_SECURE", "false").lower() == "true"
    endpoint = re.sub(r"^https?://", "", endpoint).rstrip("/")
    # The signature covers the host, so sign with the address the team will actually use.
    # Passing the region avoids a network round-trip to look it up.
    client = Minio(endpoint, access_key=os.environ["MINIO_ACCESS_KEY"],
                   secret_key=os.environ.get("MINIO_SECRET_KEY") or os.environ["MINIO_SECRET"],
                   secure=secure, region=os.environ.get("MINIO_REGION", "us-east-1"))
    bucket = os.environ["MINIO_BUCKET"]
    expires = timedelta(days=args.expires_days)
    return lambda key: client.presigned_get_object(bucket, key, expires=expires)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env-file")
    parser.add_argument("--date-from", type=date.fromisoformat, required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--date-to", type=date.fromisoformat, required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--time-window", type=parse_window, help="allowed local hours, e.g. 08:00-18:00")
    parser.add_argument("--per-track", action="store_true",
                        help="apply the time window per track instead of dropping the whole room")
    parser.add_argument("--weekdays-only", action="store_true")
    parser.add_argument("--timezone", default="Asia/Ho_Chi_Minh")
    parser.add_argument("--exclude-identity", nargs="*", default=[], help="user ids to drop (e.g. the bot's)")
    parser.add_argument("--exclude-room-name", help="regex on room name, e.g. 'test|demo'")
    parser.add_argument("--min-seconds", type=float, default=60)
    parser.add_argument("--max-seconds", type=float, default=3 * 3600)
    parser.add_argument("--min-speech-seconds", type=float, default=20)
    parser.add_argument("--min-room-speakers", type=int, default=2)
    parser.add_argument("--max-hours-per-speaker", type=float, default=0, help="0 = no cap")
    parser.add_argument("--max-hours", type=float, default=0, help="0 = no cap")
    parser.add_argument("--url-endpoint", help="MinIO address the team can reach, if not MINIO_ENDPOINT")
    parser.add_argument("--expires-days", type=int, default=7)
    parser.add_argument("--out-dir", default=str(ROOT / "exports"))
    args = parser.parse_args()
    if args.date_to < args.date_from:
        parser.error("--date-to is before --date-from")
    if not 1 <= args.expires_days <= MAX_PRESIGN_DAYS:
        parser.error(f"--expires-days must be 1..{MAX_PRESIGN_DAYS}")
    if args.env_file:
        load_env_file(args.env_file)

    tz = ZoneInfo(args.timezone)
    tracks = fetch(args, tz)
    kept, dropped = select(tracks, args)

    print(f"read {len(tracks)} tracks from {len({t.room_id for t in tracks})} rooms")
    for reason, items in dropped.items():
        print(f"  dropped {len(items):5d} tracks / {sum(t.duration for t in items) / 3600:6.1f} h : {reason}")
    hours = sum(t.duration for t in kept) / 3600
    print(f"kept {len(kept)} tracks, {len({t.room_id for t in kept})} rooms, "
          f"{len({t.participant_id for t in kept})} speakers, {hours:.1f} h")
    if not kept:
        return 0

    sign = presigner(args)
    expires_at = (datetime.now(tz) + timedelta(days=args.expires_days)).isoformat(timespec="minutes")
    rows = [{
        "track_id": t.track_id,
        "room_id": t.room_id,
        "participant_id": t.participant_id,
        "url": sign(t.object_key),
        "created_at": t.created_at.isoformat(timespec="seconds"),
        "started_at": t.start.isoformat(timespec="seconds"),
        "duration_sec": round(t.duration, 1),
        "url_expires_at": expires_at,
    } for t in kept]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"tracks_{args.date_from}_{args.date_to}"
    with stem.with_suffix(".csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with stem.with_suffix(".jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {stem}.csv and .jsonl (links valid until {expires_at})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
