#!/bin/bash
# Health check script for Mezon Call Translation Services
# Checks if all components are properly set up and running

set -e

# Colors
CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ARCH_DIR="$PROJECT_ROOT/Architect_MultiClient_Server"

# Service directories
STT_SERVICE_DIR="$ARCH_DIR/stt_service"
ORCHESTRATOR_DIR="$ARCH_DIR/orchestrator_service"
AGENTS_DIR="$ARCH_DIR/agents"

# Model directories
MODELS_DIR="$PROJECT_ROOT/models"
NEMOTRON_MODEL_DIR="$MODELS_DIR/nemotron-model"
WHISPER_MODEL_DIR="$MODELS_DIR/whisper"
KOKORO_MODEL_DIR="$MODELS_DIR/kokoro_models"
GIPFORMER_REPOSITORY="g-group-ai-lab/gipformer-65M-rnnt"
GIPFORMER_FILES=("encoder.int8.onnx" "decoder.int8.onnx" "joiner.int8.onnx" "tokens.txt")

# Counters
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0

# Functions
print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo -e "${CYAN}${BOLD}    Mezon Call Translation - Health Check${NC}"
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo ""
}

print_section() {
    echo ""
    echo -e "${CYAN}${BOLD}------------------------------------------------------------${NC}"
    echo -e "${CYAN}${BOLD}  $1${NC}"
    echo -e "${CYAN}${BOLD}------------------------------------------------------------${NC}"
    echo ""
}

check_pass() {
    echo -e "${GREEN}✅ $1${NC}"
    ((PASSED_CHECKS+=1))
    ((TOTAL_CHECKS+=1))
}

check_fail() {
    echo -e "${RED}❌ $1${NC}"
    ((FAILED_CHECKS+=1))
    ((TOTAL_CHECKS+=1))
}

check_warn() {
    echo -e "${YELLOW}⚠️  $1${NC}"
    ((WARNING_CHECKS+=1))
    ((TOTAL_CHECKS+=1))
}

print_info() {
    echo -e "${CYAN}   $1${NC}"
}

# Print header
print_header

# ============================================================================
# Check Python Installation
# ============================================================================
print_section "Python Installation"

if command -v python3 &> /dev/null && PYTHON_VERSION=$(python3 --version 2>&1); then
    check_pass "Python3 installed: $PYTHON_VERSION"
elif command -v python &> /dev/null && PYTHON_VERSION=$(python --version 2>&1); then
    check_warn "Python installed: $PYTHON_VERSION (python3 recommended)"
else
    check_fail "Python not found"
fi

# ============================================================================
# Check Models
# ============================================================================
print_section "Models"

# Check Nemotron model
if [ -d "$NEMOTRON_MODEL_DIR" ]; then
    nemotron_config=$(find "$NEMOTRON_MODEL_DIR" -maxdepth 2 -type f -name "genai_config.json" | head -n 1)
    if [ -n "$nemotron_config" ]; then
        check_pass "Nemotron model found: $(dirname "$nemotron_config")"
    else
        check_fail "No Nemotron genai_config.json found in $NEMOTRON_MODEL_DIR"
        print_info "  Run: bash scripts/download-nemotron-model.sh"
    fi
else
    check_fail "Nemotron model directory not found: $NEMOTRON_MODEL_DIR"
    print_info "  Run: bash scripts/download-nemotron-model.sh"
fi

# faster-whisper resolves and downloads/caches the model on STT startup.
print_info "Non-realtime Whisper model is managed by faster-whisper cache"

if [ -f "$STT_SERVICE_DIR/assets/whisper_marker.wav" ]; then
    check_pass "Non-realtime Whisper marker asset found"
else
    check_fail "Non-realtime Whisper marker asset is missing"
fi

# Gipformer shares Hugging Face cache management with faster-whisper rather
# than using a project-local models/ directory. Check the exact files used by
# the fallback service at STT startup, without allowing a download.
if command -v hf &> /dev/null; then
    HF_DOWNLOAD_COMMAND=(hf download)
elif command -v huggingface-cli &> /dev/null; then
    HF_DOWNLOAD_COMMAND=(huggingface-cli download)
else
    HF_DOWNLOAD_COMMAND=()
fi

