#!/bin/bash
# ==============================================================================
# Download Vietnamese Bi-Encoder ONNX Model for Hallucination Filtering
# Repository: HgTuyen25/vietnamese-bi-encoder-onnx
# Model: vietnamese_bi_encoder_int8_accurate.onnx
# ==============================================================================

set -e

# ========================
# Colors & Formatting
# ========================
CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
BOLD='\033[1m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo -e "${CYAN}${BOLD}    Vietnamese Bi-Encoder ONNX Downloader (Hallucination)   ${NC}"
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo ""
}

print_info()    { echo -e "${CYAN}ℹ️  $1${NC}"; }
print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error()   { echo -e "${RED}❌ $1${NC}"; }

# ========================
# Configurations
# ========================
REPO_ID="HgTuyen25/vietnamese-bi-encoder-onnx"
OUTPUT_DIR="models/bi-encoder-model"
FORCE=false
LIST=false

# Files required for full offline inference (model + tokenizer)
MODEL_FILES=(
    "vietnamese_bi_encoder_int8_accurate.onnx"
    "tokenizer_config.json"
    "vocab.txt"
    "bpe.codes"
    "added_tokens.json"
)

# ========================
# Help & Argument Parsing
# ========================
show_help() {
    cat << EOF
Download the Vietnamese Bi-Encoder ONNX model and tokenizer for Whisper hallucination filtering.

USAGE:
    ./scripts/download-bi-encoder-model.sh [options]

OPTIONS:
    -o, --output <path>  Target output directory (default: models/bi-encoder-model)
    -f, --force          Force fresh download even if files already exist
    -l, --list           List files to be downloaded from the repository
    -h, --help           Show this help message

EXAMPLES:
    ./scripts/download-bi-encoder-model.sh
    ./scripts/download-bi-encoder-model.sh -o models/bi-encoder-model --force
EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output)
            if [ -z "${2:-}" ]; then
                print_error "$1 requires a directory path."
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
    echo "Hugging Face Repository: $REPO_ID"
    echo "Files to download:"
    for f in "${MODEL_FILES[@]}"; do
        echo "  - $f"
    done
    exit 0
fi

print_header

# ========================
# Resolve Paths
# ========================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ "$OUTPUT_DIR" != /* ]]; then
    TARGET_DIR="$PROJECT_ROOT/$OUTPUT_DIR"
else
    TARGET_DIR="$OUTPUT_DIR"
fi

mkdir -p "$TARGET_DIR"

print_info "Repository  : $REPO_ID"
print_info "Destination : $TARGET_DIR"
echo ""

# ========================
# Determine Download Tool
# ========================
HF_CMD=""
if command -v hf >/dev/null 2>&1; then
    HF_CMD="hf download"
elif command -v huggingface-cli >/dev/null 2>&1; then
    HF_CMD="huggingface-cli download"
elif python3 -m huggingface_hub.commands.huggingface_cli --help >/dev/null 2>&1; then
    HF_CMD="python3 -m huggingface_hub.commands.huggingface_cli download"
elif python -m huggingface_hub.commands.huggingface_cli --help >/dev/null 2>&1; then
    HF_CMD="python -m huggingface_hub.commands.huggingface_cli download"
fi

# Download helper using curl / wget as fallback
download_http() {
    local filename="$1"
    local dest="$TARGET_DIR/$filename"
    local url="https://huggingface.co/$REPO_ID/resolve/main/$filename"

    if [ -f "$dest" ] && [ "$FORCE" = false ]; then
        print_success "Exists: $filename"
        return 0
    fi

    print_info "Downloading $filename..."
    if command -v curl >/dev/null 2>&1; then
        curl -f -L -# "$url" -o "$dest"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --show-progress "$url" -O "$dest"
    else
        print_error "Neither curl, wget, nor huggingface-cli found."
        exit 1
    fi

    if [ -f "$dest" ]; then
        print_success "Done: $filename"
    else
        print_error "Failed to download $filename"
        exit 1
    fi
}

# ========================
# Execute Download
# ========================
if [ -n "$HF_CMD" ]; then
    print_info "Using Hugging Face CLI ($HF_CMD)..."
    for f in "${MODEL_FILES[@]}"; do
        dest="$TARGET_DIR/$f"
        if [ -f "$dest" ] && [ "$FORCE" = false ]; then
            print_success "Exists: $f"
            continue
        fi

        DOWNLOAD_ARGS=("$REPO_ID" "$f" --local-dir "$TARGET_DIR")
        if [ "$FORCE" = true ]; then
            DOWNLOAD_ARGS+=(--force-download)
        fi

        if ! $HF_CMD "${DOWNLOAD_ARGS[@]}"; then
            print_warning "Hugging Face CLI failed for $f. Retrying with direct HTTP download..."
            download_http "$f"
        else
            print_success "Done: $f"
        fi
    done
else
    print_info "Hugging Face CLI not found. Falling back to direct HTTP download (curl/wget)..."
    for f in "${MODEL_FILES[@]}"; do
        download_http "$f"
    done
fi

# ========================
# Verify Downloaded Files
# ========================
echo ""
missing=false
for f in "${MODEL_FILES[@]}"; do
    if [ ! -f "$TARGET_DIR/$f" ]; then
        print_error "Missing required file: $f"
        missing=true
    fi
done

if [ "$missing" = true ]; then
    print_error "Download completed but required files are missing."
    exit 1
fi

echo ""
print_success "All Bi-Encoder ONNX model files are ready!"
echo "Model path: $TARGET_DIR/vietnamese_bi_encoder_int8_accurate.onnx"
echo ""
