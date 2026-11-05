#!/usr/bin/env bash
# Set up the batch STT, orchestrator, and TTS Python services.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
STT_SERVICE_DIR="$PROJECT_ROOT/stt_service"
ORCHESTRATOR_DIR="$PROJECT_ROOT/Architect_MultiClient_Server/orchestrator_service"
TTS_SERVICE_DIR="$PROJECT_ROOT/Architect_MultiClient_Server/tts_service"
MODELS_DIR="$PROJECT_ROOT/models"
GIPFORMER_MODEL_DIR="$MODELS_DIR/gipformer-model"
KOKORO_MODEL_DIR="$MODELS_DIR/kokoro_models"
BI_ENCODER_MODEL_DIR="$MODELS_DIR/bi-encoder-model"

SKIP_MODELS=false
SKIP_VENV=false
SKIP_ENV=false
KOKORO_VOICES=""
ALL_KOKORO_VOICES=false

usage() {
    cat <<'EOF'
Usage: ./scripts/setup.sh [options]

  --skip-models          Skip model downloads
  --skip-venv            Skip Python virtual environments
  --skip-env             Skip .env creation
  --kokoro-voices LIST   Comma-separated Kokoro voices
  --all-kokoro-voices    Download all Kokoro voices
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-models) SKIP_MODELS=true; shift ;;
        --skip-venv) SKIP_VENV=true; shift ;;
        --skip-env) SKIP_ENV=true; shift ;;
        --kokoro-voices) KOKORO_VOICES="$2"; shift 2 ;;
        --all-kokoro-voices) ALL_KOKORO_VOICES=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

PYTHON_CMD="${PYTHON_CMD:-python3.12}"
if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    PYTHON_CMD=python3
fi
command -v "$PYTHON_CMD" >/dev/null 2>&1 || {
    echo "Python 3 is required" >&2
    exit 1
}

setup_env() {
    local service_dir="$1"
    if [[ ! -f "$service_dir/.env.example" ]]; then
        echo "Skipping $service_dir: no .env.example"
        return
    fi
    if [[ -f "$service_dir/.env" ]]; then
        cp "$service_dir/.env" "$service_dir/.env.backup.$(date +%Y%m%d_%H%M%S)"
    fi
    cp "$service_dir/.env.example" "$service_dir/.env"
}

set_env_value() {
    local env_file="$1" key="$2" value="$3"
    if grep -q "^${key}=" "$env_file"; then
        sed -i.tmp "s|^${key}=.*|${key}=${value}|" "$env_file"
        rm -f "$env_file.tmp"
    else
        printf '%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}

setup_venv() {
    local service_dir="$1" requirements="$2"
    [[ -f "$service_dir/$requirements" ]] || {
        echo "Skipping $service_dir: $requirements not found"
        return
    }
    "$PYTHON_CMD" -m venv "$service_dir/venv"
    "$service_dir/venv/bin/python" -m pip install --upgrade pip
    "$service_dir/venv/bin/python" -m pip install -r "$service_dir/$requirements"
}

mkdir -p "$MODELS_DIR"

if [[ "$SKIP_MODELS" == false ]]; then
    bash "$SCRIPT_DIR/download-gipformer-model.sh" --output "$GIPFORMER_MODEL_DIR"

    kokoro_args=("$SCRIPT_DIR/download-kokoro-model.sh" --output models/kokoro_models)
    if [[ "$ALL_KOKORO_VOICES" == true ]]; then
        kokoro_args+=(--all-voices)
    elif [[ -n "$KOKORO_VOICES" ]]; then
        kokoro_args+=(--voices "$KOKORO_VOICES")
    fi
    bash "${kokoro_args[@]}"

    bash "$SCRIPT_DIR/download-bi-encoder-model.sh" --output "$BI_ENCODER_MODEL_DIR"
fi

if [[ "$SKIP_ENV" == false ]]; then
    setup_env "$STT_SERVICE_DIR"
    setup_env "$ORCHESTRATOR_DIR"
    setup_env "$TTS_SERVICE_DIR"

    [[ -f "$STT_SERVICE_DIR/.env" ]] && \
        set_env_value "$STT_SERVICE_DIR/.env" WHISPER_GIPFORMER_MODEL_PATH "$GIPFORMER_MODEL_DIR"
    [[ -f "$TTS_SERVICE_DIR/.env" ]] && \
        set_env_value "$TTS_SERVICE_DIR/.env" TTS_MODEL_PATH "$KOKORO_MODEL_DIR"
    [[ -f "$ORCHESTRATOR_DIR/.env" ]] && \
        set_env_value "$ORCHESTRATOR_DIR/.env" HALLUCINATION_FILTER_MODEL_PATH \
            "$BI_ENCODER_MODEL_DIR/vietnamese_bi_encoder_int8_accurate.onnx"
fi

if [[ "$SKIP_VENV" == false ]]; then
    setup_venv "$STT_SERVICE_DIR" requirements.txt
    setup_venv "$ORCHESTRATOR_DIR" requirements-orchestrator.txt
    setup_venv "$TTS_SERVICE_DIR" requirements-tts.txt
fi

echo "Setup complete. Start STT with:"
echo "  cd $STT_SERVICE_DIR && ./venv/bin/python -m uvicorn stt_service.main:app --host 0.0.0.0 --port 8001"
