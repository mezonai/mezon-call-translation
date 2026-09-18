# orchestrator_service

FastAPI service that is the central coordination layer of `mezon-call-translation`. It does not touch media itself — it registers rooms/calls, receives lifecycle events from the recording/transcode pipeline, dispatches STT and transcode work over Redis Streams, persists transcripts/tracks/summaries to Postgres, pushes live updates to clients over Server-Sent Events (SSE), and generates end-of-call meeting summaries via an LLM.

## Role in the system

Depends on:
- **PostgreSQL** — primary datastore (rooms, tracks, transcript chunks, summaries, outbox, users/auth). Schema managed by Alembic (`migrations_pg/`).
- **Redis** — Streams for async work dispatch (`services/redis/`) and a hash-based room-name registry (`services/room_registry.py`).
- **`stt_service`** (`Architect_MultiClient_Server/stt_service`) — consumes the `transcription:stream` Redis Stream this service produces to; not called directly over HTTP by orchestrator for transcription.
- **An LLM provider** (Gemini via `google-genai`, or an OpenAI-compatible local endpoint) — for meeting summaries (`services/llm/`).
- **The Go `agents-bot`** (`AGENTS_BOT_BASE_URL`, see `Architect_MultiClient_Server/agents`) — voice-channel roster and Mezon `user_id` → username resolution during room registration (`services/agents_bot_user_client.py`).
- **Mezon OAuth2** — end-user login (`auth/`, `services/auth_service.py`).

Depended on by:
- **`audio-ingestion/record-service`** — POSTs recording lifecycle events (`recording.started`/`completed`/`failed`) to `POST /api/v2/recordings/events`. See that service's README for its side of this contract.
- **`audio-ingestion/audio-processing-service`** (planned, Phase 5 per `audio-ingestion/PLAN.md`) — will POST `derivative.completed`/`derivative.failed` to the same endpoint once built; the request/response models (`models/recording_event_models.py`) and dispatch logic already exist and are exercised by that same endpoint today.
- **A frontend/dashboard** — reads rooms/transcripts/summaries via the v2 REST API and subscribes to the SSE endpoints for live transcript/chat/metadata/agent-request updates.

## Architecture / data flow

```
record-service  ──POST /api/v2/recordings/events──▶  recording_events_api.py
audio-processing-service (planned)                    │
                                                        ▼
                                          services/recording_event_service.py
                                            │                         │
                              recording.completed          recording.failed /
                                    │                       derivative.completed/.failed
                                    ▼                                 │
                    TranscriptionService.handle_recording_completed   │
                      - upsert Track (status=wait_process)            │
                      - XADD transcription:stream  ───▶ stt_service   │
                    AudioDerivativeService.enqueue                    │
                      - XADD audio_derivative:stream (Phase 5 consumer)
                                    │                                 │
                                    └──────────────┬──────────────────┘
                                                    ▼
                              PgTranscriptRepository.check_and_notify_room_recordings_ready
                                        (fires once per room)
                                                    ▼
                                SSE metadata channel: room_record_done

stt_service ──(elsewhere: batches of transcript segments)──▶ save_transcription:stream
                                                    │
                                                    ▼
                              RedisSaveTranscriptionService (consumer, main.py startup)
                                - progressively saves segments to Postgres
                                - on is_final: Track.status=completed
                                - check_and_complete_room → SummaryService.generate_summary
```

Two identifiers matter throughout this flow: `room_id` (orchestrator's own stable UUID for one call, minted at registration) and `room_name` (the reused Mezon channel name). `services/recording_event_service.py`'s `_resolve_room_ref_id` accepts either from record-service (it sends whatever the agent had), preferring a direct `room_id` lookup and only falling back to the Redis name registry (`services/room_registry.py`) if the value isn't a UUID.

Track completion for a room is a race between two independent paths that can each finish last — the derivative pipeline (`handle_derivative_event`) and the transcript-save pipeline (`RedisSaveTranscriptionService`/ `TranscriptionService.final_room`) — both call the same atomic `check_and_notify_room_recordings_ready` / `check_and_complete_room` guards in `pg_transcript_repository.py`, so the SSE `room_record_done` notice and `SummaryService.generate_summary` each fire exactly once regardless of which path gets there last.

The agent's own TTS track is a special case: it is never sent through Whisper (`skip_stt` — Whisper's feature extractor is fixed at 16kHz, TTS captures at 24kHz, and the text is already known), and instead its terminal status/segments arrive via `tts.transcript`/`tts.completed` events on the same `/api/v2/recordings/events` endpoint, handled by `handle_tts_transcript_event`.

