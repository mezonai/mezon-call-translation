# record-service

Critical-path service that receives streaming Ogg/Opus call audio from agents and durably uploads the original object to S3/MinIO.

This README is "what the code does and how to read it" — it does not attempt to re-derive the full historical rationale behind every design choice.

## End-to-end flow

```
Call room (mezon-sfu, WebRTC)
   │  agent's rtcagent.PeerAgent gets OnTrack for each mic track
   ▼
agents/cmd/agent  (Go)
   internal/audiopipeline.Bridge      -- wraps existing RTP/Opus in Ogg
   → internal/tracksink.RecordSinkFactory  -- builds a per-track sink
   → internal/recordclient.Forwarder  -- gRPC client
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
   → stores original .ogg key in tracks.audio_info (Postgres)
   → transcription:stream (Redis Stream, consumed by stt_service)
   → SSE room_record_done notice (once per room, bare notice, no file paths)
```

record-service itself never talks to the SFU, Postgres, or Redis. Its only two network dependencies are S3/MinIO (data plane) and one HTTP POST to orchestrator (control plane, best-effort/async).

## Why gRPC bidi streaming, not client-streaming

`proto/recording.proto` defines `StreamAudio(stream AudioChunk) returns (stream RecordingAck)`. Bidi (not plain client-streaming) so record-service can `accepted`/`rejected` the session on the very first message, instead of the agent only finding out something was wrong after the whole call ended.

```protobuf
message AudioChunk {
  oneof payload {
    SessionStart start = 1;   // first message: (room_id, track_id) key + format
    bytes pcm = 2;            // legacy field; rejected by the Ogg-only server
    DroppedFrames dropped = 3; // agent-side backpressure count, annotation only
    bytes ogg = 4;            // consecutive fragments of one Ogg object
  }
}
message RecordingAck {
  string status = 1;  // accepted | rejected | completed
  string object_key = 2;
  string error = 3;
}
```

One `RecordingSession` = one `(room_id, track_id)` = one S3 multipart upload. `session_id` is always `"{room_id}:{track_id}"` (`RecordingSession.make_session_id`).

## Request lifecycle, file by file

1. **`infra/grpc/ingest_server.py`** — reads `SessionStart`, then `ogg`/`dropped` chunks. PCM payloads are explicitly rejected.

2. **`application/start_recording.py`** (`StartRecording`) — opens a new S3 multipart upload (`create_multipart_upload`) and registers an `ActiveSession` in the in-memory `SessionRegistry`, or **resumes** an existing session if one is sitting in `GRACE_WAIT` for the same `session_id` (reconnect within the grace window — see Recovery tiers below). `SessionRegistry.creation_lock(session_id)` serializes the whole decision so two concurrent starts for the same track can't both open separate uploads — scoped per `session_id` (not a single global lock), so starting one track never blocks starting an unrelated one (D24). Once a genuinely new session is registered, fires `recording.started` to orchestrator fire-and-forget (D26) — not awaited, so a slow/unreachable orchestrator never delays accepting audio.

3. **`application/append_audio.py`** (`AppendAudio`) — buffers Ogg bytes without decoding or re-encoding and flushes multipart parts to S3.

4. **`application/stop_recording.py`** (`StopRecording`) — on graceful close, finalizes immediately. On abrupt disconnect, parks the session in `GRACE_WAIT` and starts a `grace_period_seconds` (default 45s) timer before finalizing best-effort — see Recovery tiers. `_finalize` flushes whatever's left in the buffer as a final part, then calls `finalize.complete_or_abort` and reports `recording.completed` / `recording.failed` to orchestrator.

5. **`application/finalize.py`** (`complete_or_abort`) — completes or aborts the multipart upload. Its low-byte-rate warning uses a conservative minimum streaming-Ogg page rate, so compressed audio still receives a useful truncation warning.

6. **`application/report_event.py`** (`ReportEvent`) — POSTs the event to orchestrator with its own retry policy. If every attempt fails, the session is persisted with `reported=False` instead of the event being dropped — the reconciler (`recover_orphaned_sessions.py`) picks it up later.

## Recovery tiers (why three separate mechanisms)

Raw audio capture is the critical path, so failure handling is deliberately layered:

| Tier | Failure mode | Mechanism |
|---|---|---|
| 1 | Bad `SessionStart` (bad room/track) | Reject on the first `RecordingAck`, before any upload starts |
| 2 | gRPC stream drops abruptly (network blip) | `GRACE_WAIT` + `grace_period_seconds` timer; a `SessionStart` with the same `session_id` within the window resumes the same `upload_id` instead of opening a new file |
| 3 | Process crash / restart | `application/recover_orphaned_sessions.py`, run once at startup (`main.py::serve`) and on a `reconciler.interval_seconds` timer. Reads durable per-session JSON files off disk (`infra/state/file_session_state_repo.py`) and either finalizes a session a dead process left mid-upload, or retries reporting an already-terminal one that never made it to orchestrator |

