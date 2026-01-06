#!/bin/bash
# Download Kokoro-82M TTS Model
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
OUTPUT_DIR="models/kokoro_models"
VOICES=""
ALL_VOICES=false
FORCE=false
LIST=false
INFO=false

# Functions
print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo -e "${CYAN}${BOLD}    Kokoro-82M TTS Model Downloader${NC}"
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
Download Kokoro-82M TTS Model

USAGE:
    ./scripts/download-kokoro-model.sh [options]

OPTIONS:
    -o, --output <path>     Output directory (default: models/kokoro_models)
    -v, --voices <list>     Comma-separated voice names (e.g., af_heart,am_adam)
    -a, --all-voices        Download all available voices
    -f, --force             Force re-download even if files exist
    -l, --list              List downloaded voices
    -i, --info              Show model information
    -h, --help              Show this help message

EXAMPLES:
    # Download model with default voices
    ./scripts/download-kokoro-model.sh

    # Download specific voices
    ./scripts/download-kokoro-model.sh -v "af_heart,af_bella,am_adam"

    # Download all available voices
    ./scripts/download-kokoro-model.sh --all-voices

    # Force re-download
    ./scripts/download-kokoro-model.sh --force

    # List downloaded voices
    ./scripts/download-kokoro-model.sh --list

AVAILABLE VOICES:
    American Female: af_heart, af_bella, af_sarah, af_nicole, af_sky
    American Male:   am_adam, am_michael, am_liam
    British Female:  bf_emma, bf_isabella
    British Male:    bm_george, bm_lewis

MORE INFO:
    https://huggingface.co/hexgrad/Kokoro-82M

EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -v|--voices)
            VOICES="$2"
            shift 2
            ;;
        -a|--all-voices)
            ALL_VOICES=true
            shift
            ;;
        -f|--force)
            FORCE=true
            shift
            ;;
        -l|--list)
            LIST=true
            shift
            ;;
        -i|--info)
            INFO=true
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
MODEL_DIR="$PROJECT_ROOT/$OUTPUT_DIR"

# Handle --list flag
if [ "$LIST" = true ]; then
    print_header
    VOICES_DIR="$MODEL_DIR/voices"
    
    echo -e "${BOLD}Downloaded Voices:${NC}"
    echo "============================================================"
    
    if [ ! -d "$VOICES_DIR" ]; then
        print_warning "No voices directory found"
        exit 0
    fi
    
    voice_count=0
    for voice_file in "$VOICES_DIR"/*.pt; do
        if [ -f "$voice_file" ]; then
            voice_name=$(basename "$voice_file" .pt)
            size_mb=$(du -m "$voice_file" | cut -f1)
            echo -e "  ${GREEN}✓${NC} $(printf "%-20s" "$voice_name") ($size_mb MB)"
            ((voice_count++))
        fi
    done
    
    if [ $voice_count -eq 0 ]; then
        print_warning "No voices downloaded yet"
    else
        echo ""
        echo "Total: $voice_count voices"
    fi
    
    exit 0
fi

# Handle --info flag
if [ "$INFO" = true ]; then
    print_header
    
    echo -e "${BOLD}Model Information:${NC}"
    echo "============================================================"
    echo "Model directory: $MODEL_DIR"
    echo "Repository: hexgrad/Kokoro-82M"
    echo ""
    
    MODEL_PATH="$MODEL_DIR/kokoro-v0_19.pth"
    CONFIG_PATH="$MODEL_DIR/config.json"
    VOICES_DIR="$MODEL_DIR/voices"
    
    if [ -f "$MODEL_PATH" ]; then
        size_mb=$(du -m "$MODEL_PATH" | cut -f1)
        echo -e "  ${GREEN}✓${NC} Model: kokoro-v0_19.pth ($size_mb MB)"
    else
        echo -e "  ${RED}✗${NC} Model: Not downloaded"
    fi
    
    if [ -f "$CONFIG_PATH" ]; then
        echo -e "  ${GREEN}✓${NC} Config: config.json"
    else
        echo -e "  ${RED}✗${NC} Config: Not downloaded"
    fi
    
    if [ -d "$VOICES_DIR" ]; then
        voice_count=$(find "$VOICES_DIR" -name "*.pt" 2>/dev/null | wc -l)
        echo -e "  ${GREEN}✓${NC} Voices: $voice_count downloaded"
    else
        echo -e "  ${RED}✗${NC} Voices: 0 downloaded"
    fi
    
    exit 0
fi

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
PYTHON_SCRIPT="$PROJECT_ROOT/scripts/download-kokoro-model.py"
ARGS=("$PYTHON_SCRIPT")

if [ -n "$OUTPUT_DIR" ]; then
    ARGS+=("--output" "$OUTPUT_DIR")
fi

if [ -n "$VOICES" ]; then
    ARGS+=("--voices" "$VOICES")
fi

if [ "$ALL_VOICES" = true ]; then
    ARGS+=("--all-voices")
fi

if [ "$FORCE" = true ]; then
    ARGS+=("--force")
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
