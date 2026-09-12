# Mezon Call Translation - Setup & Management Scripts

This directory contains scripts to help you set up and manage the Mezon Call Translation services.

## 📋 Available Scripts

### 1. `setup.sh` / `setup.ps1` - Complete Setup Script

Automates the entire setup process including:
- ✅ Downloading Nemotron and Kokoro models on Linux
- ✅ Backing up existing `.env` files
- ✅ Creating `.env` files from `.env.example`
- ✅ Updating model paths in `.env` files
- ✅ Setting up virtual environments for all services

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

The Windows setup requires the official 64-bit CPython 3.12 build. An
MSYS2/MinGW Python installation is not used because it is incompatible with
many standard Windows wheels. `-InstallDeps` installs the supported build.

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

PowerShell uses the corresponding `-SkipModels`, `-SkipVenv`, `-SkipEnv`,
`-KokoroVoices`, `-AllKokoroVoices`, and `-InstallDeps` parameters. Use
`download-nemotron-model.sh` separately for the Nemotron model.

---

### 2. `create-systemd-services.sh` - Systemd Service Creator

Creates and configures systemd service files for all three services.

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
- `mezon-orchestrator-service` - Orchestrator Service (port 8001)
- `mezon-agents-service` - Agents Service (port 8002)

---

### 3. `manage-services.sh` - Service Management Helper

Quick helper script to control all services at once.

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

---

### 5. `download-nemotron-model.sh` - Nemotron Model Downloader

Downloads the ONNX INT4 Nemotron streaming STT model from Hugging Face. The
script works on Linux, macOS, WSL, and Git Bash.

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
   - `Architect_MultiClient_Server/agents/.env`

4. **Create systemd services (optional):**
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
./venv/bin/python -m uvicorn orchestrator_service.main:app --host 0.0.0.0 --port 8001

# Terminal 3 - Agents Service
cd Architect_MultiClient_Server/agents
./venv/bin/python src/main.py
```

---

## 🔧 Systemd Service Management

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
```

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

## 🔗 Related Documentation

- [Main Project README](../README.md)
- [Nemotron ONNX INT4 model](https://huggingface.co/onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4)
- [Kokoro-82M TTS](https://huggingface.co/hexgrad/Kokoro-82M)
