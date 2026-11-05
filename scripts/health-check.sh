#!/usr/bin/env bash
# Validate the maintained Python services and local model assets.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
STT_DIR="$PROJECT_ROOT/stt_service"
ORCHESTRATOR_DIR="$PROJECT_ROOT/Architect_MultiClient_Server/orchestrator_service"
TTS_DIR="$PROJECT_ROOT/Architect_MultiClient_Server/tts_service"
GIPFORMER_DIR="$PROJECT_ROOT/models/gipformer-model"
FAILED=0

pass() { printf 'PASS  %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; FAILED=$((FAILED + 1)); }

check_file() {
    [[ -f "$1" ]] && pass "$2" || fail "$2 ($1)"
}

check_dir() {
    [[ -d "$1" ]] && pass "$2" || fail "$2 ($1)"
}

check_dir "$STT_DIR" "STT service directory"
check_dir "$ORCHESTRATOR_DIR" "Orchestrator service directory"
check_dir "$TTS_DIR" "TTS service directory"
check_file "$STT_DIR/stt_service/assets/whisper_marker.wav" "Whisper marker asset"
check_file "$STT_DIR/requirements.txt" "STT requirements"
check_file "$ORCHESTRATOR_DIR/requirements-orchestrator.txt" "Orchestrator requirements"
check_file "$TTS_DIR/requirements-tts.txt" "TTS requirements"

for file in encoder.int8.onnx decoder.int8.onnx joiner.int8.onnx tokens.txt; do
    check_file "$GIPFORMER_DIR/$file" "Gipformer $file"
done

for service_dir in "$STT_DIR" "$ORCHESTRATOR_DIR" "$TTS_DIR"; do
    if [[ -f "$service_dir/.env" ]]; then
        pass "$(basename "$service_dir") .env"
    elif [[ -f "$service_dir/.env.example" ]]; then
        warn "$(basename "$service_dir") .env is missing; copy .env.example"
    else
        fail "$(basename "$service_dir") has no environment template"
    fi
done

if command -v systemctl >/dev/null 2>&1; then
    for service in mezon-stt-service mezon-orchestrator-service mezon-tts-service; do
        if systemctl is-active --quiet "$service"; then
            pass "$service is running"
        else
            warn "$service is not running"
        fi
    done
fi

if (( FAILED > 0 )); then
    echo "$FAILED required check(s) failed."
    exit 1
fi

echo "All required checks passed."