Tiers 2 and 3 share one subtlety worth reading if you're reviewing correctness: a resume (tier 2, in `start_recording.py`) and a grace-timeout finalize (tier 2's own timer, in `stop_recording.py::_grace_timeout`) can race each other. Both sides coordinate through the **same** `active.lock` object on the `ActiveSession`, and each re-checks status after acquiring the lock — so exactly one of "resume" or "finalize" wins, never both. Tier 3 is similarly guarded: the reconciler skips any `session_id` still present in the in-process `SessionRegistry`, so it never touches a session a live task already owns.

Local per-session state (`RECORD_STATE_DIR`, default `/data/record-service/state`) is *not* the source of truth for audio bytes — every uploaded part already durably exists in S3/MinIO via the multipart upload. It only remembers `(upload_id, parts, status)` so a restarted process can pick a session back up without re-reading any audio. See the module docstring in `infra/state/file_session_state_repo.py` for the one known limitation (ephemeral filesystem across pod reschedule).

## Object storage layout

`infra/naming.py::build_object_key` computes the key locally (never asks orchestrator), so opening a recording never has a synchronous dependency on orchestrator being reachable:

```
{room_id}/{participant_identity}-{source}-audio-{random_hex}.ogg
```

The object is the original client-playable Ogg/Opus stream emitted by the agent. No derivative file or transcoding job is created.

## Talking to orchestrator

One outbound call: `POST {ORCHESTRATOR_BASE_URL}{RECORDING_EVENTS_PATH}` (default `/api/v2/recordings/events`), Bearer-authenticated via `ORCHESTRATOR_API_KEY` if set. Payload is a full self-describing session snapshot (see `infra/reporting/http_event_reporter.py::_to_payload`), same shape for all three events (`event` field is what orchestrator dispatches on).

`room_id` throughout this file (`SessionStart.room_id`, `session_id`, the S3 object key prefix) is whatever the agent sent at `StreamAudio` start -- record-service treats it as an opaque string, no validation, no assumption about its shape. The agent sends orchestrator's own stable room UUID here (captured once at registration, before it even connects to the call's room), not the room name it used to send previously -- this is what lets orchestrator resolve `room_id` on an incoming event directly (existence check) instead of re-resolving a room-name-to-id mapping that could have been reassigned to a different call by the time a late event arrives. record-service itself needed zero code changes for this -- it was already agnostic to what `room_id` actually contains.

- `recording.started` — fired once after session registration. Orchestrator creates a placeholder track so room finalization cannot overlook an in-flight recording.
- `recording.completed` / `recording.failed` — unchanged, awaited from `stop_recording.py`/`recover_orphaned_sessions.py`, upsert the same row via `save_track_metadata`.

**Known open gap**: if a placeholder is created and record-service permanently loses its local recovery state before a terminal event, that row has no `audio_info` and continues to block `room_record_done`; there is no orchestrator timeout yet.

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

Server errors (5xx) are retried by `report_event.py`; 4xx is treated as a final rejection (not retried); if retries are exhausted the event is persisted locally and picked up by the reconciler instead of being lost.

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

On the `agents` side, there is no enabled/disabled switch — the old built-in egress/recording pipeline is fully removed, so there's no fallback for a flag to roll back to. Forwarding is always attempted; `recordclient.NewForwarder` fails soft per track if record-service isn't reachable (`tracksink.RecordSinkFactory.NewSink` just returns a `nil` sink, logged, and the track keeps flowing without recording).

## Where the caller (agents) hooks in

[`agents/internal/recordclient`](../../agents/internal/recordclient/forwarder.go) is the gRPC client counterpart. `audiopipeline` muxes existing RTP/Opus directly into Ogg, creates chained logical streams across SFU binding/SSRC changes, and `Forwarder.SendOgg` sends consecutive fragments without blocking the media loop.

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

`application/*` and `infra/grpc/ingest_server.py` depend only on `domain/ports.py`. A future `infra/sfu/rtp_ingest.py` (direct RTP capture once the SFU is swapped out, D3) would drive the same three use cases — `start_recording` / `append_audio` / `stop_recording` — without any application-layer change.

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

No Docker in dev/prod -- systemd on the host instead. Full install steps, template unit file, and env file examples: **`deploy/systemd/README.md`**.

## Benchmarking

```bash
# terminal 1 -- the target (pin to its own core if benchmarking on one
# machine, see the script's own docstring for why):
taskset -c 0 python -m record_service.main

# terminal 2:
taskset -c 1 python scripts/benchmark_concurrency.py --sweep 10,25,50,100 --csv results.csv
```
Sweeps concurrency levels using representative Ogg fragments at real Opus packet cadence while sampling CPU/RSS.

## Tests

`tests/` uses fakes (`tests/fakes.py`) for `BlobStorage`/`EventReporter`, no mocks-of-mocks. Notably includes two tests that exist specifically to prove the tier-3 recovery race condition described above is closed: `test_reconciler_never_touches_a_session_live_in_this_process` and `test_late_reconnect_after_grace_timeout_claimed_the_session_gets_a_fresh_upload`.

