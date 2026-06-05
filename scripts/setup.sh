#!/bin/bash
# Setup script for Mezon Call Translation Services
# This script:
# - Downloads Vosk and Kokoro models
# - Backs up existing .env files
# - Creates .env files from .env.example
# - Sets up virtual environments for each service
# - Updates model paths in .env files

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
TTS_SERVICE_DIR="$ARCH_DIR/tts_service"

# Model directories
MODELS_DIR="$PROJECT_ROOT/models"
VOSK_MODEL_DIR="$MODELS_DIR/vosk-model"
KOKORO_MODEL_DIR="$MODELS_DIR/kokoro_models"

# Functions
print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo -e "${CYAN}${BOLD}    Mezon Call Translation - Setup Script${NC}"
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
    cat <<EOF
Mezon Call Translation - Setup Script

USAGE:
    ./scripts/setup.sh [options]

OPTIONS:
    --skip-models           Skip model downloads
    --skip-venv             Skip virtual environment setup
    --skip-env              Skip .env file creation
    --vosk-model <name>     Vosk model name (default: vosk-model-small-en-us-0.15)
    --kokoro-voices <list>  Comma-separated Kokoro voice names
    --all-kokoro-voices     Download all Kokoro voices
    --install-deps          Force installation of system dependencies (Python, FFmpeg, etc.)
    -h, --help              Show this help message

EXAMPLES:
    # Full setup with defaults
    ./scripts/setup.sh

    # Install system dependencies first
    ./scripts/setup.sh --install-deps

    # Skip model downloads (if already downloaded)
    ./scripts/setup.sh --skip-models

    # Use specific Vosk model
    ./scripts/setup.sh --vosk-model vosk-model-en-us-0.22

    # Download all Kokoro voices
    ./scripts/setup.sh --all-kokoro-voices

EOF
}

# Default options
SKIP_MODELS=false
SKIP_VENV=false
SKIP_ENV=false
INSTALL_DEPS=false
VOSK_MODEL="vosk-model-small-en-us-0.15"
KOKORO_VOICES=""
ALL_KOKORO_VOICES=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-models)
            SKIP_MODELS=true
            shift
            ;;
        --skip-venv)
            SKIP_VENV=true
            shift
            ;;
        --skip-env)
            SKIP_ENV=true
            shift
            ;;
        --install-deps)
            INSTALL_DEPS=true
            shift
            ;;
        --vosk-model)
            VOSK_MODEL="$2"
            shift 2
            ;;
        --kokoro-voices)
            KOKORO_VOICES="$2"
            shift 2
            ;;
        --all-kokoro-voices)
            ALL_KOKORO_VOICES=true
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

# Print header
print_header

# Check Python
print_info "Checking system dependencies..."

install_dependencies() {
    if [ -f /etc/debian_version ]; then
        # Check for sudo
        if ! command -v sudo &> /dev/null && [ "$EUID" -ne 0 ]; then
            print_error "Sudo not found and not running as root. Cannot install dependencies."
            exit 1
        fi

        SUDO=""
        if [ "$EUID" -ne 0 ]; then
            SUDO="sudo"
        fi

        print_info "Detected Debian/Ubuntu system. Updating package lists..."
        $SUDO apt-get update

        print_info "Installing software-properties-common..."
        $SUDO apt-get install -y software-properties-common

        print_info "Adding deadsnakes PPA for Python 3.12..."
        $SUDO add-apt-repository -y ppa:deadsnakes/ppa
        $SUDO apt-get update

        print_info "Installing system dependencies (Python 3.12, PortAudio, FFmpeg)..."
        $SUDO apt-get install -y \
            python3.12 \
            python3.12-venv \
            python3.12-dev \
            python3-pip \
            portaudio19-dev \
            python3-pyaudio \
            ffmpeg \
            gcc \
            curl \
            wget \
            rsyslog \
            git

        print_success "System dependencies installed!"
    else
        print_warning "Automatic dependency installation is only supported on Debian/Ubuntu."
        print_warning "Please ensure you have Python 3.12, PortAudio, and FFmpeg installed manually."
    fi
}

# Install dependencies if Python 3.12 is missing or requested
if [ "$INSTALL_DEPS" = true ]; then
    print_info "Forcing dependency installation..."
    install_dependencies
elif ! command -v python3.12 &> /dev/null; then
    print_warning "Python 3.12 not found. Attempting to install system dependencies..."
    install_dependencies