if [ "${#HF_DOWNLOAD_COMMAND[@]}" -eq 0 ]; then
    check_warn "Hugging Face CLI not found; cannot verify Gipformer cache"
    print_info "  Install huggingface-hub, then run: ./scripts/download-gipformer-model.sh"
elif "${HF_DOWNLOAD_COMMAND[@]}" "$GIPFORMER_REPOSITORY" "${GIPFORMER_FILES[@]}" --local-files-only >/dev/null 2>&1; then
    check_pass "Gipformer fallback model is present in Hugging Face cache"
else
    check_fail "Gipformer fallback model is missing or incomplete in Hugging Face cache"
    print_info "  Run: ./scripts/download-gipformer-model.sh"
fi

# Check Kokoro model
if [ -f "$KOKORO_MODEL_DIR/kokoro.onnx" ] || [ -f "$KOKORO_MODEL_DIR/kokoro-v0_19.pth" ]; then
    check_pass "Kokoro model found: kokoro-v0_19.pth"

    # Check voices
    if [ -d "$KOKORO_MODEL_DIR/voices" ]; then
        voice_count=$(find "$KOKORO_MODEL_DIR/voices" -name "*.pt" 2>/dev/null | wc -l)
        if [ "$voice_count" -gt 0 ]; then
            check_pass "Kokoro voices found: $voice_count voice(s)"
        else
            check_warn "No Kokoro voices downloaded"
            print_info "  Run: ./scripts/download-kokoro-model.sh"
        fi
    else
        check_warn "Kokoro voices directory not found"
    fi
else
    check_fail "Kokoro model not found: $KOKORO_MODEL_DIR/kokoro-v0_19.pth"
    print_info "  Run: ./scripts/download-kokoro-model.sh"
fi

# ============================================================================
# Check Service Directories
# ============================================================================
print_section "Service Directories"

check_service_dir() {
    local service_name=$1
    local service_dir=$2
    
    if [ -d "$service_dir" ]; then
        check_pass "$service_name directory exists"
    else
        check_fail "$service_name directory not found: $service_dir"
    fi
}

check_service_dir "STT Service" "$STT_SERVICE_DIR"
check_service_dir "Orchestrator Service" "$ORCHESTRATOR_DIR"
check_service_dir "Agents Service" "$AGENTS_DIR"

# ============================================================================
# Check Virtual Environments
# ============================================================================
print_section "Virtual Environments"

check_venv() {
    local service_name=$1
    local venv_dir=$2
    
    if [ -d "$venv_dir" ]; then
        if [ -f "$venv_dir/bin/python" ] || [ -f "$venv_dir/Scripts/python.exe" ]; then
            check_pass "$service_name venv exists"
        else
            check_fail "$service_name venv corrupted (no python executable)"
        fi
    else
        check_fail "$service_name venv not found"
        print_info "  Run: ./scripts/setup.sh"
    fi
}

check_venv "STT Service" "$STT_SERVICE_DIR/venv"
check_venv "Orchestrator Service" "$ORCHESTRATOR_DIR/venv"
check_venv "Agents Service" "$AGENTS_DIR/venv"

# Gipformer runs in the STT virtual environment. Keep this separate from the
# cache check so a missing Python dependency is distinguishable from a missing
# Hugging Face artifact.
if [ -x "$STT_SERVICE_DIR/venv/bin/python" ]; then
    if "$STT_SERVICE_DIR/venv/bin/python" -c "import sherpa_onnx" >/dev/null 2>&1; then
        check_pass "STT venv has sherpa-onnx for Gipformer fallback"
    else
        check_fail "STT venv is missing sherpa-onnx for Gipformer fallback"
        print_info "  Install STT requirements: $STT_SERVICE_DIR/venv/bin/pip install -r $STT_SERVICE_DIR/requirements-server.txt"
    fi
else
    check_warn "Cannot verify sherpa-onnx because STT Linux venv is unavailable"
fi

# ============================================================================
# Check .env Files
# ============================================================================
print_section "Environment Configuration"

check_env_file() {
    local service_name=$1
    local env_file=$2
    local env_example=$3
    
    if [ -f "$env_file" ]; then
        check_pass "$service_name .env exists"
    else
        if [ -f "$env_example" ]; then
            check_warn "$service_name .env not found (but .env.example exists)"
            print_info "  Run: cp $env_example $env_file"
        else
            check_fail "$service_name .env and .env.example not found"
        fi
    fi
}

