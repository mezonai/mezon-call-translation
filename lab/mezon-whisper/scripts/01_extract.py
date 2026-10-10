"""Pick Mezon call recordings from PostgreSQL and fetch them from MinIO (plan 5.3 steps 1-3).

Two subcommands:
  inspect   metadata only, read-only: track counts by status / source / month,
            audio_info keys, hours per speaker. Run this first to choose --source.
  download  select whole rooms (latest rooms first, then random) up to --hours,
            fetch the raw .pcm objects, write data/raw/<track>.wav and
            data/manifests/tracks.jsonl. Needs --confirm-consent (plan 13).

Whole rooms are kept together because the split is room-disjoint (plan 8) and the
ordering metric (plan 7.2) needs every track of a room.

Tracks are headerless PCM16 mono 16 kHz, the exact input of the STT service
(`_pcm16_bytes_to_float32`); the object key is `tracks.audio_info.filename`.

Connection settings come from the environment, same names as the services:
  POSTGRES_HOST POSTGRES_PORT POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DATABASE
  MINIO_ENDPOINT MINIO_ACCESS_KEY MINIO_SECRET_KEY MINIO_BUCKET MINIO_SECURE
or from `--env-file path/to/.env`. Use a read-only PostgreSQL user.

Usage:
    python scripts/01_extract.py --env-file .env inspect
    python scripts/01_extract.py --env-file .env download --source <value> --hours 50 --confirm-consent
"""

import argparse
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
SR = 16000

DURATION = "NULLIF(t.audio_info->>'duration_sec', '')::float"
VAD_DURATION = "NULLIF(t.audio_info->>'duration_after_vad_sec', '')::float"


def load_env_file(path: str) -> None:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def connect():
    import psycopg

    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DATABASE"],
        options="-c default_transaction_read_only=on",
    )


def table(cursor, title: str, sql: str, params: tuple = ()) -> None:
    cursor.execute(sql, params or None)
    print(f"\n-- {title}")
    print(" | ".join(column.name for column in cursor.description))
    for row in cursor.fetchall():
        print(" | ".join("" if value is None else (f"{value:.2f}" if isinstance(value, float) else str(value))
                         for value in row))


def inspect(_args) -> int:
    with connect() as conn, conn.cursor() as cur:
        table(cur, "tracks by status", "SELECT status, count(*) FROM tracks t GROUP BY 1 ORDER BY 2 DESC")
        table(cur, "audio_info keys (latest 5000 tracks)",
              """SELECT key, count(*) FROM (SELECT audio_info FROM tracks WHERE audio_info IS NOT NULL
                 ORDER BY created_at DESC NULLS LAST LIMIT 5000) s, jsonb_object_keys(s.audio_info) AS key
                 GROUP BY 1 ORDER BY 2 DESC""")
        table(cur, "completed tracks by source and file extension",
              f"""SELECT t.audio_info->>'source' AS source,
                         substring(t.audio_info->>'filename' from '\\.([A-Za-z0-9]+)$') AS ext,
                         count(*) AS tracks, sum({DURATION}) / 3600 AS hours,
                         sum({VAD_DURATION}) / 3600 AS hours_after_vad
                  FROM tracks t WHERE t.status = 'completed' GROUP BY 1, 2 ORDER BY 3 DESC""")
        table(cur, "completed tracks by month",
              f"""SELECT to_char(date_trunc('month', coalesce(r.created_at, t.created_at)), 'YYYY-MM') AS month,
                         count(DISTINCT t.room_ref_id) AS rooms, count(*) AS tracks,
                         count(DISTINCT t.participant_identity) AS speakers, sum({DURATION}) / 3600 AS hours
                  FROM tracks t LEFT JOIN rooms r ON r.id = t.room_ref_id
                  WHERE t.status = 'completed' GROUP BY 1 ORDER BY 1 DESC LIMIT 12""")
        table(cur, "top 15 speakers by hours (completed)",
              f"""SELECT t.participant_identity AS speaker, count(*) AS tracks, sum({DURATION}) / 3600 AS hours
                  FROM tracks t WHERE t.status = 'completed' GROUP BY 1 ORDER BY 3 DESC NULLS LAST LIMIT 15""")
        table(cur, "3 latest completed tracks",
              """SELECT t.id, t.track_id, t.room_ref_id, t.audio_info FROM tracks t
                 WHERE t.status = 'completed' ORDER BY t.created_at DESC NULLS LAST LIMIT 3""")
    print("\nNext: pick the microphone `source` value above and run `download --source <value>`.")
    return 0


def select_rooms(rows: list[dict], hours: float, recent_rooms: int, seed: int) -> list[dict]:
    rooms: dict[str, list[dict]] = {}
    for row in rows:
        rooms.setdefault(row["room_id"], []).append(row)
    by_recency = sorted(rooms, key=lambda room: max(r["date"] for r in rooms[room]), reverse=True)
    order = by_recency[:recent_rooms]
    rest = by_recency[recent_rooms:]
    random.Random(seed).shuffle(rest)
    picked, total = [], 0.0
    for room in order + rest:
        if total >= hours * 3600:
            break
        picked += rooms[room]
        total += sum(r["duration"] for r in rooms[room])
    return picked


