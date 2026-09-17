#!/bin/bash
# Download the CPU Gipformer fallback model into the Hugging Face cache.
#
# This deliberately has no --output directory: the runtime resolves Gipformer
# through Hugging Face exactly as faster-whisper resolves Whisper. Keeping both
# models in the standard cache avoids a second model-location configuration.

set -e

CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
BOLD='\033[1m'
NC='\033[0m'

REPOSITORY="g-group-ai-lab/gipformer-65M-rnnt"
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

print_info() { echo -e "${CYAN}â„¹ï¸  $1${NC}"; }
print_success() { echo -e "${GREEN}âœ… $1${NC}"; }
print_warning() { echo -e "${YELLOW}âš ï¸  $1${NC}"; }
print_error() { echo -e "${RED}âŒ $1${NC}"; }

show_help() {
    cat << EOF
Download the CPU Gipformer fallback model used by non-realtime Whisper STT.

USAGE:
    ./scripts/download-gipformer-model.sh [options]

OPTIONS:
    -f, --force  Force a fresh Hugging Face download
    -l, --list   Show the configured repository and required files
    -h, --help   Show this help message

The model is saved in the active user's Hugging Face cache (or the cache
selected by HF_HOME/HUGGINGFACE_HUB_CACHE), not in models/.
EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
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

if command -v hf &> /dev/null; then
    DOWNLOAD_COMMAND=(hf download)
elif command -v huggingface-cli &> /dev/null; then
    DOWNLOAD_COMMAND=(huggingface-cli download)
else
    print_error "Hugging Face CLI not found."
    echo "Install it with:"
    echo "  python3 -m pip install \"huggingface-hub>=0.24.0\""
    exit 1
fi

print_header
print_info "Repository: $REPOSITORY"
print_info "Destination: Hugging Face cache"

DOWNLOAD_ARGS=("$REPOSITORY" "${MODEL_FILES[@]}")
if [ "$FORCE" = true ]; then
    print_warning "Forcing a fresh model download."
    DOWNLOAD_ARGS+=(--force-download)
fi

print_info "Downloading required Gipformer files..."
if ! "${DOWNLOAD_COMMAND[@]}" "${DOWNLOAD_ARGS[@]}"; then
    print_error "Download failed. Check Hugging Face access and internet connectivity."
    exit 1
fi

# A local-only read verifies every required file is available in precisely the
# same cache that stt_service will use at startup.
if ! "${DOWNLOAD_COMMAND[@]}" "$REPOSITORY" "${MODEL_FILES[@]}" --local-files-only >/dev/null; then
    print_error "Download completed but the complete Gipformer cache cannot be verified."
    exit 1
fi

print_success "Gipformer fallback model is cached and ready for STT startup."