check_env_file "STT Service" "$STT_SERVICE_DIR/.env" "$STT_SERVICE_DIR/.env.example"
check_env_file "Orchestrator Service" "$ORCHESTRATOR_DIR/.env" "$ORCHESTRATOR_DIR/.env.example"
check_env_file "Agents Service" "$AGENTS_DIR/.env" "$AGENTS_DIR/.env.example"

# ============================================================================
# Check Requirements Files
# ============================================================================
print_section "Requirements Files"

check_requirements() {
    local service_name=$1
    local req_file=$2
    
    if [ -f "$req_file" ]; then
        check_pass "$service_name requirements.txt exists"
    else
        check_fail "$service_name requirements.txt not found"
    fi
}

check_requirements "STT Service" "$STT_SERVICE_DIR/requirements-server.txt"
check_requirements "Orchestrator Service" "$ORCHESTRATOR_DIR/requirements-orchestrator.txt"
check_requirements "Agents Service" "$AGENTS_DIR/requirements-agent.txt"

# ============================================================================
# Check Systemd Services (if running on Linux)
# ============================================================================
if command -v systemctl &> /dev/null; then
    print_section "Systemd Services"
    
    check_systemd_service() {
        local service_name=$1
        
        if systemctl list-unit-files | grep -q "$service_name"; then
            if systemctl is-active --quiet "$service_name"; then
                check_pass "$service_name is installed and running"
            elif systemctl is-enabled --quiet "$service_name"; then
                check_warn "$service_name is installed and enabled but not running"
                print_info "  Start with: sudo systemctl start $service_name"
            else
                check_warn "$service_name is installed but not enabled"
                print_info "  Enable with: sudo systemctl enable $service_name"
            fi
        else
            check_warn "$service_name not installed"
            print_info "  Install with: sudo ./scripts/create-systemd-services.sh"
        fi
    }
    
    check_systemd_service "mezon-stt-service"
    check_systemd_service "mezon-orchestrator-service"
    check_systemd_service "mezon-agents-service"
fi

# ============================================================================
# Check Network Ports
# ============================================================================
print_section "Network Ports"

check_port() {
    local port=$1
    local service_name=$2
    
    if command -v netstat &> /dev/null; then
        if netstat -tuln 2>/dev/null | grep -q ":$port "; then
            check_pass "Port $port is in use (likely $service_name)"
        else
            check_warn "Port $port is not in use ($service_name not running?)"
        fi
    elif command -v ss &> /dev/null; then
        if ss -tuln 2>/dev/null | grep -q ":$port "; then
            check_pass "Port $port is in use (likely $service_name)"
        else
            check_warn "Port $port is not in use ($service_name not running?)"
        fi
    else
        check_warn "Cannot check port $port (netstat/ss not available)"
    fi
}

check_port "8000" "STT Service"
check_port "8001" "Orchestrator Service"
check_port "8002" "Agents Service"

# ============================================================================
# Summary
# ============================================================================
print_section "Summary"

echo -e "${BOLD}Health Check Results:${NC}"
echo ""
echo -e "  ${GREEN}✅ Passed:  $PASSED_CHECKS${NC}"
echo -e "  ${YELLOW}⚠️  Warnings: $WARNING_CHECKS${NC}"
echo -e "  ${RED}❌ Failed:  $FAILED_CHECKS${NC}"
echo -e "  ${CYAN}━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${BOLD}Total:    $TOTAL_CHECKS${NC}"
echo ""

if [ "$FAILED_CHECKS" -eq 0 ] && [ "$WARNING_CHECKS" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}🎉 All checks passed! System is ready.${NC}"
    exit 0
elif [ "$FAILED_CHECKS" -eq 0 ]; then
    echo -e "${YELLOW}${BOLD}⚠️  System is mostly ready, but has some warnings.${NC}"
    exit 0
else
    echo -e "${RED}${BOLD}❌ System has critical issues that need to be fixed.${NC}"
    echo ""
    echo -e "${CYAN}${BOLD}Recommended Actions:${NC}"
    echo ""
    echo -e "  1. Run setup script to fix most issues:"
    echo -e "     ${CYAN}./scripts/setup.sh${NC}"
    echo ""
    echo -e "  2. Review the failed checks above and fix them manually"
    echo ""
    exit 1
fi
