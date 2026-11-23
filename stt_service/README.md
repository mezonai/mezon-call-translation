# stt_service

Batch (non-realtime) speech-to-text worker for Mezon Call Translation. It transcribes a whole recorded track after the call: there is no WebSocket and no live client. The realtime Nemotron streaming path that used to live in `Architect_MultiClient_Server/stt_service` was removed in Phase 8-2.

## How it fits in

```
orchestrator_service ──XADD transcription:stream──▶ stt_service ──XADD save_transcription:stream──▶ orchestrator_service
  (recording.completed)   group transcription-workers     │                               (RedisSaveTranscriptionService
                                                          ▼                                persists segments to Postgres)
                                                  MinIO: raw PCM16 track
```

1. `orchestrator_service/services/transcription_service.py` enqueues a `TranscriptionTask` on `transcription:stream` when record-service reports `recording.completed`.
2. `RedisTranscriptionQueueService` (`service/redis/redis_transcription_queue_service.py`) consumes it with consumer group `transcription-workers`. It retries failed tasks up to `REDIS_MAX_RETRIES` times and reclaims tasks orphaned by a crashed worker.
3. `WhisperTranscriptionProcessor` (`service/whisper_transcription_processor.py`) downloads the headerless PCM16 recording from MinIO and transcribes it. It sends `SaveTranscriptionTask` batches of `TRANSCRIPT_CHUNK_SIZE` segments to `save_transcription:stream` as they are produced, followed by a final "completed" message.

## Transcription pipeline

All of it lives in `service/whisper_marker_transcriber.py`:

1. **VAD**: Silero VAD (bundled with faster-whisper) splits the recording into speech spans. Each span's start and end become the timestamps of its segment; Whisper's own timestamps are never used.
2. **Packing with an audio marker**: consecutive spans are packed into chunks of at most 30 s, with the marker asset `stt_service/assets/whisper_marker.wav` ("đầu mốc hồng ngọc") placed between spans. A chunk with N spans therefore contains exactly N−1 markers.
3. **Whisper decode**: one decode per chunk (`faster-whisper`, CPU), always with `vad_filter=False`. Known Whisper spam phrases (`HALLUCINATION_BLACKLIST`) are removed from the raw output.
4. **Marker split**: the marker text is located in the output to split it back into per-span text.
   - Strict edit-distance matches are used first.
   - Looser variants ("mục hồng ngọc", "đầu mốc", ...) only fill a marker slot that strict matching left empty, and only near the position where that marker is expected.
   - A one-span chunk has no marker, so nothing is removed from it.
5. **Gipformer fallback**: if fewer than N−1 markers are found, the chunk is re-transcribed span by span with the CPU Gipformer model (`service/gipformer_service.py`, sherpa-onnx), so marker text never leaks into the transcript. Gipformer is a hard dependency: the service does not start without its model.

## Setup

- Python 3.11+.
- Install: `python -m venv venv && ./venv/bin/pip install -r requirements.txt`, or use `scripts/setup.sh`, which also creates `.env` and downloads models.
- Models:
  - Whisper (`WHISPER_MODEL_SIZE`) is downloaded and cached by faster-whisper on first startup. A local CTranslate2 directory also works.
  - Gipformer: run `scripts/download-gipformer-model.sh` (defaults to `<repo-root>/models/gipformer-model`).
- Runtime dependencies: Redis and MinIO.

## Running

Run from this directory (`stt_service/`). The package import path is `stt_service.*`, and relative paths in `.env`, such as `models/...` and `logs/`, resolve against the current working directory.

```bash
cd stt_service
./venv/bin/python -m uvicorn stt_service.main:app --host localhost --port 8000
```

In production it runs as the `mezon-stt-service` systemd unit, created by `scripts/create-systemd-services.sh`. Startup waits until Redis is connected and both Whisper and Gipformer are loaded before it consumes any task.

HTTP endpoints (local only, for health checks and monitoring):

