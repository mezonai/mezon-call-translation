#!/bin/bash
# Download ONNX INT4 Nemotron Streaming STT Model
# Pure Bash implementation using the Hugging Face CLI

set -e

# Colors
CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Default values
MODEL_NAME="nemotron-3.5-asr-streaming-0.6b-onnx-int4"
OUTPUT_DIR="models/nemotron-model"
REPOSITORY="onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4"
FORCE=false
LIST=false
MODELS_URL="https://huggingface.co/models?search=nemotron%20asr"

# Functions
print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo -e "${CYAN}${BOLD}    Nemotron Streaming STT Model Downloader${NC}"
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo ""
}

print_info() {
    echo -e "${CYAN}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

show_help() {
    cat << EOF
Download ONNX INT4 Nemotron Streaming STT Model

USAGE:
    ./scripts/download-nemotron-model.sh [options]

OPTIONS:
    -m, --model <name>        Local model directory name
                              (default: $MODEL_NAME)
    -r, --repository <repo>   Hugging Face repository ID
                              (default: $REPOSITORY)
    -o, --output <path>       Output directory
                              (default: $OUTPUT_DIR)
    -f, --force               Force re-download even if the model exists
    -l, --list                Show the model repository and discovery link
    -h, --help                Show this help message

EXAMPLES:
    # Download the default model
    ./scripts/download-nemotron-model.sh

    # Download into a custom parent directory
    ./scripts/download-nemotron-model.sh --output /opt/mezon/models/nemotron-model

    # Select a repository and local directory name
    ./scripts/download-nemotron-model.sh \
        --repository onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4 \
        --model nemotron-3.5-asr-streaming-0.6b-onnx-int4

    # Force re-download
    ./scripts/download-nemotron-model.sh --force

EOF
}

require_value() {
    if [ -z "${2:-}" ]; then
        print_error "$1 requires a value."
        exit 1
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -m|--model)
            require_value "$1" "${2:-}"
            MODEL_NAME="$2"
            shift 2
            ;;
        -r|--repository)
            require_value "$1" "${2:-}"
            REPOSITORY="$2"
            shift 2
            ;;
        -o|--output)
            require_value "$1" "${2:-}"
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

# Handle list
if [ "$LIST" = true ]; then
    print_header
    echo "Configured model:"
    echo "  - $REPOSITORY"
    echo ""
    echo "Other Nemotron ASR models can be found at:"
    echo -e "${CYAN}$MODELS_URL${NC}"
    exit 0
fi

# A model name must be one directory name so --force cannot remove an
# unexpected path outside OUTPUT_DIR.
case "$MODEL_NAME" in
    ""|"."|".."|*/*|*\\*)
        print_error "Model name must be a single directory name: $MODEL_NAME"
        exit 1
        ;;
esac

# Get project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Resolve absolute path for output dir
if [[ "$OUTPUT_DIR" != /* ]]; then
    OUTPUT_DIR="$PROJECT_ROOT/$OUTPUT_DIR"
fi

print_header

# Check tools
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

# Target directory
TARGET_MODEL_DIR="$OUTPUT_DIR/$MODEL_NAME"

print_info "Repository: $REPOSITORY"
print_info "Model: $MODEL_NAME"
print_info "Output: $OUTPUT_DIR"

# Check if model exists
if [ -d "$TARGET_MODEL_DIR" ]; then
    if [ "$FORCE" = true ]; then
        print_warning "Model directory exists and will be replaced after a successful download."
    elif [ -f "$TARGET_MODEL_DIR/genai_config.json" ]; then
        print_success "Model already exists at: $TARGET_MODEL_DIR"
        exit 0
    else
        print_error "An incomplete model directory exists at: $TARGET_MODEL_DIR"
        print_info "Run again with --force to replace it."
        exit 1
    fi
fi

# Create temp dir
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT
TEMP_MODEL_DIR="$TEMP_DIR/$MODEL_NAME"
mkdir -p "$TEMP_MODEL_DIR"

# Download
print_info "Downloading model snapshot..."
DOWNLOAD_ARGS=("$REPOSITORY" --local-dir "$TEMP_MODEL_DIR")
if [ "$FORCE" = true ]; then
    DOWNLOAD_ARGS+=(--force-download)
fi

if ! "${DOWNLOAD_COMMAND[@]}" "${DOWNLOAD_ARGS[@]}"; then
    print_error "Download failed. Check the repository ID, authentication, or internet connection."
    exit 1
fi

# Validate
if [ ! -f "$TEMP_MODEL_DIR/genai_config.json" ]; then
    print_error "Downloaded snapshot does not contain genai_config.json."
    exit 1
fi

# Move to final destination
mkdir -p "$OUTPUT_DIR"
if [ -d "$TARGET_MODEL_DIR" ]; then
    rm -rf "$TARGET_MODEL_DIR"
fi
mv "$TEMP_MODEL_DIR" "$TARGET_MODEL_DIR"

print_success "Model installed successfully!"
echo "Path: $TARGET_MODEL_DIR"
