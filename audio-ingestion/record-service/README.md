# record-service

Critical-path service that captures raw call audio and durably uploads it to
S3/MinIO, replacing LiveKit Egress. Built Ports & Adapters style so the
capture source (today: gRPC from `agents`) can later be swapped for direct
SFU/RTP capture without touching the business logic.

Design rationale for every decision below lives in `../PLAN.md` (decisions
D1-D26) — this README is "what the code does and how to read it", the plan is
"why it's built this way".

## End-to-end flow

```
LiveKit room
   │  (agent subscribes to a track, same lifecycle as STT)
   ▼
Architect_MultiClient_Server/agents
   EventHandlers._start_record_forwarding()          [event_handlers.py]
   → independent rtc.AudioStream.from_track() subscription
   → RecordForwarder (record_service_client.py)
   │  gRPC bidi stream: StreamAudio(stream AudioChunk) → stream RecordingAck
   ▼
record-service  (this repo)
   infra/grpc/ingest_server.py  (RecordingIngestServicer)
   → application/start_recording.py   (open S3 multipart upload)
   → application/append_audio.py      (buffer + flush parts, per frame)
   → application/stop_recording.py    (flush tail, complete/abort upload)
   → application/report_event.py      (HTTP POST recording.started/completed/failed)
   ▼
Architect_MultiClient_Server/orchestrator_service
   POST /api/v2/recordings/events     (recording_events_api.py)
   → services/recording_event_service.py
   → tracks.derivative_status, rooms.record_notified_at (Postgres)
   → audio_derivative:stream (Redis Stream, for future audio-processing-service)
   → SSE room_record_done notice (once per room, bare notice, no file paths)
```

record-service itself never talks to LiveKit, Postgres, or Redis. Its only
two network dependencies are S3/MinIO (data plane) and one HTTP POST to
orchestrator (control plane, best-effort/async).

## Why gRPC bidi streaming, not client-streaming

`proto/recording.proto` defines `StreamAudio(stream AudioChunk) returns
(stream RecordingAck)`. Bidi (not plain client-streaming) so record-service
can `accepted`/`rejected` the session on the very first message, instead of
the agent only finding out something was wrong after the whole call ended.

```protobuf
message AudioChunk {
  oneof payload {
    SessionStart start = 1;   // first message: (room_id, track_id) key + format
    bytes pcm = 2;            // raw PCM16, headerless, no encode/decode (D6)
    DroppedFrames dropped = 3; // agent-side backpressure count, annotation only
  }
}
message RecordingAck {
  string status = 1;  // accepted | rejected | completed
  string object_key = 2;
  string error = 3;
}
```

One `RecordingSession` = one `(room_id, track_id)` = one S3 multipart upload.
`session_id` is always `"{room_id}:{track_id}"` (`RecordingSession.make_session_id`).

## Request lifecycle, file by file

1. **`infra/grpc/ingest_server.py`** — `RecordingIngestServicer.StreamAudio`
   is the only place that knows frames arrive over gRPC. It reads
   `SessionStart` once, then `pcm`/`dropped` chunks in a loop, and drives
   exactly three application use cases. Graceful vs. abrupt end is detected
   structurally: the iterator finishing on its own = agent closed on purpose;
   an exception while iterating = the connection broke.

2. **`application/start_recording.py`** (`StartRecording`) — opens a new S3
   multipart upload (`create_multipart_upload`) and registers an
   `ActiveSession` in the in-memory `SessionRegistry`, or **resumes** an
   existing session if one is sitting in `GRACE_WAIT` for the same
   `session_id` (reconnect within the grace window — see Recovery tiers
   below). `SessionRegistry.creation_lock(session_id)` serializes the whole
   decision so two concurrent starts for the same track can't both open
   separate uploads — scoped per `session_id` (not a single global lock), so
   starting one track never blocks starting an unrelated one (D24). Once a
   genuinely new session is registered, fires `recording.started` to
   orchestrator fire-and-forget (D26) — not awaited, so a slow/unreachable
   orchestrator never delays accepting audio.

