#!/bin/bash
# Kokoro-82M downloader (PURE BASH - no python required)

set -e

# ========================
# Config
# ========================
OUTPUT_DIR="models/kokoro_models"
FORCE=false

BASE_URL="https://huggingface.co/hexgrad/Kokoro-82M/resolve/main"

DEFAULT_VOICES=(
  af_heart
  af_bella
  af_sarah
  am_adam
  am_michael
)

ALL_VOICES=(
  af_heart af_bella af_sarah af_nicole af_sky
  am_adam am_michael am_liam
  bf_emma bf_isabella
  bm_george bm_lewis
)

# ========================
# Colors
# ========================
GREEN='\033[92m'
CYAN='\033[96m'
RED='\033[91m'
NC='\033[0m'

info()    { echo -e "${CYAN}ℹ️  $1${NC}"; }
success() { echo -e "${GREEN}✅ $1${NC}"; }
error()   { echo -e "${RED}❌ $1${NC}"; }

# ========================
# Args
# ========================
VOICES=("${DEFAULT_VOICES[@]}")

while [[ $# -gt 0 ]]; do
  case $1 in
    -o|--output)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    -v|--voices)
      IFS=',' read -ra VOICES <<< "$2"
      shift 2
      ;;
    -a|--all-voices)
      VOICES=("${ALL_VOICES[@]}")
      shift
      ;;
    -f|--force)
      FORCE=true
      shift
      ;;
    *)
      shift
      ;;
  esac
done

# ========================
# Setup
# ========================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MODEL_DIR="$PROJECT_ROOT/$OUTPUT_DIR"
VOICES_DIR="$MODEL_DIR/voices"

mkdir -p "$VOICES_DIR"

download() {
  url=$1
  out=$2

  if [ -f "$out" ] && [ "$FORCE" = false ]; then
    success "Exists: $(basename "$out")"
    return
  fi

  info "Downloading $(basename "$out")"
  wget -q --show-progress "$url" -O "$out"
  success "Done: $(basename "$out")"
}

echo ""
info "Downloading Kokoro-82M model..."
echo ""

# ========================
# Download core files
# ========================
download "$BASE_URL/kokoro-v1_0.pth" "$MODEL_DIR/kokoro-v1_0.pth"
download "$BASE_URL/config.json" "$MODEL_DIR/config.json"

echo ""
info "Downloading voices (${#VOICES[@]})..."
echo ""

# ========================
# Download voices
# ========================
for voice in "${VOICES[@]}"; do
  download "$BASE_URL/voices/${voice}.pt" "$VOICES_DIR/${voice}.pt"
done

echo ""
success "All downloads completed!"
echo "Model path: $MODEL_DIR"
echo ""
