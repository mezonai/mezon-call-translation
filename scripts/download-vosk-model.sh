#!/bin/bash
# Download Vosk STT Model
# Shell script for Linux/macOS

set -e

# Colors
CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Default values
MODEL="vosk-model-small-en-us-0.15"
OUTPUT_DIR="models/vosk-model"
FORCE=false
LIST=false

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
    -l, --list              List available models
    -h, --help              Show this help message

EXAMPLES:
    # Download default small model
    ./scripts/download-vosk-model.sh

    # Download large model
    ./scripts/download-vosk-model.sh -m vosk-model-en-us-0.22

    # List available models
    ./scripts/download-vosk-model.sh --list

    # Force re-download
    ./scripts/download-vosk-model.sh --force

EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -m|--model)
            MODEL="$2"
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

# Get project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Print header
print_header

# Check Python
print_info "Checking Python installation..."
if ! command -v python3 &> /dev/null; then
    if ! command -v python &> /dev/null; then
        print_error "Python not found. Please install Python 3.8 or higher."
        exit 1
    fi
    PYTHON_CMD="python"
else
    PYTHON_CMD="python3"
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1)
print_success "Python: $PYTHON_VERSION"

echo ""

# Build command arguments
PYTHON_SCRIPT="$PROJECT_ROOT/scripts/download-vosk-model.py"
ARGS=("$PYTHON_SCRIPT")

if [ -n "$MODEL" ]; then
    ARGS+=("--model" "$MODEL")
fi

if [ -n "$OUTPUT_DIR" ]; then
    ARGS+=("--output" "$OUTPUT_DIR")
fi

if [ "$FORCE" = true ]; then
    ARGS+=("--force")
fi

if [ "$LIST" = true ]; then
    ARGS+=("--list")
fi

# Run Python script
print_info "Running download script..."
echo ""

if $PYTHON_CMD "${ARGS[@]}"; then
    echo ""
    print_success "Script completed successfully!"
    exit 0
else
    EXIT_CODE=$?
    echo ""
    print_error "Script failed with exit code: $EXIT_CODE"
    exit $EXIT_CODE
fi