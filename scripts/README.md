# Mezon Call Translation - Setup & Management Scripts

This directory contains scripts to help you set up and manage the Mezon Call Translation services.

> **Scope:** these scripts currently set up and manage **`stt_service`, `orchestrator_service`, and `tts_service`** (all Python/FastAPI) plus the Nemotron/Kokoro model downloads. They do **not** cover the Go `agents`/`worker-manager` binaries, the Go `agents-bot` service, or the `audio-ingestion/*` services — those have their own setup/deploy docs, linked below and in [Known Gaps](#-known-gaps--not-covered-by-these-scripts). The repo used to run a Python-based agent at `Architect_MultiClient_Server/agents/`; that directory is now empty and its Python setup path is dead — see the migration note under script 1 and the Known Gaps section.

## 📋 Available Scripts

### 1. `setup.sh` / `setup.ps1` - Complete Setup Script

Automates the entire setup process including:
- ✅ Downloading Nemotron and Kokoro models on Linux
- ✅ Backing up existing `.env` files
- ✅ Creating `.env` files from `.env.example`
- ✅ Updating model paths in `.env` files
- ✅ Setting up virtual environments for all services

`setup.sh` actually provisions **`stt_service`, `orchestrator_service`, and `tts_service`** (`.env` + venv for each). It also still has an `Architect_MultiClient_Server/agents` entry left over from the old Python agent — that directory is now empty (no `.env.example`, no `requirements-agent.txt`), so `setup.sh` just prints a "not found, skipping" warning for it and moves on; it's a harmless no-op, not a working setup path. The replacement Go `agents`/`worker-manager` binaries and the Go `agents-bot` service are **not** Python venv services and are not touched by this script at all — build/configure/run them per `agents/README.md` and `agents-bot/README.md`.

> **`setup.ps1` does not exist in this repository.** Only `setup.sh` is present in `scripts/`. The PowerShell usage below documents an intended/expected interface — until `setup.ps1` is actually added, Windows users should run `setup.sh` from WSL or Git Bash instead.

#### Usage

```bash
# Full setup with defaults
./scripts/setup.sh

# Skip model downloads (if already downloaded)
./scripts/setup.sh --skip-models

# Use a different Nemotron model directory name
./scripts/setup.sh --nemotron-model nemotron-3.5-asr-streaming-0.6b-onnx-int4

# Download all Kokoro voices
./scripts/setup.sh --all-kokoro-voices

# Skip virtual environment setup
./scripts/setup.sh --skip-venv

# Skip .env file creation
./scripts/setup.sh --skip-env
```

Windows PowerShell:

```powershell
# Full setup with defaults
.\scripts\setup.ps1

# Install missing Python 3.12 and FFmpeg with winget first
.\scripts\setup.ps1 -InstallDeps

# Skip selected setup stages
.\scripts\setup.ps1 -SkipModels
.\scripts\setup.ps1 -SkipVenv
.\scripts\setup.ps1 -SkipEnv

# The PowerShell setup still has legacy Vosk model logic. Skip that stage,
# then use the Nemotron downloader from Git Bash or WSL as documented below.
.\scripts\setup.ps1 -SkipModels
python -m pip install "huggingface-hub>=0.24.0"
bash ./scripts/download-nemotron-model.sh

# Select Kokoro voices
.\scripts\setup.ps1 -KokoroVoices 'af_heart,am_adam'
.\scripts\setup.ps1 -AllKokoroVoices
```

The Windows setup requires the official 64-bit CPython 3.12 build. An MSYS2/MinGW Python installation is not used because it is incompatible with many standard Windows wheels. `-InstallDeps` installs the supported build.

If script execution is disabled for the current PowerShell process, use:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
```

#### Options

- `--skip-models` - Skip model downloads
- `--skip-venv` - Skip virtual environment setup
- `--skip-env` - Skip .env file creation
- `--nemotron-model <name>` - Specify the Nemotron model directory name (default: `nemotron-3.5-asr-streaming-0.6b-onnx-int4`)
- `--kokoro-voices <list>` - Comma-separated Kokoro voice names
- `--all-kokoro-voices` - Download all Kokoro voices
- `-h, --help` - Show help message

PowerShell uses the corresponding `-SkipModels`, `-SkipVenv`, `-SkipEnv`, `-KokoroVoices`, `-AllKokoroVoices`, and `-InstallDeps` parameters. Use `download-nemotron-model.sh` separately for the Nemotron model.

---

### 2. `create-systemd-services.sh` - Systemd Service Creator

Creates and configures systemd service files. As written today it only creates units for **`stt_service`** and **`orchestrator_service`**, plus a third `mezon-agents-service` unit that points at the now-empty `Architect_MultiClient_Server/agents` (dead — see below); a `create_tts_service` function exists in the script (would create `mezon-tts-service` on port 8008) but is never actually invoked, so **no `tts_service` unit is created**, even though the script's own pre-flight validation checks for a `tts_service` venv.

#### Usage

```bash
# Create services (requires sudo)
sudo ./scripts/create-systemd-services.sh

# Create and enable services
sudo ./scripts/create-systemd-services.sh --enable

# Create, enable, and start services
sudo ./scripts/create-systemd-services.sh --enable --start

# Dry run to see what would be created
sudo ./scripts/create-systemd-services.sh --dry-run
```

#### Options

- `--user <username>` - User to run services as (default: current user)
- `--group <groupname>` - Group to run services as (default: current user's group)
- `--enable` - Enable services to start on boot
- `--start` - Start services immediately after creation
- `--dry-run` - Show what would be created without actually creating
- `-h, --help` - Show help message

#### Created Services

- `mezon-stt-service` - STT Service (port 8000)
- `mezon-orchestrator-service` - Orchestrator Service (port 8002 — this is what the generated unit and the service's own `AGENT_PORT` default actually use; older docs in this repo said 8001, that was wrong)
- `mezon-agents-service` - **Broken.** Its `ExecStart` runs `Architect_MultiClient_Server/agents/venv/bin/python .../main.py`, but that directory is now empty (the old Python agent it configured was replaced by the Go binaries below). The unit will be created but will fail to start, and the script's own `validate_setup` pre-flight check will fail on this directory's missing venv unless you pass `--skip-validation`.

**Not created by this script:** the Go `agents`/`worker-manager` binaries and `agents-bot` have their own systemd setup — see [`agents/deploy/systemd/README.md`](../agents/deploy/systemd/README.md) (`worker-manager` is the long-lived unit; it spawns/kills `agent` subprocesses itself). `agents-bot` currently has no documented deployment method in this repo (no systemd unit or Dockerfile exists for it yet).

---

### 3. `manage-services.sh` - Service Management Helper

Quick helper script to control all services at once. It hardcodes the same three systemd unit names `create-systemd-services.sh` creates (`mezon-stt-service`, `mezon-orchestrator-service`, `mezon-agents-service`) — so it does **not** manage `tts_service` or the Go `agents`/`worker-manager`/`agents-bot` binaries, and `mezon-agents-service` here is the dead unit described above, not the new Go agent.

#### Usage

```bash
# Start all services
sudo ./scripts/manage-services.sh start

# Stop all services
sudo ./scripts/manage-services.sh stop

# Restart all services
sudo ./scripts/manage-services.sh restart

# Check status of all services
sudo ./scripts/manage-services.sh status

# Enable all services to start on boot
sudo ./scripts/manage-services.sh enable

# Disable all services from starting on boot
sudo ./scripts/manage-services.sh disable

# Show recent logs (last 20 lines from each service)
sudo ./scripts/manage-services.sh logs

# Follow logs in real-time
sudo ./scripts/manage-services.sh follow
```

---

### 4. `health-check.sh` - System Health Check

Validates that all components are properly set up and running.

#### Usage

```bash
# Run health check
./scripts/health-check.sh
```

#### What it checks

- ✅ Python installation
- ✅ Nemotron and Kokoro models
- ✅ Service directories
- ✅ Virtual environments
- ✅ .env files
- ✅ Requirements files
- ✅ Systemd services (if on Linux)
- ✅ Network ports (8000, 8001, 8002)

The script will provide a summary with passed, warning, and failed checks, along with recommendations for fixing issues.

**Known gaps in this script (not fixed here, documenting current behavior):**
- It only checks `stt_service`, `orchestrator_service`, and the dead `Architect_MultiClient_Server/agents` directory — `tts_service` isn't checked at all.
- Its hardcoded port checks (8000/8001/8002) label 8001 as "Orchestrator Service", but `orchestrator_service`'s actual default port (and what `create-systemd-services.sh` generates) is **8002**. Expect a false "port not in use" warning on a correctly running orchestrator, not a real problem.
- Its Kokoro-model check looks for `kokoro-v0_19.pth` (or `kokoro.onnx`), but `download-kokoro-model.sh` now downloads `kokoro-v1_0.pth`. This check will report the Kokoro model as **not found even after a successful download** — a stale filename check, not a real failure. `setup.sh` has the same stale check when deciding whether to skip re-downloading, so it will silently re-download the model (with `--force`) on every run instead of detecting the existing install.

---

### 5. `download-nemotron-model.sh` - Nemotron Model Downloader

Downloads the ONNX INT4 Nemotron streaming STT model from Hugging Face. The script works on Linux, macOS, WSL, and Git Bash.

#### Prerequisite

```bash
python -m pip install "huggingface-hub>=0.24.0"
```

#### Usage

```bash
# Download the default model to models/nemotron-model/
bash scripts/download-nemotron-model.sh

# Select a different repository and local model directory name
bash scripts/download-nemotron-model.sh \
  --repository onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4 \
  --model nemotron-3.5-asr-streaming-0.6b-onnx-int4

# Download under a different parent directory
bash scripts/download-nemotron-model.sh --output /opt/mezon/models/nemotron-model

# Force a fresh download and replace the installed model only after success
bash scripts/download-nemotron-model.sh --force

# Show the configured repository and Nemotron model discovery link
bash scripts/download-nemotron-model.sh --list
```

On Windows, run the shell script from WSL or Git Bash:

```powershell
python -m pip install "huggingface-hub>=0.24.0"
bash ./scripts/download-nemotron-model.sh
```

The default result is:

```text
models/
└── nemotron-model/
    └── nemotron-3.5-asr-streaming-0.6b-onnx-int4/
        ├── genai_config.json
        ├── encoder.onnx
        ├── encoder.onnx.data
        ├── decoder.onnx
        ├── decoder.onnx.data
        ├── joint.onnx
        ├── joint.onnx.data
        └── tokenizer.json
```

Configure the STT service with either the default directory name:

```dotenv
NEMOTRON_MODEL_PATH=nemotron-3.5-asr-streaming-0.6b-onnx-int4
```

or an absolute path:

```dotenv
NEMOTRON_MODEL_PATH=/opt/mezon/models/nemotron-model/nemotron-3.5-asr-streaming-0.6b-onnx-int4
```

Verify the download:

```bash
test -f models/nemotron-model/nemotron-3.5-asr-streaming-0.6b-onnx-int4/genai_config.json \
  && echo "Nemotron model found"
```

---

### 6. `download-kokoro-model.sh` - Kokoro TTS Model Downloader

Downloads Kokoro-82M TTS models and voices.

#### Usage

```bash
# Download model with default voices
./scripts/download-kokoro-model.sh

# Download specific voices
./scripts/download-kokoro-model.sh -v "af_heart,af_bella,am_adam"

# Download all available voices
./scripts/download-kokoro-model.sh --all-voices

# List downloaded voices
./scripts/download-kokoro-model.sh --list

# Show model information
./scripts/download-kokoro-model.sh --info
```

#### Available Voices

- **American Female**: af_heart, af_bella, af_sarah, af_nicole, af_sky
- **American Male**: am_adam, am_michael, am_liam
- **British Female**: bf_emma, bf_isabella
- **British Male**: bm_george, bm_lewis

---

## 🚀 Quick Start Guide

### First Time Setup

1. **Run the setup script:**
   ```bash
   chmod +x scripts/*.sh
   ./scripts/setup.sh
   ```

2. **Verify the setup:**
   ```bash
   ./scripts/health-check.sh
   ```

3. **Review and update `.env` files:**
   - `Architect_MultiClient_Server/stt_service/.env`
   - `Architect_MultiClient_Server/orchestrator_service/.env`
   - `Architect_MultiClient_Server/tts_service/.env`
   - ~~`Architect_MultiClient_Server/agents/.env`~~ — dead, this directory is empty. The old Python agent it used to configure has been replaced by the Go `agents`/`worker-manager` binaries (own `.env`, see `agents/README.md`) and the Go `agents-bot` service (own `.env`, see `agents-bot/README.md`). Neither is set up by `setup.sh`.

4. **Create systemd services (optional, only covers stt_service + orchestrator_service today):**
   ```bash
   sudo ./scripts/create-systemd-services.sh --enable --start
   ```

### Manual Service Start (without systemd)

```bash
# Terminal 1 - STT Service
cd Architect_MultiClient_Server/stt_service
./venv/bin/python -m uvicorn stt_service.main:app --host 0.0.0.0 --port 8000

# Terminal 2 - Orchestrator Service
cd Architect_MultiClient_Server/orchestrator_service
./venv/bin/python -m uvicorn orchestrator_service.main:app --host 0.0.0.0 --port 8002

# Terminal 3 - TTS Service
cd Architect_MultiClient_Server/tts_service
./venv/bin/python -m uvicorn tts_service.main:app --host 0.0.0.0 --port 8008
```

The old "Terminal 3 - Agents Service" step (`Architect_MultiClient_Server/agents/venv/bin/python src/main.py`) is gone — that directory no longer contains a Python agent. To run the replacement Go services manually, see `agents/README.md` (`cmd/agent` and `cmd/worker-manager`) and `agents-bot/README.md` for their own build/run instructions; they are not part of this setup script's flow.

---

## 🔧 Systemd Service Management

> As above, `mezon-agents-service` is the dead Python-agent unit (see script 2) — it exists if you ran `create-systemd-services.sh`, but won't start. There is no systemd unit here for `tts_service` or the Go `agents`/`worker-manager`/`agents-bot` binaries; see their own docs linked above.

### Individual Service Commands

```bash
# Start a service
sudo systemctl start mezon-stt-service
sudo systemctl start mezon-orchestrator-service
sudo systemctl start mezon-agents-service

# Stop a service
sudo systemctl stop mezon-stt-service

# Restart a service
sudo systemctl restart mezon-stt-service

# Check service status
sudo systemctl status mezon-stt-service

# Enable service to start on boot
sudo systemctl enable mezon-stt-service

# View service logs
sudo journalctl -u mezon-stt-service -f
sudo journalctl -u mezon-stt-service -n 50
```

### All Services at Once

```bash
# Use the manage-services.sh script
sudo ./scripts/manage-services.sh start
sudo ./scripts/manage-services.sh stop
sudo ./scripts/manage-services.sh restart
sudo ./scripts/manage-services.sh status
sudo ./scripts/manage-services.sh logs
sudo ./scripts/manage-services.sh follow
```

---

## 📁 Directory Structure

```
scripts/
├── README.md                          # This file
├── setup.sh                           # Complete setup script
├── create-systemd-services.sh         # Systemd service creator
├── manage-services.sh                 # Service management helper
├── health-check.sh                    # System health check
├── download-nemotron-model.sh         # Nemotron model downloader
├── download-kokoro-model.sh           # Kokoro model downloader
├── edit_env.sh                        # Undocumented helper to set KEY=VALUE pairs in a service .env
```

`edit_env.sh` isn't part of the numbered list above (it predates it and was never folded in). Its `--agent` target still points at `Architect_MultiClient_Server/agents/.env` — the now-empty, dead Python-agent directory — so that target is currently useless; `--orchestrator` and `--stt` still work.

---

## 🐛 Troubleshooting

### Services won't start

1. Check service status:
   ```bash
   sudo systemctl status mezon-stt-service
   ```

2. View detailed logs:
   ```bash
   sudo journalctl -u mezon-stt-service -n 100
   ```

3. Check if virtual environment exists:
   ```bash
   ls -la Architect_MultiClient_Server/stt_service/venv
   ```

4. Verify .env file exists and is configured:
   ```bash
   cat Architect_MultiClient_Server/stt_service/.env
   ```

### Models not found

1. Check if models are downloaded:
   ```bash
   ls -la models/nemotron-model/nemotron-3.5-asr-streaming-0.6b-onnx-int4/
   ls -la models/kokoro_models/
   ```

2. Re-download models:
   ```bash
   python -m pip install "huggingface-hub>=0.24.0"
   bash scripts/download-nemotron-model.sh
   ./scripts/download-kokoro-model.sh --force
   ```

### Permission issues

1. Make scripts executable:
   ```bash
   chmod +x scripts/*.sh
   ```

2. Check file ownership:
   ```bash
   ls -la Architect_MultiClient_Server/*/venv
   ```

3. Fix ownership if needed:
   ```bash
   sudo chown -R $USER:$USER Architect_MultiClient_Server/
   ```

---

## 📝 Notes

- All scripts support `--help` flag for detailed usage information
- The setup script automatically backs up existing `.env` files with timestamps
- Systemd services are configured with automatic restart on failure
- Services run with security hardening (NoNewPrivileges, PrivateTmp, etc.)
- Logs are available through systemd journal (`journalctl`)

---

## ⚠️ Known Gaps / Not Covered by These Scripts

This directory's scripts are the setup path for `stt_service`, `orchestrator_service`, and `tts_service` only. As part of the ongoing migration to `mezon-sfu`, several other components exist in this repo but are **not** wired into `setup.sh` / `create-systemd-services.sh` / `manage-services.sh` / `health-check.sh`, and have their own separate docs instead of being duplicated here:

- **`agents/` (Go `cmd/agent` + `cmd/worker-manager`)** — replaces the old Python agent that used to live at `Architect_MultiClient_Server/agents/` (now empty). Compiled Go binaries, not a Python venv service. Build/run/config: [`agents/README.md`](../agents/README.md); systemd deploy: [`agents/deploy/systemd/README.md`](../agents/deploy/systemd/README.md).
- **`agents-bot/`** — separate Go service that bridges Mezon chat/identity events. Build/run/config: [`agents-bot/README.md`](../agents-bot/README.md). No systemd unit or Dockerfile exists for it in this repo yet — it has no documented deployment method at all currently.
- **`audio-ingestion/record-service` and `audio-ingestion/audio-processing-service`** — deployed via their own systemd docs, not these scripts: [`audio-ingestion/record-service/deploy/systemd/README.md`](../audio-ingestion/record-service/deploy/systemd/README.md) and [`audio-ingestion/audio-processing-service/deploy/systemd/README.md`](../audio-ingestion/audio-processing-service/deploy/systemd/README.md).
- **`setup.ps1`** — referenced in script 1's usage docs but does not exist in this repository; only `setup.sh` is present.
- **`tts_service` systemd unit** — `create-systemd-services.sh` contains a `create_tts_service` function but never calls it, so no `mezon-tts-service` unit is ever created even though `setup.sh` does provision the service's venv/`.env`.
- **Stale Kokoro filename checks** — `setup.sh` and `health-check.sh` look for `kokoro-v0_19.pth`, but `download-kokoro-model.sh` now fetches `kokoro-v1_0.pth`; both checks are effectively always-false against a current install (see script 4's section above).
- **Dead `Architect_MultiClient_Server/agents` references** — still present in `setup.sh`, `health-check.sh`, `create-systemd-services.sh`, and `edit_env.sh --agent`, all now no-ops or broken since that directory is empty.

None of the above were fixed as part of this doc update — they're documented here so they aren't mistaken for working coverage.

---

## 🔗 Related Documentation

- [Main Project README](../README.md)
- [Nemotron ONNX INT4 model](https://huggingface.co/onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4)
- [Kokoro-82M TTS](https://huggingface.co/hexgrad/Kokoro-82M)
- [`agents/README.md`](../agents/README.md) — Go `cmd/agent` / `cmd/worker-manager` (replaces the old Python agent), including their systemd deploy doc
- [`agents-bot/README.md`](../agents-bot/README.md) — Go Mezon chat/identity bridge service
- [`audio-ingestion/record-service/deploy/systemd/README.md`](../audio-ingestion/record-service/deploy/systemd/README.md) and [`audio-ingestion/audio-processing-service/deploy/systemd/README.md`](../audio-ingestion/audio-processing-service/deploy/systemd/README.md)

