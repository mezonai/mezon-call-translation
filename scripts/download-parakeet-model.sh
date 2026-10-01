#!/bin/bash
# ==============================================================================
# Script: download-parakeet-model.sh
# Description: Download Parakeet TDT FP16 ASR model from Hugging Face into a 
#              local directory (models/parakeet-model).
#              Ensures non-realtime STT service can load entirely offline,
#              analogous to the Gipformer fallback model architecture.
#
# Default Repository: grikdotnet/parakeet-tdt-0.6b-fp16
# Default Target:     models/parakeet-model
# ==============================================================================

set -e

# ANSI Color codes for formatted terminal output
CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ------------------------------------------------------------------------------
# Default Configuration
# ------------------------------------------------------------------------------
REPOSITORY="grikdotnet/parakeet-tdt-0.6b-fp16"
OUTPUT_DIR="models/parakeet-model"
FORCE=false
LIST=false

# Essential files required by Parakeet TDT FP16 ASR Engine
MODEL_FILES=(
    "config.json"
    "decoder_joint-model.fp16.onnx"
    "encoder-model.fp16.onnx"
    "nemo128.onnx"
    "vocab.txt"
)

# ------------------------------------------------------------------------------
# Helper Functions (Logging & Help)
# ------------------------------------------------------------------------------
print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo -e "${CYAN}${BOLD}    Parakeet TDT FP16 ASR Model Downloader${NC}"
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo ""
}

print_info() { echo -e "${CYAN}ℹ️  $1${NC}"; }
print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error() { echo -e "${RED}❌ $1${NC}"; }

show_help() {
    cat << EOF
Download Parakeet TDT FP16 ASR model for offline inference in non-realtime STT.

USAGE:
    ./scripts/download-parakeet-model.sh [options]

OPTIONS:
    -o, --output <path> Target directory to save the model (default: models/parakeet-model)
    -f, --force         Force a fresh download from Hugging Face
    -l, --list          Show repository and list of required files
    -h, --help          Show this help message

NOTES:
    The model artifacts are stored locally, enabling fast server startup without
    requiring outbound internet connections to Hugging Face during runtime.
EOF
}

# ------------------------------------------------------------------------------
# Parse Command-Line Arguments
# ------------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output)
            if [ -z "${2:-}" ]; then
                print_error "$1 requires a path value."
                exit 1
            fi
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -f|--force)
            FORCE=true
            shift
            ;;
        -l|--list)
            LIST=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            echo ""
            show_help
            exit 1
            ;;
    esac
done

# List mode: display repository and required files
if [ "$LIST" = true ]; then
    print_header
    echo "Repository: $REPOSITORY"
    echo "Required files:"
    for model_file in "${MODEL_FILES[@]}"; do
        echo "  - $model_file"
    done
    exit 0
fi

# ------------------------------------------------------------------------------
# Normalize Destination Directory Path
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# If relative path, anchor to project root
if [[ "$OUTPUT_DIR" != /* && "$OUTPUT_DIR" != ?:/* && "$OUTPUT_DIR" != ?:\\* ]]; then
    OUTPUT_DIR="$PROJECT_ROOT/$OUTPUT_DIR"
fi

TARGET_MODEL_DIR="$OUTPUT_DIR"
mkdir -p "$TARGET_MODEL_DIR"

# ------------------------------------------------------------------------------
# Resolve Download Tool (CLI tool resolution: hf > huggingface-cli > venv > python snapshot)
# ------------------------------------------------------------------------------
USE_PYTHON_FALLBACK=false

if command -v hf >/dev/null 2>&1; then
    DOWNLOAD_COMMAND=(hf download)
elif [ -f "$PROJECT_ROOT/non_realtime_stt_service/venv/bin/hf" ]; then
    DOWNLOAD_COMMAND=("$PROJECT_ROOT/non_realtime_stt_service/venv/bin/hf" download)
elif [ -f "$PROJECT_ROOT/non_realtime_stt_service/venv/Scripts/hf.exe" ]; then
    DOWNLOAD_COMMAND=("$PROJECT_ROOT/non_realtime_stt_service/venv/Scripts/hf.exe" download)
elif command -v huggingface-cli >/dev/null 2>&1; then
    DOWNLOAD_COMMAND=(huggingface-cli download)
elif [ -f "$PROJECT_ROOT/non_realtime_stt_service/venv/Scripts/python.exe" ] && "$PROJECT_ROOT/non_realtime_stt_service/venv/Scripts/python.exe" -c "import huggingface_hub" >/dev/null 2>&1; then
    USE_PYTHON_FALLBACK=true
    PYTHON_EXEC="$PROJECT_ROOT/non_realtime_stt_service/venv/Scripts/python.exe"
elif [ -f "$PROJECT_ROOT/non_realtime_stt_service/venv/bin/python" ] && "$PROJECT_ROOT/non_realtime_stt_service/venv/bin/python" -c "import huggingface_hub" >/dev/null 2>&1; then
    USE_PYTHON_FALLBACK=true
    PYTHON_EXEC="$PROJECT_ROOT/non_realtime_stt_service/venv/bin/python"
elif python3 -c "import huggingface_hub" >/dev/null 2>&1; then
    USE_PYTHON_FALLBACK=true
    PYTHON_EXEC="python3"
elif python -c "import huggingface_hub" >/dev/null 2>&1; then
    USE_PYTHON_FALLBACK=true
    PYTHON_EXEC="python"
else
    print_error "Hugging Face CLI or Python huggingface_hub not found."
    echo "Please install via:"
    echo "  python -m pip install \"huggingface-hub>=0.24.0\""
    exit 1
fi

print_header
print_info "Repository: $REPOSITORY"
print_info "Target directory: $TARGET_MODEL_DIR"

# ------------------------------------------------------------------------------
# Execute Model Download from Hugging Face
# ------------------------------------------------------------------------------
print_info "Downloading Parakeet TDT FP16 model files..."

if [ "$USE_PYTHON_FALLBACK" = true ]; then
    print_info "Using Python API (huggingface_hub.snapshot_download)..."
    "$PYTHON_EXEC" -c "
import sys
from huggingface_hub import snapshot_download
force = ('$FORCE' == 'true')
snapshot_download(
    repo_id='$REPOSITORY',
    local_dir=r'$TARGET_MODEL_DIR',
    force_download=force
)
"
else
    DOWNLOAD_ARGS=("$REPOSITORY" --local-dir "$TARGET_MODEL_DIR")
    if [ "$FORCE" = true ]; then
        print_warning "Enabling force re-download flag (--force-download)."
        DOWNLOAD_ARGS+=(--force-download)
    fi

    if ! "${DOWNLOAD_COMMAND[@]}" "${DOWNLOAD_ARGS[@]}"; then
        print_error "Model download failed. Please check internet connection and access permissions."
        exit 1
    fi
fi

# ------------------------------------------------------------------------------
# Verify Integrity of Downloaded Files
# ------------------------------------------------------------------------------
missing=false
for f in "${MODEL_FILES[@]}"; do
    if [ ! -f "$TARGET_MODEL_DIR/$f" ]; then
        print_error "Missing required file: $f"
        missing=true
    fi
done

if [ "$missing" = true ]; then
    print_error "Download completed but required files for Parakeet Engine are missing."
    exit 1
fi

print_success "Parakeet TDT FP16 model is fully downloaded and ready for STT Service!"
