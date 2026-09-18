# tts_service

A small FastAPI service that turns text into speech using the [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model. It exposes exactly one thing that matters: an HTTP endpoint that takes text and returns raw PCM audio.

## Why a separate service

The Go `agents` binary (repo-root `agents/`; `Architect_MultiClient_Server/agents` is now just an empty placeholder directory, the old Python agent code having already been removed) calls this service over HTTP whenever a room's agent runs with `AGENT_ROLE=speaker` — see `agents/internal/ttsclient/client.go`, whose package doc says it's "ported from the old Python agent's `process_text_to_audio`". `ttsclient.Synthesize` POSTs to `{TTS_SERVICE_BASE_URL}/api/tts/process` and treats the response body as raw S16LE PCM at a sample rate it has to already know (`TTS_SAMPLE_RATE`, default `24000`, documented in `agents/README.md` as "Kokoro's default" — confirmed below, this service hardcodes 24000Hz).

Keeping it a separate microservice rather than embedding Kokoro in the agent process means: the model (PyTorch + `kokoro` package, GPU/CPU inference) only needs to be loaded once per host regardless of how many rooms/agents are running, agent processes stay small Go binaries with no Python/PyTorch dependency, and the two can be scaled/restarted independently.

## Requirements & setup

- Python 3.12 (matches the rest of `Architect_MultiClient_Server`; see repo-root `scripts/setup.sh`).
- Dependencies in `requirements-tts.txt` — notably `kokoro>=0.9.4`, `torch`/`torchaudio`, `fastapi`, `cachetools`. Install with:
  ```bash
  cd Architect_MultiClient_Server/tts_service
  python3.12 -m venv venv
  source venv/bin/activate
  pip install -r requirements-tts.txt
  ```
- **Kokoro model files.** The engine (`services/tts_engine.py`) loads them via `kokoro.KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M')` with `HF_HOME` pointed at the parent of the configured model directory, so the files must already be present on disk — this is not an on-demand download at request time. Fetch them with the repo-root script:
  ```bash
  ./scripts/download-kokoro-model.sh          # Linux/macOS
  .\scripts\download-kokoro-model.ps1          # Windows
  ```
  By default this downloads `kokoro-v1_0.pth`, `config.json`, and five voices (`af_heart`, `af_bella`, `af_sarah`, `am_adam`, `am_michael`) into `<repo-root>/models/kokoro_models`. Use `-a`/`--all-voices` to also get the other four voices this service's `KokoroVoice` enum knows about (`bf_emma`, `bf_isabella`, `bm_george`, `bm_lewis`).
  - **Path caveat:** the script writes to `<repo-root>/models/kokoro_models`, but this service's own default (`TTS_MODEL_PATH`, see below) is the relative path `models/kokoro_models`, resolved against whatever the process's current working directory is at startup. These only agree if you run the service with `Architect_MultiClient_Server/` as the cwd (as the systemd unit in `scripts/create-systemd-services.sh` does) — and even then, the script's `<repo-root>/models` is one directory level above `Architect_MultiClient_Server/`, so a bare relative default will NOT find it. `scripts/setup.sh` papers over this by rewriting `TTS_MODEL_PATH` in `.env` to an absolute path after downloading (`KOKORO_MODEL_PATH="$MODELS_DIR"`, i.e. `<repo-root>/models/kokoro_models`). If you're not going through `setup.sh`, set `TTS_MODEL_PATH` to an absolute path yourself rather than relying on the shipped relative default.
  - If `services/tts_engine.py`'s `load()` doesn't find `model_dir`, it logs a warning and returns `False`, which makes `main.py`'s FastAPI `lifespan` raise `RuntimeError("Failed to load TTS engine")` at startup — the process will not come up serving requests without the model.

## Configuration

Read in `config/app_config.py` (`TTSConfig`, a dataclass populated from environment variables at construction time — see also `.env.example`):

| Var | Required | Default | Meaning |
|---|---|---|---|
| `TTS_MODEL_PATH` | no | `models/kokoro_models` | Directory containing the Kokoro model files (`kokoro-v1_0.pth`, `config.json`, `voices/*.pt`). See the path caveat above. |
| `TTS_LFU_CACHE_MAXSIZE` | no | `50` | Max entries in the in-process least-frequently-used synthesis cache (`cachetools.LFUCache`), keyed by a SHA-256 hash of normalized text + voice + speed. |

`logger.py` also reads `LOG_LEVEL` (default `INFO`), same convention as the other Python services in this repo.

There is no port/host configuration read from the environment — that's an ASGI-server concern (see below), not something this codebase parameterizes itself.

## Running the service

```bash
python -m uvicorn tts_service.main:app --host 0.0.0.0 --port 8008
```
Must be run with `Architect_MultiClient_Server/` as the working directory (or otherwise on `PYTHONPATH`), since `main.py` and everything else import with the `tts_service.` package prefix (`from tts_service.api.tts_api import router...`). Port `8008` is not a default baked into this service — it's the convention used by `scripts/create-systemd-services.sh` and matches `agents/README.md`'s documented default for `TTS_SERVICE_BASE_URL` (`http://localhost:8008`).

On startup the FastAPI `lifespan` handler (`main.py`) loads the Kokoro pipeline once via `get_tts_engine().load()`; on shutdown it calls `engine.cleanup()`, which drops the pipeline reference and clears the synthesis cache.

## API

### `POST /api/tts/process`

Defined in `api/tts_api.py`.

Request body (JSON):
```json
{
  "text": "Hello, world",
  "voice": "af_heart",
  "speed": 1.0
}
```
- `text` (string, required).
- `voice` (string, optional) — one of the `KokoroVoice` enum values: `af_heart`, `af_bella`, `af_sarah`, `am_adam`, `am_michael`, `bf_emma`, `bf_isabella`, `bm_george`, `bm_lewis`. Defaults to `af_heart` if omitted.
- `speed` (float, optional) — validated to the range `0.5`–`2.0` by Pydantic (`Field(ge=0.5, le=2.0)`); a value outside that range is rejected with a 422. Defaults to `1.0` if omitted.

Response: `200 OK`, `Content-Type: application/octet-stream`, body is raw **signed 16-bit little-endian PCM, mono, 24000 Hz** — the engine synthesizes as 32-bit float (`np.ndarray`) and `tts_api.py` converts it in place with `(audio * 32767).astype(np.int16)` before returning the bytes. The sample rate is fixed by `TTSEngine.__init__`'s `sample_rate: int = 24000` default (never overridden elsewhere in this codebase) — it is **not** included anywhere in the response, matching what `agents/internal/ttsclient` and `agents/README.md`'s `TTS_SAMPLE_RATE` note already assume.

Repeated requests with the same normalized text + voice + speed are served from the in-process LFU cache (`services/tts_engine.py`'s `_make_cache_key`/`_normalize_text`) instead of re-running inference.

There is no separate health-check endpoint in `api/tts_api.py` — startup failure is the only signal (the process refuses to come up if the model fails to load; see above).

## Layout

- `main.py` — FastAPI app, CORS (wide open, `allow_origins=["*"]`), lifespan-managed model load/cleanup, mounts the API router under `/api`.
- `api/tts_api.py` — the single `POST /tts/process` route and its Pydantic request model.
- `services/tts_engine.py` — `TTSEngine`: loads the Kokoro `KPipeline`, runs synthesis (splitting on `\n+`), and owns the LFU result cache; `KokoroVoice` enum; `get_tts_engine()` singleton accessor (`functools.lru_cache`).
- `config/app_config.py` — `TTSConfig` dataclass reading `TTS_MODEL_PATH` / `TTS_LFU_CACHE_MAXSIZE` from the environment.
- `logger.py` — stdlib `logging` setup shared by this service (console handler, `LOG_LEVEL`-controlled level); note it also unconditionally sets up a rotating file handler for a logger named `metrics`, though nothing in this service currently logs through that name.
- `requirements-tts.txt` — pinned dependency list; includes some entries (`websockets`, `pyaudio`, `librosa`, `scipy`) that nothing in this service's actual code imports, suggesting the file was copied from another service (e.g. `stt_service`) and not pruned.
- `.env.example` — sole documented var, `TTS_MODEL_PATH`.

## Testing

There is no `tests/` directory and no automated tests for this service at all currently — not the engine's synthesis path, not the API layer, not the config loading. This mirrors the gap already tracked for `orchestrator_service`/`stt_service`.

## Relation to other services

- **`agents` (Go, repo-root `agents/`)** — the only known caller. `internal/ttsclient` HTTP-POSTs to this service's `/api/tts/process` when a room's agent has `AGENT_ROLE=speaker`, then hands the returned PCM to `internal/ttsplayer` for Opus encoding and publish-back into the call. Configured via that binary's `TTS_SERVICE_BASE_URL` / `TTS_SAMPLE_RATE` env vars (see `agents/README.md`).
- **`orchestrator_service`** — per `agents/README.md`, `orchestrator_service` is what triggers a `tts_play` request in the first place (SSE agent-request), but this `tts_service` has no direct dependency on or knowledge of the orchestrator; it only ever sees the HTTP call from `agents`.
- **`stt_service`** — no runtime relationship; mentioned only because `requirements-tts.txt` appears to share leftover dependencies with it (see Layout above).

