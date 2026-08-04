# audio-processing-service

Non-critical, async worker: transcodes a call's raw PCM capture (written by
`record-service`) into a client-playable OGG/Opus derivative and reports the
result back to orchestrator. Scales independently from `record-service`
(CPU-bound transcode vs. I/O-bound capture) -- see `../PLAN.md` section 4.

Design rationale lives in `../PLAN.md` (decision **D28** covers this
service's own design choices; D17/D19/D20 cover the surrounding pipeline
this plugs into) -- this README is "what the code does and how to read it".

## End-to-end flow

```
record-service                          [PLAN.md D5/D6]
   │  raw PCM16 uploaded to MinIO, reports recording.completed
   ▼
orchestrator_service
   services/audio_derivative_service.py  → XADD audio_derivative:stream
   ▼
audio-processing-service  (this repo)
   services/redis_derivative_queue_service.py   (XREADGROUP, consumer group)
   → services/derivative_processor.py
       1. infra/storage.py            download raw .pcm from MinIO
       2. infra/transcoder.py         ffmpeg: raw PCM16 → OGG/Opus
       3. infra/naming.py             .pcm → .ogg (same bucket, same prefix)
       4. infra/storage.py            upload derivative back to MinIO
       5. infra/event_reporter.py     POST derivative.completed/.failed
   ▼
orchestrator_service
   POST /api/v2/recordings/events (recording_events_api.py)
   → services/recording_event_service.py::handle_derivative_event
   → tracks.derivative_status, tracks.audio_info.derivative_object_key
   → check_and_notify_room_recordings_ready (room_record_done, PLAN.md D19)
```

This service never talks to Postgres or LiveKit. Its only network
dependencies are Redis (job queue), S3/MinIO (data plane), and one HTTP POST
to orchestrator (control plane).

## Why no Ports & Adapters here (unlike record-service)

record-service is hexagonal because it's on the critical path and has a
concrete future adapter swap planned (`infra/sfu/` once LiveKit's SFU is
replaced, PLAN.md D3/D4). Neither applies here: this service has one job
(download → transcode → upload → report), isn't critical (PLAN.md D7: fail
→ retry, no risk to the raw capture already safe on MinIO), and has no
planned adapter swap. Kept flatter, closer to `stt_service`'s shape, which
is the service this one's Redis Stream consumer plumbing was copied from
(see below).

## Why the Redis Stream consumer code looks copy-pasted (it is)

`infra/redis/redis_stream_service.py`, `infra/redis/connection_pool.py`, and
`services/redis_derivative_queue_service.py` are adapted copies of
`stt_service/service/redis/redis_stream_service.py` /
`redis_transcription_queue_service.py` (already duplicated once, between
`stt_service` and `orchestrator_service` -- same convention continued here).
**Reviewed but deliberately left as-is** (PLAN.md D28 point 3), including 2
known bugs, both self-healing (no data loss, ~60-90s recovery delay, not
worth the risk of touching code shared with two other services right now):

1. `RedisStreamService.release_my_pending_tasks()` — the `XCLAIM ... force=True`
   call meant to release a task "immediately" actually **resets its idle-time
   counter**, so the task can't be auto-claimed by another consumer until
   `claim_min_idle_time_ms` (60s default) elapses again — the opposite of
   "immediately".
2. `RedisDerivativeQueueService._process_task()`'s "already processing,
   skip" branch returns without ack'ing or rejecting the message — it just
   sits in the PEL until the next orphan-recovery pass (~30-90s) claims it.

If either needs fixing, fix all 3 copies together (same "sync by hand" note
PLAN.md D15 already has for the duplicated `.proto` files).

## Format / naming decisions (PLAN.md D28)

- **sample_rate=16000, channels=1 are hardcoded** (`config.py::TranscodeConfig`),
  not read per-track from DB metadata — matches record-service's fixed
  capture format (D6). Simpler on purpose; revisit only if record-service's
  capture format ever becomes variable.
- **Derivative reuses record-service's bucket**, only the object key suffix
  changes (`infra/naming.py::build_derivative_key`: `.pcm` → `.ogg`, same
  path/prefix otherwise) — no new bucket/prefix to provision.
