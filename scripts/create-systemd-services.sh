#!/usr/bin/env bash
# Create systemd units for the maintained Python services.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
STT_DIR="$PROJECT_ROOT/stt_service"
ORCHESTRATOR_DIR="$PROJECT_ROOT/Architect_MultiClient_Server/orchestrator_service"
TTS_DIR="$PROJECT_ROOT/Architect_MultiClient_Server/tts_service"
SYSTEMD_DIR=/etc/systemd/system
SERVICE_USER="${SUDO_USER:-${USER:-$(whoami)}}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
ENABLE=false
START=false
DRY_RUN=false

usage() {
    echo "Usage: sudo ./scripts/create-systemd-services.sh [--user USER] [--group GROUP] [--enable] [--start] [--dry-run]"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user) SERVICE_USER="$2"; shift 2 ;;
        --group) SERVICE_GROUP="$2"; shift 2 ;;
        --enable) ENABLE=true; shift ;;
        --start) START=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

if [[ "$DRY_RUN" == false && "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run this script with sudo (or use --dry-run)." >&2
    exit 1
fi

write_unit() {
    local name="$1" description="$2" directory="$3" command="$4"
    local target="$SYSTEMD_DIR/$name.service"
    local content="[Unit]
Description=$description
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$directory
Environment=PYTHONUNBUFFERED=1
ExecStart=$command
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target"

    if [[ "$DRY_RUN" == true ]]; then
        printf '\n# %s\n%s\n' "$target" "$content"
    else
        printf '%s\n' "$content" > "$target"
        chmod 644 "$target"
    fi
}

write_unit mezon-stt-service \
    "Mezon Call Translation - STT Service" \
    "$STT_DIR" \
    "$STT_DIR/venv/bin/python -m uvicorn stt_service.main:app --host 0.0.0.0 --port 8001"
write_unit mezon-orchestrator-service \
    "Mezon Call Translation - Orchestrator Service" \
    "$PROJECT_ROOT/Architect_MultiClient_Server" \
    "$ORCHESTRATOR_DIR/venv/bin/python -m uvicorn orchestrator_service.main:app --host 0.0.0.0 --port 8002"
write_unit mezon-tts-service \
    "Mezon Call Translation - TTS Service" \
    "$PROJECT_ROOT/Architect_MultiClient_Server" \
    "$TTS_DIR/venv/bin/python -m uvicorn tts_service.main:app --host 0.0.0.0 --port 8008"

if [[ "$DRY_RUN" == false ]]; then
    systemctl daemon-reload
    services=(mezon-stt-service mezon-orchestrator-service mezon-tts-service)
    [[ "$ENABLE" == true ]] && systemctl enable "${services[@]}"
    [[ "$START" == true ]] && systemctl restart "${services[@]}"
fi