else
    print_info "Python 3.12 found. Skipping full system install (run with --install-deps to force)."
fi

PYTHON_CMD="python3.12"

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1)
print_success "Using Python: $PYTHON_VERSION"

# ============================================================================
# Step 1: Download Models
# ============================================================================
if [ "$SKIP_MODELS" = false ]; then
    print_section "Step 1: Downloading Models"
    
    # Download Vosk model
    print_info "Downloading Vosk STT model: $VOSK_MODEL"
    if [ -d "$VOSK_MODEL_DIR/$VOSK_MODEL" ]; then
        print_warning "Vosk model already exists. Skipping download."
    else
        bash "$SCRIPT_DIR/download-vosk-model.sh" --model "$VOSK_MODEL" --output "models/vosk-model"
    fi
    
    # Download Kokoro model
    print_info "Downloading Kokoro TTS model..."
    KOKORO_ARGS=("$SCRIPT_DIR/download-kokoro-model.sh" "--output" "models/kokoro_models")
    
    if [ "$ALL_KOKORO_VOICES" = true ]; then
        KOKORO_ARGS+=("--all-voices")
    elif [ -n "$KOKORO_VOICES" ]; then
        KOKORO_ARGS+=("--voices" "$KOKORO_VOICES")
    fi
    
    if [ -f "$KOKORO_MODEL_DIR/kokoro-v0_19.pth" ]; then
        print_warning "Kokoro model already exists. Checking for new voices..."
        KOKORO_ARGS+=("--force")
    fi
    
    bash "${KOKORO_ARGS[@]}"
    
    print_success "Models downloaded successfully!"
else
    print_section "Step 1: Skipping Model Downloads"
    print_warning "Model downloads skipped. Make sure models are already present."
fi

# ============================================================================
# Step 2: Backup and Create .env Files
# ============================================================================
if [ "$SKIP_ENV" = false ]; then
    print_section "Step 2: Setting up .env Files"
    
    # Function to backup and create .env
    setup_env_file() {
        local service_dir=$1
        local service_name=$2
        local env_file="$service_dir/.env"
        local env_example="$service_dir/.env.example"
        
        print_info "Setting up .env for $service_name..."
        
        # Check if .env.example exists
        if [ ! -f "$env_example" ]; then
            print_warning ".env.example not found for $service_name. Skipping."
            return
        fi
        
        # Backup existing .env
        if [ -f "$env_file" ]; then
            backup_file="$env_file.backup.$(date +%Y%m%d_%H%M%S)"
            cp "$env_file" "$backup_file"
            print_success "Backed up existing .env to: $(basename "$backup_file")"
        fi
        
        # Copy .env.example to .env
        cp "$env_example" "$env_file"
        print_success "Created .env from .env.example for $service_name"
    }
    
    # Setup .env for each service
    setup_env_file "$STT_SERVICE_DIR" "STT Service"
    setup_env_file "$ORCHESTRATOR_DIR" "Orchestrator Service"
    setup_env_file "$AGENTS_DIR" "Agents Service"
    setup_env_file "$TTS_SERVICE_DIR" "TTS Service"
    
    # Update model paths in .env files
    print_info "Updating model paths in .env files..."
    
    # Get absolute paths for models
    VOSK_MODEL_PATH="$VOSK_MODEL_DIR/$VOSK_MODEL"
    KOKORO_MODEL_PATH="$KOKORO_MODEL_DIR"
    
    # Update STT Service .env
    if [ -f "$STT_SERVICE_DIR/.env" ]; then
        # Update VOSK_MODEL_PATH
        if grep -q "^VOSK_MODEL_PATH=" "$STT_SERVICE_DIR/.env"; then
            sed -i.tmp "s|^VOSK_MODEL_PATH=.*|VOSK_MODEL_PATH=$VOSK_MODEL_PATH|" "$STT_SERVICE_DIR/.env"
            rm -f "$STT_SERVICE_DIR/.env.tmp"
        else
            echo "VOSK_MODEL_PATH=$VOSK_MODEL_PATH" >> "$STT_SERVICE_DIR/.env"
        fi
        print_success "Updated VOSK_MODEL_PATH in STT Service"
    fi
    
    # Update Agents .env
    if [ -f "$TTS_SERVICE_DIR/.env" ]; then
        # Update TTS_MODEL_PATH
        if grep -q "^TTS_MODEL_PATH=" "$TTS_SERVICE_DIR/.env"; then
            sed -i.tmp "s|^TTS_MODEL_PATH=.*|TTS_MODEL_PATH=$KOKORO_MODEL_PATH|" "$TTS_SERVICE_DIR/.env"
            rm -f "$TTS_SERVICE_DIR/.env.tmp"
        else
            echo "TTS_MODEL_PATH=$KOKORO_MODEL_PATH" >> "$TTS_SERVICE_DIR/.env"
        fi
        print_success "Updated TTS_MODEL_PATH in TTS Service"
    fi
    
    print_success ".env files configured successfully!"