- **ffmpeg command line** (`infra/transcoder.py`) is byte-for-byte the same
  encoder settings the old LiveKit-Egress-era pipeline used (recovered from
  `agents`' deleted `audio_recording_manager.py` via git history) — so the
  output format is unchanged from what client/bot already integrate against
  (PLAN.md D20).

## Failure reporting is retry-count-aware

A transient failure (MinIO blip, ffmpeg hiccup) on an early attempt must
**not** report `derivative.failed` to orchestrator — that would let
`check_and_notify_room_recordings_ready` treat the track as done-but-failed
and potentially fire `room_record_done` before a later retry gets a chance
to succeed. `services/derivative_processor.py` only reports
`derivative.failed` on the attempt `RedisStreamService.reject()` is about to
send to the dead-letter queue (`task.retry_count >= config.redis.max_retries`,
default 3 → 4th attempt). Earlier attempts fail silently — no event sent,
the track just stays `derivative_status='pending'` in orchestrator's DB —
and retry.

## Configuration

All env-driven, see `src/audio_processing_service/config.py` for defaults:

| Group | Vars |
|---|---|
| Redis | `REDIS_HOST` (localhost), `REDIS_PORT` (6379), `REDIS_PASSWORD`, `REDIS_DB` (0), `REDIS_CLAIM_MIN_IDLE_TIME_MS` (60000), `REDIS_BLOCK_TIMEOUT_MS` (5000), `REDIS_MAX_RETRIES` (3), `REDIS_MAX_CONNECTIONS` (10), `REDIS_SOCKET_TIMEOUT` (30), `REDIS_SOCKET_CONNECT_TIMEOUT` (10), `REDIS_HEARTBEAT_INTERVAL_SEC` (10), `REDIS_WORKER_TIMEOUT_SEC` (30) |
| MinIO/S3 | `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, `MINIO_REGION`, `MINIO_SECURE`, `MINIO_FORCE_PATH_STYLE` -- same bucket as record-service |
| Transcode | `FFMPEG_PATH` (ffmpeg), `TRANSCODE_SAMPLE_RATE` (16000), `TRANSCODE_CHANNELS` (1), `TRANSCODE_OPUS_BITRATE_KBPS` (32), `TRANSCODE_FFMPEG_TIMEOUT_SECONDS` (300) |
| Orchestrator | `ORCHESTRATOR_BASE_URL`, `RECORDING_EVENTS_PATH` (/api/v2/recordings/events), `ORCHESTRATOR_TIMEOUT_SECONDS` (5), `ORCHESTRATOR_API_KEY` |
| Logging | `LOG_LEVEL` (INFO) |

`ffmpeg` (with `libopus`) must be installed on the host -- it's invoked as a
subprocess, not a Python dependency. `sudo apt-get install ffmpeg`.

## Code layout

```
src/audio_processing_service/
  config.py                          env-driven config (RedisConfig, MinIOConfig, TranscodeConfig, OrchestratorConfig)
  models/
    stream_base.py                   generic Redis Stream task types (copied verbatim from stt_service)
    derivative_task.py               AudioDerivativeStreamTask -- parses orchestrator's producer wire format
  infra/
    redis/connection_pool.py         shared Redis connection pool (adapted from stt_service)
    redis/redis_stream_service.py    generic XREADGROUP/XACK/XAUTOCLAIM consumer (adapted from stt_service)
    storage.py                       MinIO download/upload (boto3 + asyncio.to_thread)
    transcoder.py                    ffmpeg subprocess: raw PCM -> OGG/Opus
    naming.py                        derivative object key (.pcm -> .ogg)
    event_reporter.py                POST derivative.completed/.failed to orchestrator
  services/
    redis_derivative_queue_service.py  consumer loop + orphan recovery (adapted from stt_service)
    derivative_processor.py            orchestrates 1 job: download -> transcode -> upload -> report
  bootstrap.py                       wires config + adapters + processor + queue service
  main.py                            entrypoint: connect, run until SIGTERM/SIGINT
```

## Running locally

```bash
# from audio-ingestion/audio-processing-service/
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest   # fakes for MinIO/HTTP (tests/fakes.py), real ffmpeg subprocess (skipped if not installed)

# run against real Redis/MinIO/orchestrator (needs env vars, see Configuration above)
python -m audio_processing_service.main
```

There's no `dev_server_with_fakes.py`-style standalone script here (unlike
record-service) -- this service has no listening port to smoke-test against;
its only external trigger is a real `audio_derivative:stream` message, which
means a real Redis is the natural way to exercise it end-to-end. The test
suite's fakes-based tests cover the processor logic without that
dependency.

## Running as a deployed service (dev/prod)

No Docker in dev/prod (PLAN.md D14) -- systemd on the host instead. Full
install steps, template unit file, and env file example:
**`deploy/systemd/README.md`**.

## Tests

`tests/` uses fakes (`tests/fakes.py`) for MinIO/orchestrator HTTP, but runs
**real ffmpeg** (skipped automatically if `ffmpeg` isn't on `PATH`) --
`test_transcoder.py` verifies the actual command line produces a valid,
`ffprobe`-parseable Opus stream from headerless raw PCM, and
`test_derivative_processor.py` runs the full download→transcode→upload→report
path end-to-end including the retry-count-aware failure-reporting rule
above.
