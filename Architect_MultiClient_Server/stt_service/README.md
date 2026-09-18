# stt_service

FastAPI process that turns audio into text for the Mezon Call Translation system. It actually implements **two unrelated STT paths** that happen to live in the same deployable:

1. **Realtime streaming STT** (the one this README focuses on) — a WebSocket endpoint, backed by an NVIDIA Nemotron streaming ASR ONNX model, that the Go `agents` binary (`agents/internal/sttclient`, `agents/internal/tracksink`) streams live call audio into and gets partial/final transcripts back from, per track, in real time.
2. **Batch/offline transcription** — a Redis Streams consumer, backed by `faster-whisper`, that `orchestrator_service` enqueues a job into after a recording finishes (`recording.completed`, see `orchestrator_service/services/transcription_service.py`), to transcribe the whole recorded file from MinIO after the fact.

These two paths do not call each other and use two different engines. Do not conflate them — see "Two STT paths" below.

## Two STT paths, in detail

### 1. Realtime (Nemotron, WebSocket) — this is "the STT service" the Go agent talks to

- Engine: **Nemotron** streaming speech model, run via `onnxruntime-genai` (`onnxruntime-genai==0.14.0`), loaded from a local ONNX model directory (`service/nemotron_stream.py`, `service/new_nemotron_service.py`).
- Entry point: `controller/ws_nemotron_control.py`, WebSocket route `/ws/transcription/`.
- One dedicated inference pipeline (thread + own Nemotron stream state) per connected client (`service/pipeline_manager.py`, `service/client_pipeline.py`) — no shared worker pool.
- This is what `agents/README.md` calls "the Vosk STT service" and what `agents/internal/sttclient` dials via `WS_HOST`/`WS_PORT` + `/ws/transcription/?client_id=...&session_id=...` (`agents/internal/tracksink/tracksink.go:106`). **The "Vosk" name in the Go docs/comments is stale** — the actual engine in this codebase today is Nemotron, not Vosk. `WS_HOST`/`WS_PORT` on the Go side must simply resolve to this service's `SERVER_HOST`/`SERVER_PORT`; they are not literal env var names read by this Python process.

### 2. Batch/offline (Whisper, Redis Streams) — unrelated code path, same process