- `GET /health`: health checks (uptime, memory, Gipformer fallback).
- `GET /health/simple`: just healthy/unhealthy.
- `GET /metrics`: Prometheus metrics, returns 404 unless `METRICS_ENABLED=true`.

## Configuration

`stt_service/config/app_config.py` reads the configuration from `.env` in this directory. See `.env.example` for a starting point.

| Var | Default | Meaning |
|---|---|---|
| `SERVER_HOST` / `SERVER_PORT` | `localhost` / `8000` | Stored in config. The actual bind address comes from the uvicorn command line. |
| `WHISPER_MODEL_SIZE` | `large-v3-turbo` | faster-whisper model name or local CTranslate2 directory. |
| `WHISPER_GIPFORMER_MODEL_PATH` | `models/gipformer-model` | Gipformer model directory. Use an absolute path in production (as `setup.sh` does), because a relative path resolves against the working directory. |
| `WHISPER_LANGUAGE` | `vi` | Language code; `auto` enables language detection. |
| `WHISPER_COMPUTE_TYPE` | `int8` | CTranslate2 compute type. |
| `WHISPER_CPU_THREADS` | `8` | CPU threads for Whisper and Gipformer. |
| `WHISPER_TEMPERATURE` | `0.0` | Single value or comma-separated fallback list, e.g. `0.0, 0.4`. |
| `TRANSCRIPT_CHUNK_SIZE` | `50` | Segments per `SaveTranscriptionTask` batch. Has no effect on transcription quality. |
| `SAMPLE_RATE` / `CHANNELS` | `16000` / `1` | Expected capture format (16 kHz mono PCM16). |
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` / `MINIO_BUCKET` / `MINIO_SECURE` | `localhost:9000` / `minioadmin` / `minioadmin123` / `livekit-recordings` / `false` | Where the recordings are stored. |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` | `localhost` / `6379` / empty / `0` | Redis connection. |
| `REDIS_CLAIM_MIN_IDLE_TIME_MS` | `60000` | Idle time before a pending task is reclaimed as orphaned. |
| `REDIS_BLOCK_TIMEOUT_MS` | `5000` | `XREADGROUP` block time. |
| `REDIS_MAX_RETRIES` | `3` | Attempts before a task is given up. |
| `REDIS_MAX_CONNECTIONS` | `10` | Connection pool size. |
| `REDIS_SOCKET_TIMEOUT` / `REDIS_SOCKET_CONNECT_TIMEOUT` | `30.0` / `10.0` | Socket timeouts in seconds. Keep the socket timeout above the block timeout. |
| `REDIS_HEARTBEAT_INTERVAL_SEC` / `REDIS_WORKER_TIMEOUT_SEC` | `10.0` / `30.0` | Worker heartbeat interval and dead-worker detection window. |
| `LOG_LEVEL` | `INFO` | Log level. Logs go to stdout, `logs/app.log` and `logs/metrics.log`. |
| `METRICS_ENABLED` / `METRICS_SYSTEM` / `METRICS_STT` / `METRICS_UPDATE_INTERVAL` | `false` / `true` / `true` / `5.0` | Prometheus metrics switches and CPU/memory sampling interval. |

Algorithm parameters (VAD thresholds, marker gain and guard, matching rules) are versioned in code in `service/whisper_marker_transcriber.py` on purpose and cannot be set from the environment.

## Migrating from `Architect_MultiClient_Server/stt_service`

- Code moved to `<repo-root>/stt_service/`. The old directory may still hold untracked files (`.env`, `venv/`, `logs/`) after pulling. Recreate the venv here, because venv paths are absolute.
- Create a new `stt_service/.env` from `.env.example`. If you copy the old one, check `WHISPER_GIPFORMER_MODEL_PATH`: the working directory changed from `Architect_MultiClient_Server/` to `stt_service/`.
- The Nemotron, realtime, WebSocket, circuit-breaker and MongoDB settings are gone; leftover values in an old `.env` are simply ignored.
- Re-run `scripts/create-systemd-services.sh`, because the unit's `WorkingDirectory` and `ExecStart` changed.
