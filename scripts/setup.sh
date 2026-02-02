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

# Service directories (adapted to current codebase)
SERVER_VOSK_DIR="$ARCH_DIR/server_vosk"
AGENTS_DIR="$ARCH_DIR/agents"

# Model directories (top-level models folder as described in docs)
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

    # Ensure base models directory exists
    mkdir -p "$MODELS_DIR"

    # Download Vosk model (STT) into models/vosk-model as in docs
    print_info "Downloading Vosk STT model: $VOSK_MODEL"
    if [ -d "$VOSK_MODEL_DIR/$VOSK_MODEL" ]; then
        print_warning "Vosk model already exists. Skipping download."
    else
        bash "$SCRIPT_DIR/download-vosk-model.sh" --model "$VOSK_MODEL" --output "models/vosk-model"
    fi

    # Optional: download Kokoro model (used by agents TTS)
    print_info "Downloading Kokoro TTS model (optional, used by agents)..."
    mkdir -p "$KOKORO_MODEL_DIR"
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
# Step 2: Backup and Create .env Files (root-level + per service)
# ============================================================================
if [ "$SKIP_ENV" = false ]; then
    print_section "Step 2: Setting up .env Files"

    ROOT_ENV_FILE="$PROJECT_ROOT/.env"
    ROOT_ENV_EXAMPLE="$PROJECT_ROOT/env.example"

    # 2.1 Root .env (for Docker / global config)
    if [ ! -f "$ROOT_ENV_EXAMPLE" ]; then
        print_warning "env.example not found at project root. Skipping root .env creation."
    else
        print_info "Setting up root .env from env.example..."

        # Backup existing .env
        if [ -f "$ROOT_ENV_FILE" ]; then
            backup_file="$ROOT_ENV_FILE.backup.$(date +%Y%m%d_%H%M%S)"
            cp "$ROOT_ENV_FILE" "$backup_file"
            print_success "Backed up existing .env to: $(basename "$backup_file")"
        fi

        # Copy env.example to .env
        cp "$ROOT_ENV_EXAMPLE" "$ROOT_ENV_FILE"
        print_success "Created .env from env.example at project root"
    fi

    # Update model paths in root .env
    if [ -f "$ROOT_ENV_FILE" ]; then
        print_info "Updating model paths in root .env..."

        VOSK_MODEL_PATH="$VOSK_MODEL_DIR/$VOSK_MODEL"
        KOKORO_MODEL_PATH="$KOKORO_MODEL_DIR"

        # Update or append VOSK_MODEL_PATH
        if grep -q "^VOSK_MODEL_PATH=" "$ROOT_ENV_FILE"; then
            sed -i.tmp "s|^VOSK_MODEL_PATH=.*|VOSK_MODEL_PATH=$VOSK_MODEL_PATH|" "$ROOT_ENV_FILE"
            rm -f "$ROOT_ENV_FILE.tmp"
        else
            echo "VOSK_MODEL_PATH=$VOSK_MODEL_PATH" >> "$ROOT_ENV_FILE"
        fi

        # Update or append TTS_MODEL_PATH (for agents / TTS usage)
        if grep -q "^TTS_MODEL_PATH=" "$ROOT_ENV_FILE"; then
            sed -i.tmp "s|^TTS_MODEL_PATH=.*|TTS_MODEL_PATH=$KOKORO_MODEL_PATH|" "$ROOT_ENV_FILE"
            rm -f "$ROOT_ENV_FILE.tmp"
        else
            echo "TTS_MODEL_PATH=$KOKORO_MODEL_PATH" >> "$ROOT_ENV_FILE"
        fi

        print_success "Updated model paths in root .env"
    fi

    # 2.2 Helper: setup .env for a specific service from its .env.example
    setup_env_file() {
        local service_dir=$1
        local service_name=$2
        local env_example_name=${3:-.env.example}

        local env_file="$service_dir/.env"
        local env_example="$service_dir/$env_example_name"

        print_info "Setting up .env for $service_name..."

        if [ ! -f "$env_example" ]; then
            print_warning "$env_example_name not found for $service_name at $service_dir. Skipping."
            return
        fi

        # Backup existing .env
        if [ -f "$env_file" ]; then
            local backup_file="$env_file.backup.$(date +%Y%m%d_%H%M%S)"
            cp "$env_file" "$backup_file"
            print_success "Backed up existing .env for $service_name to: $(basename "$backup_file")"
        fi

        cp "$env_example" "$env_file"
        print_success "Created .env from $env_example_name for $service_name"
    }

    # 2.3 Create .env for each service (server_vosk, agents)
    if [ -d "$SERVER_VOSK_DIR" ]; then
        setup_env_file "$SERVER_VOSK_DIR" "Server Vosk Service"

        # Update VOSK_MODEL_PATH in server_vosk .env if present
        if [ -f "$SERVER_VOSK_DIR/.env" ]; then
            VOSK_MODEL_PATH="$VOSK_MODEL_DIR/$VOSK_MODEL"
            if grep -q "^VOSK_MODEL_PATH=" "$SERVER_VOSK_DIR/.env"; then
                sed -i.tmp "s|^VOSK_MODEL_PATH=.*|VOSK_MODEL_PATH=$VOSK_MODEL_PATH|" "$SERVER_VOSK_DIR/.env"
                rm -f "$SERVER_VOSK_DIR/.env.tmp"
            else
                echo "VOSK_MODEL_PATH=$VOSK_MODEL_PATH" >> "$SERVER_VOSK_DIR/.env"
            fi
            print_success "Updated VOSK_MODEL_PATH in Server Vosk .env"
        fi
    fi

    if [ -d "$AGENTS_DIR" ]; then
        setup_env_file "$AGENTS_DIR" "Agents Service"

        # Update TTS_MODEL_PATH in agents .env if present
        if [ -f "$AGENTS_DIR/.env" ]; then
            KOKORO_MODEL_PATH="$KOKORO_MODEL_DIR"
            if grep -q "^TTS_MODEL_PATH=" "$AGENTS_DIR/.env"; then
                sed -i.tmp "s|^TTS_MODEL_PATH=.*|TTS_MODEL_PATH=$KOKORO_MODEL_PATH|" "$AGENTS_DIR/.env"
                rm -f "$AGENTS_DIR/.env.tmp"
            else
                echo "TTS_MODEL_PATH=$KOKORO_MODEL_PATH" >> "$AGENTS_DIR/.env"
            fi
            print_success "Updated TTS_MODEL_PATH in Agents .env"
        fi
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
        # POSIX-style activation (this script is intended for Linux/macOS)
        # shellcheck source=/dev/null
        source "$venv_dir/bin/activate"

        # Upgrade pip
        pip install --upgrade pip > /dev/null 2>&1

        # Install requirements
        pip install -r "$requirements_file"

        deactivate
        print_success "Dependencies installed for $service_name"
    }

    # Setup venv for each service that exists in Architect_MultiClient_Server
    if [ -d "$SERVER_VOSK_DIR" ]; then
        setup_venv "$SERVER_VOSK_DIR" "Server Vosk Service" "requirements-server.txt"
    else
        print_warning "Server Vosk directory not found at $SERVER_VOSK_DIR. Skipping."
    fi

    if [ -d "$AGENTS_DIR" ]; then
        setup_venv "$AGENTS_DIR" "Agents Service" "requirements-agent.txt"
    else
        print_warning "Agents directory not found at $AGENTS_DIR. Skipping."
    fi

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
echo -e "  ${GREEN}✓${NC} Kokoro model (if requested): kokoro_models"
echo ""
echo -e "  ${GREEN}✓${NC} Root .env created and configured (if env.example present)"
echo -e "  ${GREEN}✓${NC} Virtual environments set up for available services (server_vosk, agents)"
echo ""
echo -e "${CYAN}${BOLD}Next Steps:${NC}"
echo ""
echo -e "  1. Review and update root .env with your specific configuration:"
echo -e "     - $PROJECT_ROOT/.env"
echo ""
echo -e "  2. (Optional) Create systemd services:"
echo -e "     ${CYAN}sudo ./scripts/create-systemd-services.sh${NC}"
echo ""
echo -e "  3. Start the services manually (non-Docker):"
if [ -d "$SERVER_VOSK_DIR" ]; then
    echo -e "     ${CYAN}cd $SERVER_VOSK_DIR && ./venv/bin/python -m uvicorn server_vosk.main:app --host 0.0.0.0 --port 8000${NC}"
fi
if [ -d "$AGENTS_DIR" ]; then
    echo -e "     ${CYAN}cd $AGENTS_DIR && ./venv/bin/python main.py dev${NC}"
fi
echo ""
print_success "Setup completed successfully! 🎉"