3. **`application/append_audio.py`** (`AppendAudio`) — dumb pass-through by
   design (D6: no decode/re-encode). Buffers PCM bytes per session and
   flushes a part to S3 (`upload_part`, retried via `with_retry`) every time
   the buffer crosses `part_size_bytes` (default 8 MiB, S3's multipart
   minimum is 5 MiB). Also folds in agent-reported `dropped_frame_count` and
   raises a `QualityAnnotation` (never discards data, never restarts the
   session) if the cumulative drop rate crosses
   `drop_rate_warning_threshold`.

4. **`application/stop_recording.py`** (`StopRecording`) — on graceful close,
   finalizes immediately. On abrupt disconnect, parks the session in
   `GRACE_WAIT` and starts a `grace_period_seconds` (default 45s) timer
   before finalizing best-effort — see Recovery tiers. `_finalize` flushes
   whatever's left in the buffer as a final part, then calls
   `finalize.complete_or_abort` and reports `recording.completed` /
   `recording.failed` to orchestrator.

5. **`application/finalize.py`** (`complete_or_abort`) — shared by both the
   live-session path (`stop_recording.py`) and the crash-recovery path
   (`recover_orphaned_sessions.py`). No parts uploaded → abort the multipart
   upload. Otherwise complete it; if the resulting byte count looks thin
   relative to elapsed time × nominal PCM byte rate (`byte_rate_tolerance`),
   flags a `low_byte_rate` annotation rather than silently reporting a clean
   `completed`.

6. **`application/report_event.py`** (`ReportEvent`) — POSTs the event to
   orchestrator with its own retry policy. If every attempt fails, the
   session is persisted with `reported=False` instead of the event being
   dropped — the reconciler (`recover_orphaned_sessions.py`) picks it up
   later.

## Recovery tiers (why three separate mechanisms)

Raw audio capture is the critical path, so failure handling is deliberately
layered (`PLAN.md` D5):

| Tier | Failure mode | Mechanism |
|---|---|---|
| 1 | Bad `SessionStart` (bad room/track) | Reject on the first `RecordingAck`, before any upload starts |
| 2 | gRPC stream drops abruptly (network blip) | `GRACE_WAIT` + `grace_period_seconds` timer; a `SessionStart` with the same `session_id` within the window resumes the same `upload_id` instead of opening a new file |
| 3 | Process crash / restart | `application/recover_orphaned_sessions.py`, run once at startup (`main.py::serve`) and on a `reconciler.interval_seconds` timer. Reads durable per-session JSON files off disk (`infra/state/file_session_state_repo.py`) and either finalizes a session a dead process left mid-upload, or retries reporting an already-terminal one that never made it to orchestrator |