A `SummaryOutboxWorker` (started in `main.py`'s lifespan) polls `OutboxTask` rows on an interval (`OUTBOX_CHECK_INTERVAL_SEC`) to retry summarization for rooms whose summary generation failed, rather than losing it silently.

## Requirements & setup

- Python 3.11 (`.venv` in this checkout is built against 3.11.15).
- PostgreSQL (primary datastore) and Redis (streams + room registry) reachable.
- Dependencies: `pip install -r requirements-orchestrator.txt` (FastAPI/uvicorn, SQLAlchemy + asyncpg + Alembic, redis-py, google-genai, openai, tenacity, PyJWT, motor/pymongo — the latter two are "kept for reference" from the pre-Postgres MongoDB era and not used on the active code path).
- Copy `.env.example` to `.env` and fill in real values before starting (`main.py` calls `load_dotenv()` before importing anything else — see the `E402` ruff exemption for `main.py` in `pyproject.toml`).

### Database migrations (Alembic)

`alembic.ini` (`script_location = migrations_pg`) points at `postgresql+psycopg2://postgres:postgres@localhost:5432/mezon_transcripts` by default — override via `-x` or edit the URL for your environment, or set the `sqlalchemy.url` in `alembic.ini` to match `POSTGRES_*` from your `.env`.

```bash
cd Architect_MultiClient_Server/orchestrator_service
alembic upgrade head
```

Current migrations (`migrations_pg/versions/`): `001_initial_schema` → `002_calculate_participant_durations` → `003_drop_full_text_column` → `004_add_outbox_tasks_with_enums` → `005_add_derivative_tracking` (adds `tracks.derivative_status` / `rooms.record_notified_at`, the columns the recording-event flow above depends on) → `006_add_rooms_section_summary` (per-section summary table used by `light_summary_service.py`).

## Configuration

All configuration is environment-driven, loaded once into a `Config` singleton at import time (`config/application_config.py`); `.env.example` documents most of it but not all — vars marked "not in `.env.example`" below still work via their code default and can be set directly in the environment. Only the vars this service actually reads are listed; several `.env.example` sections (`MONGODB_*`) are legacy/unused leftovers from before the Postgres/mezon-sfu migration and are not read by any code in this service.

| Variable | Required / default | Meaning |
|---|---|---|
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DATABASE` | no — `localhost`/`5432`/`postgres`/`postgres`/`mezon_transcripts` | Primary database connection (asyncpg at runtime, psycopg2 for Alembic). |
| `POSTGRES_POOL_SIZE` / `POSTGRES_MAX_OVERFLOW` | no — `10` / `20` | SQLAlchemy async engine pool sizing. |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` | no — `localhost`/`6379`/``/`0` | Not in `.env.example`. Redis connection for streams + room registry. |
| `REDIS_MAX_CONNECTIONS` / `REDIS_SOCKET_TIMEOUT` / `REDIS_SOCKET_CONNECT_TIMEOUT` / `REDIS_BLOCK_TIMEOUT_MS` | no | Not in `.env.example`. Connection pool / `XREADGROUP` blocking behavior tuning. |
| `REDIS_MAX_RETRIES` / `REDIS_CLAIM_MIN_IDLE_TIME_MS` / `REDIS_HEARTBEAT_INTERVAL_SEC` / `REDIS_WORKER_TIMEOUT_SEC` | no | Not in `.env.example`. Stream consumer retry/claim/orphan-recovery tuning (`services/redis/redis_stream_service.py`). |
| `AGENT_HOST` / `AGENT_PORT` | no — `0.0.0.0` / `8002` | Not in `.env.example`. Server bind address (also the port `agents/README.md`'s `ORCHESTRATOR_BASE_URL` default of `:8002` assumes). |
| `AUTHENTICATE_ACCOUNT_URL` | no | External account-auth service used by the legacy (v1) transcript-push endpoint's `authenticate_account` check. |
| `MEZON_CLIENT_ID` / `MEZON_CLIENT_SECRET` / `MEZON_REDIRECT_URI` / `MEZON_AUTH_URL` / `MEZON_TOKEN_URL` / `MEZON_USERINFO_URL` | `client_id`/`client_secret` required (validated at startup) | Mezon OAuth2 login flow (`auth/`, `/api/v2/auth/mezon/*`). |
| `JWT_SECRET` | should be set | HS256 secret signing orchestrator's own session JWTs. |
| `JWT_EXPIRY_DAYS` / `REFRESH_TOKEN_EXPIRY_DAYS` | no — `1` / `5` | Session / refresh token lifetimes. |
| `INTERNAL_API_SECRET` | no — `""` | Bearer secret record-service/agents present to call service-to-service endpoints (`auth/transcript_auth.py::verify_api_key`), e.g. `POST /api/v2/recordings/events`. |
| `MINIO_ENABLED` / `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET` / `MINIO_BUCKET` / `MINIO_REGION` | required if `MINIO_ENABLED=true` (validated at startup) | MinIO/S3 config for recordings storage; validated against record-service's own bucket configuration. |
| `LOG_LEVEL` | no — `INFO` | Logger level. |
| `STT_HOST` / `STT_PORT` | no — `localhost` / `8000` | Not in `.env.example`. Present in config (`STTServiceConfig`) but note: STT dispatch actually happens over the `transcription:stream` Redis Stream, not this HTTP address — this looks like a leftover from a pre-Redis-Streams direct-HTTP design; confirm before relying on it. |
| `STT_AUTH_TOKEN` / `STT_API_KEY` / `STT_AUTH_HEADER` / `STT_RECONNECT_*` / `STT_CONNECTION_TIMEOUT` / `STT_PING_*` / `STT_MAX_QUEUE_SIZE` / `STT_BATCH_SIZE` / `STT_MAX_BUFFER_SIZE` / `STT_SEND_DELAY` | no | Not in `.env.example`. Same `STTServiceConfig` — see caveat above. |
| `GEMINI_API_KEY` / `GEMINI_URL` | no | Gemini LLM credentials (`GEMINI_URL` not in `.env.example`; defaults to the SDK's own default). |
| `LOCAL_LLM_API_KEY` / `LOCAL_LLM_URL` | no — url defaults to `http://localhost:8080/v1/chat/completions` | OpenAI-compatible local/self-hosted LLM endpoint. |
| `NOTIFICATION_ENABLED` / `NOTIFICATION_WEBHOOK_ENDPOINT` / `NOTIFICATION_CHANNEL_ID` / `NOTIFICATION_WEBHOOK_TOKEN` | no | Mezon webhook used for operational notifications (`utils/notification_log_handler.py`). |
| `OUTBOX_CHECK_INTERVAL_SEC` / `OUTBOX_DELAY_BETWEEN_ITEMS_SEC` / `OUTBOX_BATCH_LIMIT` | no — `30`/`30`/`5` | `SummaryOutboxWorker` polling cadence and batch size. |
| `OUTBOX_RETRY_SUMMARIZATION_TARGET_HOURS` | no — `19,20,21` | Comma-separated hours-of-day the worker prefers for retrying failed summaries. |
| `SUMMARY_LLM_PROVIDER` / `SUMMARY_LLM_MODEL` / `SUMMARY_LLM_TEMPERATURE` / `SUMMARY_LLM_TOP_P` / `SUMMARY_LLM_TIMEOUT` / `SUMMARY_LANGUAGE` / `SUMMARY_LLM_RETRY_COUNT` | no — see `.env.example` | Primary summary LLM. `SUMMARY_LLM_RETRY_COUNT` is a nominal count; see Known limitations below for what actually happens. |
| `SUMMARY_LLM_FALLBACK_ENABLE` / `SUMMARY_LLM_FALLBACK_PROVIDER` / `..._MODEL` / `..._TEMPERATURE` / `..._TOP_P` / `..._TIMEOUT` / `..._RETRY_COUNT` | no | Fallback LLM used if the primary exhausts its retries (`SummaryService._call_llm_with_fallback`). |
| `SUMMARY_THRESHOLD_MIN` | no — `20` | Minimum call duration (minutes) below which the full section-by-section summary flow is skipped in favor of the light summary. |
| `LIGHT_SUMMARY_TARGET_DURATION_MIN` / `..._EXTEND_MIN` / `..._MAX_DURATION_MIN` | no — `15`/`5`/`30` | Section windowing for `light_summary_service.py`. |
| `LIGHT_SUMMARY_LLM_PROVIDER` / `..._MODEL` / `..._TEMPERATURE` / `..._TOP_P` / `LIGHT_SUMMARY_TIMEOUT` / `..._RETRY_COUNT` | no | LLM used for per-section light summaries (defaults to Gemini). |
| `AGENTS_BOT_BASE_URL` | no — `http://localhost:8003` | Go agents-bot base URL for roster/user-id resolution during room registration. |

## Running the service

```bash
cd Architect_MultiClient_Server/orchestrator_service
uvicorn orchestrator_service.main:app --reload --host 0.0.0.0 --port 8002
```

Run from the `Architect_MultiClient_Server` directory (or with it on `PYTHONPATH`) so the `orchestrator_service.*` absolute imports resolve — this matches how `pyrightconfig.json`'s `extraPaths: [".."]` and `pyproject.toml`'s `mypy_path = ".."` are set up. There is no Dockerfile for this service in the repo today; it is run as a plain process (uvicorn), consistent with the "no Docker in dev/prod" policy documented for `record-service`/`agents`.

On startup (`main.py`'s `lifespan`) it initializes the Postgres engine, connects the shared Redis pool, initializes the room registry, and starts two background workers in-process: `RedisSaveTranscriptionService` (transcript save consumer) and `SummaryOutboxWorker`. Shutdown (SIGINT/SIGTERM) stops both workers, notifies all open SSE connections, then disposes the Redis and Postgres connections in order — see the numbered steps in `main.py`.

## API surface

Mounted in `main.py`: a set of legacy routes directly under `/api` (older `api/*.py` modules, using per-endpoint ad hoc auth — e.g. `sse_transcript_api.py`'s `push_transcript` route takes an `appid`/`token` query pair checked against `AUTHENTICATE_ACCOUNT_URL`, and the legacy `summary_api.py` client router is mounted with **no auth at all**, flagged with a `# TODO` in `main.py` pending confirmation nothing downstream still calls it), and the current API under `/api/v2` (`api/v2/router.py`), which uses the permission-based `AuthContext`/`require_any_permission` model (`auth/authorization.py`, `constants/permissions.py`: `rooms:view_all`, `rooms:view_own`, `rooms:delete`, `queues:view_stats`, `metadata_events:view_all`, `chat_external:view_all`, `agent:control`, `resource:delete_any`). The v1 and v2 versions of the same-named endpoints (transcript push, summary) are independent implementations, not aliases — v1 is not simply an older mount of v2's code.

v2 route groups (`api/v2/endpoints/`):
- **`auth_api.py`** (`/api/v2/auth`) — Mezon OAuth2 code exchange, userinfo, refresh, logout, bot login.
- **`room_api.py`** (`/api/v2/rooms`) — list rooms, get room by id, room statistics, per-room audio info.
- **`room_registry_api.py`** (`/api/v2/room-registry`) — register/unregister a room, participant roster snapshot/join events, registry status/list/clear-all.
- **`recording_events_api.py`** (`/api/v2/recordings/events`) — single POST entrypoint for record-service and (eventually) audio-processing-service lifecycle events; secured with `verify_api_key` (`INTERNAL_API_SECRET`), not user auth. Dispatches on payload shape to `RecordingEventService.handle_recording_event` / `handle_derivative_event` / `handle_tts_transcript_event`. Idempotent by design.
- **`dispatch_api.py`** — create/cancel/status of agent dispatch requests.
- **`queue_api.py`** (`/api/v2/queue`) — Redis Stream queue introspection: list queues, stats, pending/DLQ tasks, retry a DLQ task.
- **`summary_api.py`** (`/api/v2/summary`) — get summary by room name or room id, permission-gated.
- **`sse_transcript_api.py`**, **`sse_chat_external_api.py`**, **`sse_metadata_api.py`**, **`sse_agent_request_api.py`** — SSE endpoints (see below).

SSE channels (`api/sse/channels/`, delivered through `SSEManager`, `api/sse/sse_manager.py`):
- **transcript** (`message_channel.py`) — live transcript push per room.
- **chat_external** — external chat bridging.
- **metadata** (`metadata_channel.py`) — room lifecycle notices, including the `room_record_done` event described above.
- **agent_request** (`agent_request_channel.py`) — commands to the Go agent (`tts_play`, `transcript_control`), consumed by `agents/internal/orchestratorclient`.

`controller/` exists as a package (`__init__.py` only, 1 line) with no implementation yet — not currently used by any route.

## Testing

There are no automated tests for this service (no `tests/` directory anywhere under `orchestrator_service/`). This is a known, accepted gap — see project memory `project_missing_test_coverage.md` — not something in progress silently; `orchestrator_service` and `stt_service` are next in line for test coverage.

## Linting & type checking

Ruff and mypy configuration for this service live in this directory's own `pyproject.toml`; a new `pyrightconfig.json` (untracked as of this writing) configures Pyright/Pylance for editor use, pointed at `.venv` with `extraPaths: [".."]` so `orchestrator_service.*` absolute imports resolve the same way they do for mypy/ruff. See [`../README.md`](../README.md) for the full ruff/mypy walkthrough shared across all services (`agents`, `orchestrator_service`, `stt_service`, `tts_service`); the orchestrator-specific pieces are:

```bash
cd Architect_MultiClient_Server/orchestrator_service
ruff check .
mypy .                      # or, from Architect_MultiClient_Server/: mypy -p orchestrator_service
```

Dependencies for linting/type-checking come from this service's own `requirements-orchestrator.txt` (installed into the local `.venv`), not a repo-wide requirements file.

## Known limitations / workarounds


- **Legacy `/api` summary endpoint has no auth.** Flagged with a `# TODO` in `main.py`: `summary_client_router` (the v1, non-`/v2` version) is mounted with no authentication at all, unlike `/api/v2/summary` which requires `rooms:view_all`/`rooms:view_own`. Not yet removed because it hasn't been confirmed that nothing downstream still calls it.
- **`STT_HOST`/`STT_PORT`/`STTServiceConfig` look unused by the actual dispatch path.** STT work is dispatched via the `transcription:stream` Redis Stream (`services/transcription_service.py`), not an HTTP call to `STT_HOST:STT_PORT`. This config class may be a leftover from an earlier direct-HTTP design; verify before depending on it.
- **`.env.example` is missing several variables the code actually reads** (all `REDIS_*` vars, `STT_HOST`/`STT_PORT` and the other `STT_*` knobs, `AGENT_HOST`/`AGENT_PORT`) — they still work via their code-level defaults, but a fresh setup copying only `.env.example` will silently get Redis on `localhost:6379` with no password, which may not match a real deployment.
- **`controller/` is an empty package** (`__init__.py` only) — present but not implemented.
- **Widespread `# type: ignore[explicit-any]` / `dict[str, Any]` TODOs.** `pyproject.toml` sets `disallow_any_explicit = true` for mypy, and a large number of call sites (`services/room_service.py`, `services/summary_service.py`, `services/postgresql/models.py`, `services/redis/redis_stream_service.py`, most Pydantic models with dynamic JSON fields) carry `# TODO: Use Any type because ...` comments explaining why the strict rule is locally suppressed rather than the type actually tightened. This is the gradual-typing rollout in progress (see project memory `project_static_analysis_rollout_plan.md`), not resolved yet.
- **`motor`/`pymongo` are still in `requirements-orchestrator.txt`** ("kept for reference") from the pre-Postgres MongoDB era; not exercised by any code path found in this service today.

