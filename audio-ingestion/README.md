# audio-ingestion

Owns one domain: capturing raw audio for a call and turning it into a client-playable derivative format. Two independently deployed/scaled Python (`grpc.aio`) services live here today:

- **`record-service/`** — critical path. Captures raw PCM16 audio forwarded by `agents` over gRPC and durably multipart-uploads it straight to S3/MinIO. Kept deliberately minimal and reliable (three-tier crash/retry recovery) since nothing else in the pipeline can recover lost raw audio.
- **`audio-processing-service/`** — non-critical, async, CPU-bound. Consumes an `audio_derivative:stream` Redis Stream that `orchestrator_service` publishes to on every `recording.completed`, transcodes the raw PCM into a client-playable OGG/Opus file via `ffmpeg`, and reports the result back to orchestrator.

This file and each service's own `README.md` describe what the code actually does right now — verify against source, since the in-flight change described below means even these can lag.

## Current relationship between the two services (as of 2026-09-15)

**Today's running system**: the two services above are both fully active — record-service captures raw PCM only, and audio-processing-service is the only thing that turns it into an OGG/Opus derivative, triggered by orchestrator on every `recording.completed` event.

**But there is an in-flight, uncommitted, and so-far incomplete change to that picture.** `record-service/Dockerfile` has an uncommitted diff whose comment describes a "PLAN.md D6-successor" plan — record-service encoding PCM → OGG/Opus itself via an ffmpeg subprocess, which would make `audio-processing-service` redundant — and the new `docker-compose.yml` in this directory states in its comments that audio-processing-service "is retired" on that basis. Having read the actual code, that is **not** true yet:

- The Dockerfile diff only adds `apt-get install ffmpeg` (with `libopus`) to record-service's image. The transcode module its own comment names (`src/record_service/infra/transcode/`) does not exist anywhere in this checkout, and no other file in `record-service/` has changed — object keys, event payloads, and behavior are all still pure raw-PCM passthrough.
- `audio-processing-service`'s own code is untouched since 2026-08-06 (`git log -- audio-ingestion/audio-processing-service`).
- `orchestrator_service`'s dispatch to `audio-processing-service` (`services/audio_derivative_service.py` → `audio_derivative:stream`, called unconditionally from `services/recording_event_service.py` on every `recording.completed`) is still fully wired, with no flag gating it.

So: **audio-processing-service is fully active, not retired or even partially retired**, in the code as it stands. The Dockerfile change is groundwork for a migration that has been decided in intent (per its own comment) but not yet implemented or coordinated across `orchestrator_service`. See each service's own README — both carry a matching 2026-09-15 status note with the same finding in more detail — for what specifically would need to change for the migration to actually land.

## Data flow

The in-flight change above hasn't altered this shape yet, only prepared one service's Docker image for a future version of it:

```
agents (per-room worker)
   │  subscribes a track dedicated to recording, independent of realtime STT
   │  forwards raw PCM16 over gRPC (proto/recording.proto, StreamAudio bidi stream)
   ▼
record-service
   │  writes raw PCM straight to MinIO/S3 (multipart upload, 3-tier crash recovery)
   │  reports recording.started / recording.completed / recording.failed
   │  → orchestrator_service, HTTP POST, best-effort with retry
   ▼
orchestrator_service
   │  recording.started  → placeholder Track row
   │  recording.completed → Track.status="wait_process"
   │                        + publish transcription:stream (Whisper STT, unrelated to this folder)
   │                        + publish audio_derivative:stream (Redis Stream)
   ▼
audio-processing-service
   │  consumes audio_derivative:stream (Redis Stream + consumer group)
   │  downloads raw PCM from MinIO, transcodes via ffmpeg to OGG/Opus,
   │  uploads the derivative back to MinIO (same bucket, .pcm -> .ogg key)
   │  reports derivative.completed / derivative.failed → orchestrator_service
   ▼
orchestrator_service
   │  updates Track.derivative_status; once the room has finalized AND every
   │  track's derivative_status is terminal, fires room_record_done (SSE,
   │  bare notice, no file path)
   ▼
Bot / FE client (already authorized) calls its own existing API to fetch
the real derivative path — out of scope for this folder.
```

## Local testing with `docker-compose.yml`

`docker-compose.yml` in this directory is **local-testing-only**, not a dev/prod deployment method — both services actually run via systemd on the host in real environments ("no Docker in dev/prod"; see each service's own `deploy/systemd/README.md`). It exists because verifying record-service's S3 multipart path needs a real S3-compatible target, and Docker Compose is the fastest way to get MinIO running locally.

What it brings up: a `minio` container, a one-shot `minio-init` job that creates the `call-recordings` bucket record-service expects to already exist, and `record-service` itself built from `./record-service`, wired to that MinIO over the compose network. Point the Go `agents` worker's `RECORD_SERVICE_GRPC_ADDR` at `localhost:50051` to feed it real audio; the compose file's own comments cover platform-specific networking notes (e.g. `host.docker.internal` needing an explicit `extra_hosts` entry on native Linux Docker) and the fact that record-service's orchestrator POSTs are fire-and-forget, so connection-refused/DNS warnings in its logs without a real orchestrator running are expected and harmless.

There is deliberately **no Redis and no audio-processing-service** in this compose file. Per its own comments this is because audio-processing-service is treated as retired by the in-flight D6-successor change described above — but per the finding in this file's "Current relationship" section, that service is actually still fully active in the real pipeline today. Anyone using this compose file to test a real end-to-end derivative-generation flow (not just the raw-capture path) should be aware it does not currently stand up what that would require.

## Further reading

- [`record-service/README.md`](record-service/README.md) — capture path, Ports & Adapters layout, recovery tiers, configuration, and this transition's status note.
- [`audio-processing-service/README.md`](audio-processing-service/README.md) — transcode worker, configuration, and this transition's status note.

