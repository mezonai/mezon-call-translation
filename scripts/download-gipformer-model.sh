#!/bin/bash
# Download the CPU Gipformer fallback model into a local directory.
#
# By default, downloads to models/gipformer-model (or specified via --output)
# to keep model artifacts localized and consistent with Nemotron and Kokoro.

set -e

CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
BOLD='\033[1m'
NC='\033[0m'

REPOSITORY="g-group-ai-lab/gipformer-65M-rnnt"
OUTPUT_DIR="models/gipformer-model"
FORCE=false
LIST=false
MODEL_FILES=(
    "encoder.int8.onnx"
    "decoder.int8.onnx"
    "joiner.int8.onnx"
    "tokens.txt"
)

print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo -e "${CYAN}${BOLD}    Gipformer Non-Realtime STT Fallback Downloader${NC}"
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo ""
}

print_info() { echo -e "${CYAN}ℹ️  $1${NC}"; }
print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error() { echo -e "${RED}❌ $1${NC}"; }

show_help() {
    cat << EOF
Download the CPU Gipformer fallback model used by non-realtime Whisper STT.

USAGE:
    ./scripts/download-gipformer-model.sh [options]

OPTIONS:
    -o, --output <path> Output directory (default: models/gipformer-model)
    -f, --force  Force a fresh Hugging Face download
    -l, --list   Show the configured repository and required files
    -h, --help   Show this help message

The model is saved in the specified output directory.
EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output)
            if [ -z "${2:-}" ]; then
                print_error "$1 requires a value."
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

if [ "$LIST" = true ]; then
    print_header
    echo "Repository: $REPOSITORY"
    echo "Required files:"
    for model_file in "${MODEL_FILES[@]}"; do
        echo "  - $model_file"
    done
    exit 0
fi

if command -v hf >/dev/null 2>&1; then
    DOWNLOAD_COMMAND=(hf download)
elif command -v huggingface-cli >/dev/null 2>&1; then
    DOWNLOAD_COMMAND=(huggingface-cli download)
elif python3 -m huggingface_hub.commands.huggingface_cli >/dev/null 2>&1; then
    DOWNLOAD_COMMAND=(python3 -m huggingface_hub.commands.huggingface_cli download)
elif python -m huggingface_hub.commands.huggingface_cli >/dev/null 2>&1; then
    DOWNLOAD_COMMAND=(python -m huggingface_hub.commands.huggingface_cli download)
else
    print_error "Hugging Face CLI not found."
    echo "Install it with:"
    echo "  python3 -m pip install \"huggingface-hub>=0.24.0\""
    exit 1
fi

print_header

# Get project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Resolve absolute path for output dir
if [[ "$OUTPUT_DIR" != /* ]]; then
    OUTPUT_DIR="$PROJECT_ROOT/$OUTPUT_DIR"
fi

TARGET_MODEL_DIR="$OUTPUT_DIR"
mkdir -p "$TARGET_MODEL_DIR"

print_info "Repository: $REPOSITORY"
print_info "Destination: $TARGET_MODEL_DIR"

DOWNLOAD_ARGS=("$REPOSITORY" --local-dir "$TARGET_MODEL_DIR")
if [ "$FORCE" = true ]; then
    print_warning "Forcing a fresh model download."
    DOWNLOAD_ARGS+=(--force-download)
fi

print_info "Downloading required Gipformer files..."
if ! "${DOWNLOAD_COMMAND[@]}" "${DOWNLOAD_ARGS[@]}"; then
    print_error "Download failed. Check Hugging Face access and internet connectivity."
    exit 1
fi

# Verify downloaded files
missing=false
for f in "${MODEL_FILES[@]}"; do
    if [ ! -f "$TARGET_MODEL_DIR/$f" ]; then
        print_error "Missing required file: $f"
        missing=true
    fi
done

if [ "$missing" = true ]; then
    print_error "Download completed but the complete Gipformer model cannot be verified."
    exit 1
fi

print_success "Gipformer fallback model is downloaded and ready for STT startup."