Tiers 2 and 3 share one subtlety worth reading if you're reviewing
correctness: a resume (tier 2, in `start_recording.py`) and a grace-timeout
finalize (tier 2's own timer, in `stop_recording.py::_grace_timeout`) can
race each other. Both sides coordinate through the **same** `active.lock`
object on the `ActiveSession`, and each re-checks status after acquiring the
lock — so exactly one of "resume" or "finalize" wins, never both. Tier 3 is
similarly guarded: the reconciler skips any `session_id` still present in the
in-process `SessionRegistry`, so it never touches a session a live task
already owns.

Local per-session state (`RECORD_STATE_DIR`, default
`/data/record-service/state`) is *not* the source of truth for audio bytes —
every uploaded part already durably exists in S3/MinIO via the multipart
upload. It only remembers `(upload_id, parts, status)` so a restarted process
can pick a session back up without re-reading any audio. See the module
docstring in `infra/state/file_session_state_repo.py` for the one known
limitation (ephemeral filesystem across pod reschedule).

## Object storage layout

`infra/naming.py::build_object_key` computes the key locally (never asks
orchestrator), so opening a recording never has a synchronous dependency on
orchestrator being reachable:

```
{room_id}/{participant_identity}-{source}-audio-{random_hex}.pcm
```

Raw headerless PCM16, matching D6 (no encode on the critical path — that's
`audio-processing-service`'s job, not yet built, see `PLAN.md` Phase 5).

## Talking to orchestrator

One outbound call: `POST {ORCHESTRATOR_BASE_URL}{RECORDING_EVENTS_PATH}`
(default `/api/v2/recordings/events`), Bearer-authenticated via
`ORCHESTRATOR_API_KEY` if set. Payload is a full self-describing session
snapshot (see `infra/reporting/http_event_reporter.py::_to_payload`), same
shape for all three events (`event` field is what orchestrator dispatches
on).

`room_id` throughout this file (`SessionStart.room_id`, `session_id`, the S3
object key prefix) is whatever the agent sent at `StreamAudio` start --
record-service treats it as an opaque string, no validation, no assumption
about its shape. As of **PLAN.md D27** the agent sends orchestrator's own
stable room UUID here (captured once at registration, before it even
connects to the LiveKit room), not the LiveKit room name it used to send
(PLAN.md D18) -- this is what lets orchestrator resolve `room_id` on an
incoming event directly (existence check) instead of re-resolving a
LiveKit-room-name-to-id mapping that could have been reassigned to a
different call by the time a late event arrives. record-service itself
needed zero code changes for this -- it was already agnostic to what
`room_id` actually contains.

- `recording.started` — fired once, fire-and-forget, right after the session
  is registered (`start_recording.py`). Orchestrator eagerly creates a
  placeholder `tracks` row (`status`/`derivative_status` = `pending`) off
  this so a still-recording track is visible/accounted-for the whole time
  it's in flight, not just once it finishes — D22 originally skipped this
  event to minimize scope, but that let a still-recording track's room
  finalize (and even fire `room_record_done`) before the track had reported
  anything at all. Reintroduced as **D26** (partial reversal of D22); see
  `PLAN.md` for the full incident. Orchestrator inserts with
  `ON CONFLICT (id) DO NOTHING` specifically because this can arrive *after*
  `recording.completed`/`.failed` for very short recordings — it must never
  clobber an already-terminal row.
- `recording.completed` / `recording.failed` — unchanged, awaited from
  `stop_recording.py`/`recover_orphaned_sessions.py`, upsert the same row via
  `save_track_metadata`.

**Known open gap (D26)**: if a track's placeholder row is created by
`recording.started` and record-service then crashes and loses its local
durable state before ever delivering the terminal event for it (state is
local disk, see the known limitation in
`infra/state/file_session_state_repo.py`), that track's row stays
`derivative_status='pending'` forever — nothing on the orchestrator side
currently times it out. Not yet built; flagged in
`pg_transcript_repository.py::check_and_notify_room_recordings_ready`'s
docstring so it isn't lost.

```json
{
  "event": "recording.completed",
  "recording_id": "room123:track456",
  "room_id": "room123",
  "track_id": "track456",
  "participant_identity": "user789",
  "source": "mic",
  "bucket": "...", "object_key": "...",
  "status": "completed",
  "started_at": 0, "ended_at": 0, "duration_seconds": 0,
  "raw_bytes_received": 0, "dropped_frame_count": 0,
  "quality_annotations": []
}
```

Server errors (5xx) are retried by `report_event.py`; 4xx is treated as a
final rejection (not retried); if retries are exhausted the event is
persisted locally and picked up by the reconciler instead of being lost.

## Configuration

All env-driven, see `src/record_service/config.py` for defaults:

| Group | Vars |
|---|---|
| gRPC server | `RECORD_SERVICE_GRPC_HOST` (0.0.0.0), `RECORD_SERVICE_GRPC_PORT` (50051) |
| MinIO/S3 | `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, `MINIO_REGION`, `MINIO_SECURE`, `MINIO_FORCE_PATH_STYLE` |
| Recording policy | `RECORD_PART_SIZE_MB` (8), `RECORD_MAX_UPLOAD_RETRIES` (3), `RECORD_UPLOAD_RETRY_BASE_DELAY_SECONDS` (0.2), `RECORD_GRACE_PERIOD_SECONDS` (45), `RECORD_BYTE_RATE_TOLERANCE` (0.5), `RECORD_DROP_RATE_WARNING_THRESHOLD` (0.1) |
| Local state | `RECORD_STATE_DIR` (/data/record-service/state) |
| Orchestrator | `ORCHESTRATOR_BASE_URL`, `RECORDING_EVENTS_PATH`, `ORCHESTRATOR_TIMEOUT_SECONDS` (5), `RECORD_MAX_REPORT_RETRIES` (3), `RECORD_REPORT_RETRY_BASE_DELAY_SECONDS` (0.5), `ORCHESTRATOR_API_KEY` |
| Reconciler | `RECORD_RECONCILE_INTERVAL_SECONDS` (30) |
| Logging | `LOG_LEVEL` (INFO) |

On the `agents` side, there is no enabled/disabled switch (D25 — LiveKit
Egress is fully removed, so there's no fallback for a flag to roll back to;
see `Architect_MultiClient_Server/agents/src/config/application_config.py`).
Forwarding is always attempted; `RecordServiceClient.new_forwarder` fails
soft per track if record-service isn't reachable.

## Where the caller (agents) hooks in

`Architect_MultiClient_Server/agents/src/services/record_service_client.py`
(`RecordForwarder`) is the gRPC client counterpart. Deliberately **not**
tied to the realtime-STT-enabled toggle — recording follows track/agent
lifecycle, feeding a separate non-realtime Whisper pipeline that must keep
running independent of whether live STT happens to be on for that room
(`event_handlers.py::_start_record_forwarding`, triggered from
`on_track_subscribed`, with its own `rtc.AudioStream.from_track()`
subscription — not shared with `manage_speaker_transcription`). Forwarding
is strictly non-blocking and best-effort from STT's point of view: a full
queue drops the frame (and reports the drop count) rather than ever
awaiting on record-service.

## Code layout (Ports & Adapters)

```
src/record_service/
  domain/       models.py (RecordingSession, RecordingStatus, ...), ports.py (interfaces), policies.py
  application/  the 6 use cases described above, framework-free
  infra/
    grpc/       ingest_server.py + generated recording_pb2*.py (today's AudioSource adapter)
    storage/    s3_blob_storage.py (BlobStorage adapter, boto3)
    state/      file_session_state_repo.py (SessionStateRepository adapter)
    reporting/  http_event_reporter.py (EventReporter adapter)
  bootstrap.py  the only module that wires concrete adapters into use cases
  main.py       entrypoint: gRPC server + startup reconciliation + reconciler loop
```

`application/*` and `infra/grpc/ingest_server.py` depend only on
`domain/ports.py`. A future `infra/sfu/rtp_ingest.py` (direct RTP capture
once LiveKit's SFU is swapped out, D3) would drive the same three use cases
— `start_recording` / `append_audio` / `stop_recording` — without any
application-layer change.

## Running locally

```bash
# from audio-ingestion/record-service/
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# regenerate recording_pb2*.py after editing proto/recording.proto
# (generated files are committed, not built at deploy time — no build step
# under the systemd deployment target)
./scripts/gen_proto.sh

pytest   # 14 tests, no external services required (fakes in tests/fakes.py)

# run against real MinIO/orchestrator (needs env vars, see Configuration below)
python -m record_service.main

# OR: fully in-memory dev loop, no MinIO/orchestrator needed at all --
# useful for smoke-testing a gRPC client (agent, benchmark script) against
# a real listener without standing up the rest of the stack:
python scripts/dev_server_with_fakes.py [port] [max_wait_seconds]
#   ^ exits automatically after the first recording.completed/.failed event
#   (or max_wait_seconds) and prints a JSON summary -- fine for a quick
#   smoke test, NOT for a sustained run (e.g. the benchmark script below
#   needs a listener that stays up for the whole sweep).
```

## Running as a deployed service (dev/prod)

No Docker in dev/prod (PLAN.md D14) -- systemd on the host instead. Full
install steps, template unit file, and env file examples:
**`deploy/systemd/README.md`**.

## Benchmarking (PLAN.md D13)

```bash
# terminal 1 -- the target (pin to its own core if benchmarking on one
# machine, see the script's own docstring for why):
taskset -c 0 python -m record_service.main

# terminal 2:
taskset -c 1 python scripts/benchmark_concurrency.py --sweep 10,25,50,100 --csv results.csv
```
Sweeps concurrency levels, feeding each simulated session real-cadence
PCM16 silence, sampling the target process's CPU%/RSS to find where one
instance/core starts to strain -- see `scripts/benchmark_concurrency.py`'s
docstring for the full option list and `deploy/systemd/README.md` for how
the result feeds into instance-count capacity planning.

## Tests

`tests/` uses fakes (`tests/fakes.py`) for `BlobStorage`/`EventReporter`, no
mocks-of-mocks. Notably includes two tests that exist specifically to prove
the tier-3 recovery race condition described above is closed:
`test_reconciler_never_touches_a_session_live_in_this_process` and
`test_late_reconnect_after_grace_timeout_claimed_the_session_gets_a_fresh_upload`.