else
    print_section "Step 2: Skipping .env Setup"
    print_warning ".env setup skipped."
fi

# ============================================================================
# Step 3: Setup Virtual Environments
# ============================================================================
if [ "$SKIP_VENV" = false ]; then
    print_section "Step 3: Setting up Virtual Environments"
    
    # Function to create venv and install dependencies
    setup_venv() {
        local service_dir=$1
        local service_name=$2
        local req_filename=${3:-requirements.txt}
        local venv_dir="$service_dir/venv"
        local requirements_file="$service_dir/$req_filename"
        
        print_info "Setting up virtual environment for $service_name..."
        
        # Check if requirements file exists
        if [ ! -f "$requirements_file" ]; then
            print_warning "$req_filename not found for $service_name. Skipping."
            return
        fi
        
        # Create virtual environment
        if [ -d "$venv_dir" ]; then
            print_warning "Virtual environment already exists for $service_name. Skipping creation."
        else
            $PYTHON_CMD -m venv "$venv_dir"
            print_success "Created virtual environment for $service_name"
        fi
        
        # Activate and install dependencies
        print_info "Installing dependencies for $service_name..."
        source "$venv_dir/bin/activate"
        
        # Upgrade pip
        pip install --upgrade pip > /dev/null 2>&1
        
        # Install requirements
        pip install -r "$requirements_file"
        
        deactivate
        print_success "Dependencies installed for $service_name"
    }
    
    # Setup venv for each service
    setup_venv "$STT_SERVICE_DIR" "STT Service" "requirements-server.txt"
    setup_venv "$ORCHESTRATOR_DIR" "Orchestrator Service" "requirements-orchestrator.txt"
    setup_venv "$AGENTS_DIR" "Agents Service" "requirements-agent.txt"
    setup_venv "$TTS_SERVICE_DIR" "TTS Service" "requirements-tts.txt"
    
    print_success "Virtual environments configured successfully!"
else
    print_section "Step 3: Skipping Virtual Environment Setup"
    print_warning "Virtual environment setup skipped."
fi

# ============================================================================
# Summary
# ============================================================================
print_section "Setup Complete!"

echo -e "${GREEN}${BOLD}Summary:${NC}"
echo ""
echo -e "  ${GREEN}✓${NC} Models downloaded to: $MODELS_DIR"
echo -e "  ${GREEN}✓${NC} Vosk model: $VOSK_MODEL"
echo -e "  ${GREEN}✓${NC} Kokoro model: kokoro_models"
echo ""
echo -e "  ${GREEN}✓${NC} .env files created and configured"
echo -e "  ${GREEN}✓${NC} Virtual environments set up for all services"
echo ""
echo -e "${CYAN}${BOLD}Next Steps:${NC}"
echo ""
echo -e "  1. Review and update .env files with your specific configuration:"
echo -e "     - $STT_SERVICE_DIR/.env"
echo -e "     - $ORCHESTRATOR_DIR/.env"
echo -e "     - $AGENTS_DIR/.env"
echo ""
echo -e "  2. Create systemd services (optional):"
echo -e "     ${CYAN}sudo ./scripts/create-systemd-services.sh${NC}"
echo ""
echo -e "  3. Start the services manually:"
echo -e "     ${CYAN}cd $STT_SERVICE_DIR && ./venv/bin/python -m uvicorn stt_service.main:app --host 0.0.0.0 --port 8000${NC}"
echo -e "     ${CYAN}cd $ORCHESTRATOR_DIR && ./venv/bin/python -m uvicorn orchestrator_service.main:app --host 0.0.0.0 --port 8001${NC}"
echo -e "     ${CYAN}cd $AGENTS_DIR && ./venv/bin/python src/main.py${NC}"
echo ""
print_success "Setup completed successfully! 🎉"