def fetch(client, bucket: str, row: dict) -> str:
    wav = ROOT / row["audio"]
    if wav.exists():
        return "cached"
    tmp = wav.with_suffix(".pcm.tmp")
    client.fget_object(bucket, row["object_key"], str(tmp))
    raw = tmp.read_bytes()
    tmp.unlink()
    samples = np.frombuffer(raw[: len(raw) - len(raw) % 2], dtype="<i2")
    seconds = len(samples) / SR
    # A wrong sample-rate/channel assumption shows up as a duration mismatch.
    if row["duration"] and abs(seconds - row["duration"]) > max(2.0, 0.05 * row["duration"]):
        return f"DURATION MISMATCH: file {seconds:.1f}s vs metadata {row['duration']:.1f}s (not written)"
    sf.write(str(wav), samples, SR, subtype="PCM_16")
    return "ok"


def download(args) -> int:
    if not (args.confirm_consent or args.dry_run):
        print("Refusing to download call audio without --confirm-consent "
              "(plan 13: consent / data policy must be confirmed first). "
              "--dry-run only reads metadata and needs no confirmation.", file=sys.stderr)
        return 2
    from minio import Minio

    sql = f"""SELECT t.id, t.track_id, t.room_ref_id::text, r.room_name, t.participant_identity,
                     t.audio_info->>'filename', t.audio_info->>'source', {DURATION},
                     t.audio_info->>'started_at_ns', t.audio_info->>'ended_at_ns',
                     coalesce(r.created_at, t.created_at)
              FROM tracks t JOIN rooms r ON r.id = t.room_ref_id
              WHERE t.status = 'completed'
                AND t.audio_info->>'source' = %s
                AND t.audio_info->>'filename' LIKE '%%.pcm'
                AND {DURATION} BETWEEN %s AND %s
                AND coalesce({VAD_DURATION}, %s) >= %s
                AND coalesce(r.created_at, t.created_at) >= %s"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (args.source, args.min_seconds, args.max_seconds,
                          args.min_speech_seconds, args.min_speech_seconds, args.since))
        rows = [{
            "track_id": row[0], "sfu_track_id": row[1], "room_id": row[2], "room_name": row[3],
            "speaker": row[4], "object_key": row[5], "source": row[6], "duration": row[7],
            "started_at_ns": row[8], "ended_at_ns": row[9], "date": row[10].date().isoformat(),
            "audio": f"data/raw/{re.sub(r'[^A-Za-z0-9_.-]', '_', row[0])}.wav",
        } for row in cur.fetchall()]
    print(f"{len(rows)} eligible tracks, {sum(r['duration'] for r in rows) / 3600:.1f} h")
    picked = select_rooms(rows, args.hours, args.recent_rooms, args.seed)
    print(f"picked {len(picked)} tracks from {len({r['room_id'] for r in picked})} rooms, "
          f"{sum(r['duration'] for r in picked) / 3600:.1f} h, {len({r['speaker'] for r in picked})} speakers")
    if args.dry_run:
        return 0

    (ROOT / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "manifests").mkdir(parents=True, exist_ok=True)
    client = Minio(os.environ["MINIO_ENDPOINT"], access_key=os.environ["MINIO_ACCESS_KEY"],
                   secret_key=os.environ["MINIO_SECRET_KEY"],
                   secure=os.environ.get("MINIO_SECURE", "false").lower() == "true")
    bucket = os.environ["MINIO_BUCKET"]

    def safe_fetch(row: dict) -> str:
        try:
            return fetch(client, bucket, row)
        except Exception as error:  # one bad object must not stop the batch
            return f"FAILED: {type(error).__name__}: {error!r}"

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(safe_fetch, picked))

    kept = [row for row, result in zip(picked, results) if result in ("ok", "cached")]
    for row, result in zip(picked, results):
        if result not in ("ok", "cached"):
            print(f"  {row['track_id']}: {result}")
    manifest = ROOT / "data" / "manifests" / "tracks.jsonl"
    with manifest.open("w", encoding="utf-8") as out:
        for row in kept:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

    hours_by_speaker: dict[str, float] = {}
    for row in kept:
        hours_by_speaker[row["speaker"]] = hours_by_speaker.get(row["speaker"], 0.0) + row["duration"] / 3600
    total = sum(hours_by_speaker.values())
    print(f"\nwrote {manifest}: {len(kept)} tracks, {total:.1f} h, {len({r['room_id'] for r in kept})} rooms")
    print("top speakers (share of hours):")
    for speaker, hours in sorted(hours_by_speaker.items(), key=lambda item: -item[1])[:10]:
        print(f"  {speaker}: {hours:.1f} h ({hours / total:.0%})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect")
    dl = commands.add_parser("download")
    dl.add_argument("--source", required=True, help="audio_info.source value of microphone tracks (see inspect)")
    dl.add_argument("--hours", type=float, default=50, help="raw audio hours to fetch")
    dl.add_argument("--recent-rooms", type=int, default=15, help="always include the N latest rooms (test/dev pool)")
    dl.add_argument("--since", default="2000-01-01", help="only rooms created on or after this date")
    dl.add_argument("--min-seconds", type=float, default=120)
    dl.add_argument("--max-seconds", type=float, default=3 * 3600)
    dl.add_argument("--min-speech-seconds", type=float, default=30,
                    help="skip tracks whose duration_after_vad_sec is known and below this")
    dl.add_argument("--workers", type=int, default=4)
    dl.add_argument("--seed", type=int, default=0)
    dl.add_argument("--dry-run", action="store_true", help="select and report, download nothing")
    dl.add_argument("--confirm-consent", action="store_true")
    args = parser.parse_args()
    if args.env_file:
        load_env_file(args.env_file)
    return inspect(args) if args.command == "inspect" else download(args)


if __name__ == "__main__":
    sys.exit(main())
