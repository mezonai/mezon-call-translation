#!/bin/bash
# Download Vosk STT Model
# Pure Bash implementation

set -e

# Colors
CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Default values
MODEL_NAME="vosk-model-small-en-us-0.15"
OUTPUT_DIR="models/vosk-model"
FORCE=false
LIST=false
BASE_URL="https://alphacephei.com/vosk/models"

# Functions
print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo -e "${CYAN}${BOLD}    Vosk STT Model Downloader${NC}"
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
Download Vosk STT Model

USAGE:
    ./scripts/download-vosk-model.sh [options]

OPTIONS:
    -m, --model <name>      Model name to download (default: vosk-model-small-en-us-0.15)
    -o, --output <path>     Output directory (default: models/vosk-model)
    -f, --force             Force re-download even if model exists
    -l, --list              List available models (links to website)
    -h, --help              Show this help message

EXAMPLES:
    # Download default small model
    ./scripts/download-vosk-model.sh

    # Download large model
    ./scripts/download-vosk-model.sh -m vosk-model-en-us-0.22

    # Force re-download
    ./scripts/download-vosk-model.sh --force

EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -m|--model)
            MODEL_NAME="$2"
            shift 2
            ;;
        -o|--output)
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
    echo "Available models can be found at:"
    echo -e "${CYAN}https://alphacephei.com/vosk/models${NC}"
    echo ""
    echo "Common models:"
    echo "  - vosk-model-small-en-us-0.15 (Lightweight, ~40MB)"
    echo "  - vosk-model-en-us-0.22 (Accurate, ~1.8GB)"
    echo "  - vosk-model-cn-0.22 (Chinese, ~1.3GB)"
    exit 0
fi

# Get project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Resolve absolute path for output dir
if [[ "$OUTPUT_DIR" != /* ]]; then
    OUTPUT_DIR="$PROJECT_ROOT/$OUTPUT_DIR"
fi

print_header

# Check tools
if ! command -v wget &> /dev/null && ! command -v curl &> /dev/null; then
    print_error "Neither wget nor curl found. Please install one of them."
    exit 1
fi

if ! command -v unzip &> /dev/null; then
    print_error "unzip not found. Please install unzip."
    exit 1
fi

# Target directory
TARGET_MODEL_DIR="$OUTPUT_DIR/$MODEL_NAME"

print_info "Model: $MODEL_NAME"
print_info "Output: $OUTPUT_DIR"

# Check if model exists
if [ -d "$TARGET_MODEL_DIR" ]; then
    if [ "$FORCE" = true ]; then
        print_warning "Model directory exists, removing for re-download..."
        rm -rf "$TARGET_MODEL_DIR"
    else
        print_success "Model already exists at: $TARGET_MODEL_DIR"
        exit 0
    fi
fi

# Create temp dir
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

# Download
print_info "Downloading model archive..."
ARCHIVE_URL="$BASE_URL/$MODEL_NAME.zip"
ARCHIVE_FILE="$TEMP_DIR/$MODEL_NAME.zip"

if command -v wget &> /dev/null; then
    wget -q --show-progress "$ARCHIVE_URL" -O "$ARCHIVE_FILE" || {
        print_error "Download failed. Check model name or internet connection."
        exit 1
    }
else
    curl -L "$ARCHIVE_URL" -o "$ARCHIVE_FILE" || {
        print_error "Download failed. Check model name or internet connection."
        exit 1
    }
fi

# Unzip
print_info "Extracting model..."
unzip -q "$ARCHIVE_FILE" -d "$TEMP_DIR"

# Move to final destination
mkdir -p "$OUTPUT_DIR"
if [ -d "$TEMP_DIR/$MODEL_NAME" ]; then
    # Some archives contain a folder with the model name
    mv "$TEMP_DIR/$MODEL_NAME" "$OUTPUT_DIR/"
else
    # Some might extract contents directly? Usually they have a folder.
    # Let's try to find the folder relative to unzipped root
    EXTRACTED_DIR=$(find "$TEMP_DIR" -maxdepth 1 -type d ! -path "$TEMP_DIR" | head -n 1)
    if [ -n "$EXTRACTED_DIR" ]; then
        mv "$EXTRACTED_DIR" "$OUTPUT_DIR/$MODEL_NAME"
    else
        print_error "Could not find extracted model directory."
        exit 1
    fi
fi

print_success "Model installed successfully!"
echo "Path: $OUTPUT_DIR/$MODEL_NAME"