- Engine: **faster-whisper** (`faster-whisper>=1.0.0`, `service/whisper_transcription_processor.py`).
- Triggered by `orchestrator_service` when a recording finishes (`record-service`'s `recording.completed`, per `audio-ingestion/PLAN.md` D6/D18): `orchestrator_service/services/transcription_service.py` enqueues a `TranscriptionTask` onto the Redis stream `transcription:stream` (consumer group `transcription-workers`).
- This service consumes that stream (`service/redis/redis_transcription_queue_service.py`, `RedisTranscriptionQueueService`, started from `main.py`'s lifespan), downloads the raw PCM16 recording from MinIO, runs it through `faster-whisper`, and streams the resulting segments back out as `SaveTranscriptionTask` batches on a second Redis stream, `save_transcription:stream`. `orchestrator_service`'s `services/redis/redis_save_transcription_service.py` consumes that and persists segments to MongoDB/Postgres progressively.
- There is **no WebSocket and no live client** on this path — it is a fire-and-forget Redis job queue between `orchestrator_service` and this service.
- Note: `orchestrator_service/services/transcription_service.py` also builds an unused `self.api_url` pointing at `http://<stt host>:<stt port>/api/transcribe` and an unused `self.timeout` — that HTTP route does not exist anywhere in this service; the real integration is exclusively the Redis stream described above. Looks like dead/vestigial code left over from an earlier design.

## Requirements & setup

- Python 3.11 (see `pyrightconfig.json`).
- Install: `pip install -r requirements-server.txt` (FastAPI/uvicorn, `onnxruntime-genai`, `faster-whisper`, `redis`, `minio`, `motor`/`pymongo` — the last two are pulled in as dependencies but this service does not talk to MongoDB directly today, only `orchestrator_service` does).
- **Nemotron model files must exist on disk before the realtime path can start** (this is not lazy/optional for the WS path — `main.py`'s lifespan and `PipelineManager.__init__` construct the model eagerly and raise `FileNotFoundError`/fail startup if it's missing). Default expected location, resolved relative to the repo root when `NEMOTRON_MODEL_PATH` is a relative path (`service/new_nemotron_service.py`): `<repo-root>/models/nemotron-model/<NEMOTRON_MODEL_PATH>/genai_config.json`. Fetch it with `scripts/download-nemotron-model.sh` (downloads `onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4` from Hugging Face into `models/nemotron-model/` by default).
- **Whisper model** is downloaded/cached by `faster-whisper` itself on first use of `WhisperTranscriptionProcessor.initialize()` (no manual download step), size controlled by `WHISPER_MODEL_SIZE`.
- **Gipformer fallback model files** must exist on disk for non-realtime Whisper marker-VAD chunk recovery. Default expected location is `<repo-root>/models/gipformer-model`, configured via `WHISPER_GIPFORMER_MODEL_PATH`. Fetch it with `scripts/download-gipformer-model.sh`.
- Running services this process depends on at startup: Redis (both paths; connection pool from `service/redis/connection_pool.py`) and MinIO (batch path only, to fetch recordings).

## Configuration

All configuration is centralized in `config/app_config.py` (`ConfigManager`/`get_config()`), reading from environment variables (via `.env`, `python-dotenv`) with the defaults shown below. See `.env.example` for a working starting point.

| Var | Required / default | Meaning |
|---|---|---|
| `SERVER_HOST` | no / `0.0.0.0` | FastAPI/uvicorn bind host. This is the address the Go agent's `WS_HOST` must resolve to. |
| `SERVER_PORT` | no / `8000` | FastAPI/uvicorn bind port. Must match the Go agent's `WS_PORT`. |
| `SERVER_RELOAD` | no / `false` | uvicorn auto-reload (dev only). |
| `SERVER_LOG_LEVEL` / `LOG_LEVEL` | no / `INFO` | `LOG_LEVEL` drives `setup_logging()`; `SERVER_LOG_LEVEL` is stored in config but not currently wired to the logger. |
| `MAX_CONCURRENT_CLIENTS` | no / `50` | Max simultaneous realtime pipelines (Nemotron). Rejections count toward the circuit breaker. |
| `SAMPLE_RATE` | no / `16000` | Expected PCM16 capture sample rate; must match both the Nemotron model's `sample_rate` (from `genai_config.json`) and Whisper's feature-extractor rate, or the service raises at startup/first use. |
| `CHANNELS` | no / `1` | Must be mono (`1`) — raw PCM is fed in directly with no deinterleaving. |
| `MIN_TEXT_LENGTH` | no / `2` | Declared in config; not read elsewhere in the code reviewed. |
| `NEMOTRON_MODEL_PATH` | no / `nemotron-3.5-asr-streaming-0.6b-onnx-int4` | Model directory name/path; see resolution rule above. |
| `NEMOTRON_LANGUAGE_ID` | no / `0` | Passed to the Nemotron generator as `lang_id` runtime option. |
| `NEMOTRON_EMPTY_PIECE_LIMIT` | no / `2` | Consecutive empty decoded pieces before an utterance is flushed as final. |
| `NEMOTRON_MIN_CHUNKS` / `NEMOTRON_MAX_CHUNKS` | no / `2` / `4` | Config values for chunk batching; note `ClientInferencePipeline._get_adaptive_chunk_size()` currently always returns `4` regardless of these or of concurrency (the adaptive logic is effectively dead — see open items). |
| `NEMOTRON_MIN_TIME_THRESHOLD` / `NEMOTRON_MAX_TIME_THRESHOLD` | no / `0.1` / `0.2` | Declared in config; not read by the current chunk-batching code path. |
| `METRICS_INTERVAL_SEC` | no / `10.0` | Interval for the pipeline manager's periodic metrics log line. |
| `AUDIO_QUEUE_MAXSIZE` | no / `100` (`.env.example` ships `500`) | Per-client audio queue depth (`queue.Queue`) before chunks are dropped. Conceptually the same kind of knob as the Go agent's own `STT_MAX_QUEUE_SIZE`, but that is a separate, Go-side queue in front of the WS connection — not this variable. |
| `STT_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | no / `5` | Consecutive failures before a client's circuit breaker disconnects it. Disconnected clients must reconnect for a fresh breaker. |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` | no / `localhost` / `6379` / `""` / `0` | Redis connection, shared by both the batch consumer and the save-task producer. |
| `REDIS_CLAIM_MIN_IDLE_TIME_MS` | no / `60000` | Idle time before an in-flight batch task is considered orphaned and reclaimed. |
| `REDIS_BLOCK_TIMEOUT_MS` | no / `5000` | `XREADGROUP` block duration. |
| `REDIS_MAX_RETRIES` | no / `3` | Retries before a failed batch task goes to the dead-letter path. |
| `REDIS_MAX_CONNECTIONS` | no / `10` | Shared connection pool size. |
| `REDIS_SOCKET_TIMEOUT` / `REDIS_SOCKET_CONNECT_TIMEOUT` | no / `30.0` / `10.0` | Redis socket timeouts. |
| `REDIS_HEARTBEAT_INTERVAL_SEC` / `REDIS_WORKER_TIMEOUT_SEC` | no / `10.0` / `30.0` | Consumer heartbeat cadence and orphan-detection window. |
| `WHISPER_MODEL_SIZE` | no / `large-v3-turbo` | faster-whisper model size (`tiny`..`large-v3-turbo`) or local CTranslate2 path. |
| `WHISPER_GIPFORMER_MODEL_PATH` | no / `models/gipformer-model` | Local directory containing Gipformer fallback model ONNX files. Resolved relative to repo root if relative. |
| `WHISPER_DEVICE` | no / `cpu` | `cpu` or `cuda`. |
| `WHISPER_COMPUTE_TYPE` | no / `int8` | faster-whisper compute type. |
| `WHISPER_CPU_THREADS` | no / `4` | CPU thread count for Whisper inference. |
| `WHISPER_BEAM_SIZE` | no / `1` | Whisper decode beam size. |
| `WHISPER_VAD_FILTER` | no / `true` | Enable Whisper's built-in VAD filter. |
| `WHISPER_SAMPLE_RATE` | no / `16000` | Declared but the code enforces `AudioConfig.sample_rate` against the model's own feature-extractor rate, not this var directly. |
| `WHISPER_LANGUAGE` | no / `""` (auto-detect) | Force a language code (`en`, `vi`, `ja`, ...) instead of auto-detect. |
| `TRANSCRIPT_CHUNK_SIZE` | no / `50` | Number of Whisper segments per `SaveTranscriptionTask` batch sent to `save_transcription:stream`. |
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` / `MINIO_BUCKET` / `MINIO_SECURE` | no / `localhost:9000` / `minioadmin` / `minioadmin123` / `livekit-recordings` / `false` | MinIO client used only by the batch/Whisper path to fetch the raw recording. |
| `METRICS_ENABLED` | no / `false` | Master switch for Prometheus metrics (`/metrics`) and the system-metrics background loop. |
| `METRICS_HTTP` / `METRICS_WS` / `METRICS_SYSTEM` / `METRICS_STT` | no / `true` each | Sub-toggles; only meaningful when `METRICS_ENABLED=true`. |
| `METRICS_UPDATE_INTERVAL` | no / `5.0` | System metrics (CPU/mem/queue) sampling interval. |
| `LOG_FORMAT` / `LOG_DATE_FORMAT` / `LOG_APP_FILE` / `LOG_METRICS_FILE` / `LOG_MAX_FILE_SIZE` / `LOG_BACKUP_COUNT` | no | Declared in `LoggingConfig`; `setup_logging()` in `utils/logging_config.py` currently only sets up a stdout console handler with a fixed format — the file-handler/rotation fields are not wired up in the code reviewed. |
| `MONGODB_HOST` / `MONGODB_PORT` / `MONGODB_USERNAME` / `MONGODB_PASSWORD` / `MONGODB_DATABASE` / `MONGODB_COLLECTION` | shown in `.env.example` | Not read anywhere in `config/app_config.py` or the rest of this service's code — vestigial in this service (MongoDB access lives in `orchestrator_service`, not here). |

## Running the service

```bash
pip install -r requirements-server.txt
cp .env.example .env   # adjust as needed
python -m uvicorn stt_service.main:app --host 0.0.0.0 --port 8000
```

Run from the `Architect_MultiClient_Server` directory (imports are package-absolute, e.g. `from stt_service.controller...`, see `main.py`). Redis must be reachable at startup (`main.py`'s lifespan connects the pool and starts the Whisper queue consumer unconditionally); MinIO is only needed once a batch job actually runs.

## API / protocol

### Realtime WebSocket (Nemotron)

`GET /ws/transcription/?client_id=<id>&session_id=<id>&language=<opt>&max_duration=<opt seconds>&idle_timeout=<opt seconds>` (`controller/ws_nemotron_control.py`).

- Client sends binary PCM16 mono frames (16 kHz per `SAMPLE_RATE`) as raw WebSocket binary messages. An optional per-chunk trace header is supported: if a frame starts with `b'CHID'` followed by 8 little-endian bytes, that's parsed out as `chunk_id` and stripped before the remaining bytes are treated as audio payload.
- Server sends back **plain JSON objects** (not wrapped in an envelope) for each partial/final result, e.g.:
  ```json
  {"text": "hello there", "is_final": false, "client_id": "...", "session_id": "...", "timestamp": 1234567890.1, "chunk_id": 42}
  ```
  (`service/client_pipeline.py:_process_accumulated_chunks`, dispatched via `service/result_dispatcher.py`).
- Session lifecycle: on connect, the socket is accepted, registered with `session_manager.SessionManager` (in-memory `session_id -> {clients, transcripts}` map) and with `OptimizedResultDispatcher` (a dedicated `asyncio.Queue` + sender task per client), and a dedicated `ClientInferencePipeline` (its own thread + Nemotron stream state) is created lazily on first audio chunk. On disconnect/idle-timeout/ max-duration/error, the handler always (in `finally`) removes the client from `session_manager`, unregisters it from the dispatcher, and tears down its pipeline — including flushing any partial trailing audio (zero-padded up to one Nemotron model block) so the last few hundred ms aren't silently dropped.
- Multiple clients can share a `session_id` (e.g. multiple tracks in one call); `session_manager` fans transcripts out per-client, not broadcast by default (`get_clients_to_notify_transcript` only returns other clients when explicitly asked without a `sender_client_id`).
- Diagnostic HTTP endpoints on the same router: `GET /ws/dispatcher-stats`, `GET /ws/stats`, `GET /ws/pipeline-distribution`, `GET /ws/pipeline-health`, `GET /ws/service-info`, `POST /ws/pipeline/{client_id}/cleanup`.

### Batch/offline (Redis, no HTTP/WS surface)

No client-facing endpoint. Input is a `TranscriptionTask` XADD'd by `orchestrator_service` onto `transcription:stream` (fields: `filename`, `egress_id`, `location`, `duration`, `started_at`, `ended_at`, `source`, `retry_count`, `priority`). Output is a sequence of `SaveTranscriptionTask` messages XADD'd onto `save_transcription:stream` (segments as a JSON-encoded list, `chunk_index`, `is_final`, `status`).

### Process-level HTTP

- `GET /health`, `GET /health/simple` (`main.py`) — liveness/readiness, backed by `service/health_service.py`'s registered checks (uptime, memory, circuit breakers, plus Nemotron pipeline checks registered at startup).
- `GET /metrics` — Prometheus exposition, only served (200) when `METRICS_ENABLED=true`, otherwise 404.

## Layout

- `main.py` — FastAPI app, lifespan startup/shutdown (Redis pool, Whisper queue consumer + model preload, per-client pipeline controller, system metrics loop), `/health` and `/metrics` routes.
- `session_manager.py` — in-memory per-session/per-client registry for the realtime WS path (who's connected, whether they want transcripts, last known language/text).
- `config/app_config.py` — single source of truth for all env-var-driven configuration (`AppConfig` dataclasses + `ConfigManager`).
- `controller/ws_nemotron_control.py` — the realtime WebSocket route and its diagnostic/stats HTTP siblings.
- `models/` — dataclasses for Redis Stream tasks: `transcription_task.py` (`TranscriptionStreamTask`, consumer-side, for the batch Whisper job), `save_transcription_task.py` (`SaveTranscriptionTask`, producer-side, batches sent to `orchestrator_service`), `stream_base.py` (shared base types/protocols for both Redis producer and consumer tasks).
- `service/new_nemotron_service.py` — top-level realtime STT service facade (`NewSTTNemotronService`); owns the `PipelineManager`, circuit breaker, and result dispatch wiring.
- `service/pipeline_manager.py` — creates/tracks/cleans up one `ClientInferencePipeline` per connected client, enforces `MAX_CONCURRENT_CLIENTS`, idle-cleanup loop, periodic metrics log.
- `service/client_pipeline.py` — the actual per-client pipeline: dedicated processing thread, audio accumulation/chunking into Nemotron's fixed model-block size, emits partial/final transcript events.
- `service/nemotron_stream.py` — thin wrapper around `onnxruntime_genai`: loads the model once (`NemotronModel`), and creates a cache-aware per-client `NemotronStream` (processor + tokenizer + generator + endpointing state).
- `service/migration_controller.py` — adapter/facade (`PipelineServiceController`) used by `main.py`/the controller so the rest of the app doesn't depend directly on `new_nemotron_service`; mostly pass-through today.
- `service/result_dispatcher.py` — per-client `asyncio.Queue` + dedicated sender task, decoupling Nemotron's worker thread from the WebSocket send path.
- `service/whisper_transcription_processor.py` — the batch/offline path: downloads a recording from MinIO, runs `faster-whisper`, streams segment batches to Redis for `orchestrator_service` to persist.
- `service/redis/redis_transcription_queue_service.py` — Redis Streams consumer group for `transcription:stream` (XREADGROUP loop, ack/retry/ orphan recovery), feeds tasks to `whisper_transcription_processor`.
- `service/redis/redis_stream_service.py` — generic Redis Streams consumer-group machinery (heartbeat, XAUTOCLAIM orphan recovery, retry/ dead-letter) shared by the transcription queue.
- `service/redis/redis_producer_service.py` — generic Redis Streams producer (XADD) used to publish `SaveTranscriptionTask`s.
- `service/redis/connection_pool.py` — one shared `redis.asyncio` connection pool for the whole process.
- `service/health_service.py` — health-check registry (`/health*` routes live here too, though `main.py` only actually mounts its own `/health`/`/health/simple`, not this module's router) plus Nemotron-pipeline-specific checks registered at startup.
- `service/metrics_service.py` — Prometheus metric definitions (WS, speech, STT, performance counters/gauges/histograms).
- `utils/circuit_breaker.py` — per-client circuit breaker: on `stt_failure_threshold` consecutive failures, disconnects the client instead of the classic open/half-open retry pattern.
- `utils/decode.py` — small helpers to decode raw `bytes` values/mappings coming back from `redis-py`.
- `utils/decorator.py` — `@singleton` used throughout `service/`.
- `utils/logging_config.py` — root logger setup (stdout, fixed format).
- `utils/websocket_monitor.py` — connection/disconnect event tracking for diagnostics and Prometheus WS metrics.
- `pyrightconfig.json` — local pyright config (Python 3.11, `.venv`, `extraPaths: [".."]` so sibling-service imports resolve). See `Architect_MultiClient_Server/README.md` for the shared ruff/mypy walkthrough that applies across all services in this repo; that doc is the source of truth for how to actually invoke these tools here, this README won't repeat it.

## Testing

There is currently **no `tests/` directory and no automated tests for this service at all** — neither the realtime Nemotron pipeline nor the batch Whisper path. This is a known, accepted gap (see the sibling "missing test coverage" note for `orchestrator_service`/`stt_service` in project memory), not something hidden or incidental. Given the amount of threading/async/circuit-breaker state in `client_pipeline.py` and `pipeline_manager.py`, and the two independent Redis-stream flows, this is one of the riskier untested surfaces in the repo.

## Relation to other services

- **`agents` (Go)** → this service, realtime path only: dials `ws://<WS_HOST>:<WS_PORT>/ws/transcription/` per audio track (`agents/internal/sttclient`, `agents/internal/tracksink`), streams PCM in, reads transcript JSON back to forward onward (e.g. to translation/ TTS). One-way dependency; this service never calls back into `agents`.
- **`orchestrator_service`** → this service, batch path only: enqueues `TranscriptionTask`s onto `transcription:stream` after `recording.completed` (`orchestrator_service/services/transcription_service.py`). This service never calls `orchestrator_service` over HTTP for this — the unused `api_url`/`/api/transcribe` field mentioned above notwithstanding.
- This service → **`orchestrator_service`**, batch path: publishes `SaveTranscriptionTask` batches onto `save_transcription:stream`, which `orchestrator_service/services/redis/redis_save_transcription_service.py` consumes and persists.
- **MinIO** and **Redis** are both required infrastructure; MongoDB is not used by this service directly despite appearing in `requirements-server.txt` and `.env.example`.

