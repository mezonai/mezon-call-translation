#!/bin/bash
# Create systemd service files for Mezon Call Translation Services
# This script creates and installs systemd service files for:
# - STT Service
# - Orchestrator Service
# - Agents Service

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

# Systemd directory
SYSTEMD_DIR="/etc/systemd/system"

# Functions
print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}============================================================${NC}"
    echo -e "${CYAN}${BOLD}    Mezon Call Translation - Systemd Service Creator${NC}"
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
Mezon Call Translation - Systemd Service Creator

USAGE:
    sudo ./scripts/create-systemd-services.sh [options]

OPTIONS:
    --user <username>       User to run services as (default: current user)
    --group <groupname>     Group to run services as (default: current user's group)
    --enable                Enable services to start on boot
    --start                 Start services immediately after creation
    --dry-run               Show what would be created without actually creating
    --skip-validation       Skip validation of setup
    -h, --help              Show this help message

EXAMPLES:
    # Create services for current user
    sudo ./scripts/create-systemd-services.sh

    # Create and enable services
    sudo ./scripts/create-systemd-services.sh --enable

    # Create, enable, and start services
    sudo ./scripts/create-systemd-services.sh --enable --start

    # Dry run to see what would be created
    sudo ./scripts/create-systemd-services.sh --dry-run

NOTE:
    This script must be run with sudo/root privileges.
    Run setup.sh first to ensure all dependencies are installed.

EOF
}

# Check if running as root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "This script must be run as root (use sudo)"
        echo ""
        show_help
        exit 1
    fi
}

# Validate that setup has been run
validate_setup() {
    local errors=0
    
    print_section "Validating Setup"
    
    # Check service directories exist
    print_info "Checking service directories..."
    for dir in "$STT_SERVICE_DIR" "$ORCHESTRATOR_DIR" "$AGENTS_DIR"; do
        if [ ! -d "$dir" ]; then
            print_error "Service directory not found: $dir"
            errors=$((errors + 1))
        else
            print_success "Found: $(basename "$dir")"
        fi
    done
    
    # Check virtual environments exist
    print_info "Checking virtual environments..."
    for dir in "$STT_SERVICE_DIR" "$ORCHESTRATOR_DIR" "$AGENTS_DIR"; do
        if [ ! -d "$dir/venv" ]; then
            print_error "Virtual environment not found: $dir/venv"
            print_warning "Run setup.sh to create virtual environments"
            errors=$((errors + 1))
        else
            print_success "Found venv for: $(basename "$dir")"
        fi
    done
    
    # Check .env files exist
    print_info "Checking .env files..."
    for dir in "$STT_SERVICE_DIR" "$ORCHESTRATOR_DIR" "$AGENTS_DIR"; do
        if [ ! -f "$dir/.env" ]; then
            print_warning ".env file not found: $dir/.env"
            print_info "Services will use default configuration"
        else
            print_success "Found .env for: $(basename "$dir")"
        fi
    done
    
    # Check Python in venv
    print_info "Checking Python in virtual environments..."
    for dir in "$STT_SERVICE_DIR" "$ORCHESTRATOR_DIR" "$AGENTS_DIR"; do
        if [ ! -f "$dir/venv/bin/python" ]; then
            print_error "Python not found in venv: $dir/venv/bin/python"
            errors=$((errors + 1))
        fi
    done
    
    # Check models directory
    print_info "Checking models directory..."
    if [ ! -d "$PROJECT_ROOT/models" ]; then
        print_warning "Models directory not found: $PROJECT_ROOT/models"
        print_info "Services may fail to start without models"
    else
        print_success "Models directory found"
    fi
    
    if [ $errors -gt 0 ]; then
        echo ""
        print_error "Setup validation failed with $errors error(s)."
        print_error "Please run setup.sh first:"
        echo -e "  ${CYAN}./scripts/setup.sh${NC}"
        exit 1
    fi
    
    print_success "Setup validation passed!"
}

# Default options
SERVICE_USER="${SUDO_USER:-${USER:-$(whoami)}}"
SERVICE_GROUP=$(id -gn "$SERVICE_USER")
ENABLE_SERVICES=false
START_SERVICES=false
DRY_RUN=false
SKIP_VALIDATION=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --user)
            SERVICE_USER="$2"
            shift 2
            ;;
        --group)
            SERVICE_GROUP="$2"
            shift 2
            ;;
        --enable)
            ENABLE_SERVICES=true
            shift
            ;;
        --start)
            START_SERVICES=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --skip-validation)
            SKIP_VALIDATION=true
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

# Check root unless dry-run
if [ "$DRY_RUN" = false ]; then
    check_root
fi

# Print header
print_header

print_info "Service user: $SERVICE_USER"
print_info "Service group: $SERVICE_GROUP"
print_info "Project root: $PROJECT_ROOT"
echo ""

# Validate setup
if [ "$SKIP_VALIDATION" = false ]; then
    validate_setup
else
    print_warning "Skipping setup validation as requested"
fi

# ============================================================================
# Create Service Files
# ============================================================================

create_service_file() {
    local service_name=$1
    local service_description=$2
    local service_dir=$3
    local exec_start=$4
    
    local service_file="$SYSTEMD_DIR/mezon-${service_name}.service"
    
    print_info "Creating service file: mezon-${service_name}.service"
    
    local service_content="[Unit]
Description=$service_description
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$service_dir
Environment=\"PATH=$service_dir/venv/bin:/usr/local/bin:/usr/bin:/bin\"
Environment=\"PYTHONUNBUFFERED=1\"
ExecStart=$exec_start
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=mezon-${service_name}

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$service_dir
ReadWritePaths=$PROJECT_ROOT/models

# Resource limits
LimitNOFILE=65536
LimitNPROC=4096

[Install]
WantedBy=multi-user.target
"
    
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}[DRY RUN] Would create: $service_file${NC}"
        echo "$service_content"
        echo ""
    else
        echo "$service_content" > "$service_file"
        chmod 644 "$service_file"
        print_success "Created: $service_file"
    fi
}

print_section "Creating Systemd Service Files"

# STT Service
create_service_file \
    "stt-service" \
    "Mezon Call Translation - STT Service" \
    "$STT_SERVICE_DIR" \
    "$STT_SERVICE_DIR/venv/bin/python -m uvicorn stt_service.main:app --host 0.0.0.0 --port 8000"

# Orchestrator Service
create_service_file \
    "orchestrator-service" \
    "Mezon Call Translation - Orchestrator Service" \
    "$ORCHESTRATOR_DIR" \
    "$ORCHESTRATOR_DIR/venv/bin/python -m uvicorn orchestrator_service.main:app --host 0.0.0.0 --port 8001"

# Agents Service
create_service_file \
    "agents-service" \
    "Mezon Call Translation - Agents Service" \
    "$AGENTS_DIR" \
    "$AGENTS_DIR/venv/bin/python src/main.py"

# ============================================================================
# Reload systemd
# ============================================================================
if [ "$DRY_RUN" = false ]; then
    print_section "Reloading Systemd"
    print_info "Reloading systemd daemon..."
    systemctl daemon-reload
    print_success "Systemd daemon reloaded"
fi

# ============================================================================
# Enable Services (if requested)
# ============================================================================
if [ "$ENABLE_SERVICES" = true ] && [ "$DRY_RUN" = false ]; then
    print_section "Enabling Services"
    
    for service in mezon-stt-service mezon-orchestrator-service mezon-agents-service; do
        print_info "Enabling $service..."
        systemctl enable "$service"
        print_success "$service enabled"
    done
fi

# ============================================================================
# Start Services (if requested)
# ============================================================================
if [ "$START_SERVICES" = true ] && [ "$DRY_RUN" = false ]; then
    print_section "Starting Services"
    
    for service in mezon-stt-service mezon-orchestrator-service mezon-agents-service; do
        print_info "Starting $service..."
        systemctl start "$service"
        
        # Wait a moment for service to start
        sleep 2
        
        # Check if service started successfully
        if systemctl is-active --quiet "$service"; then
            print_success "$service started successfully"
        else
            print_error "$service failed to start"
            print_info "Check logs with: journalctl -u $service -n 50"
        fi
    done
    
    echo ""
    print_section "Service Status"
    for service in mezon-stt-service mezon-orchestrator-service mezon-agents-service; do
        systemctl status "$service" --no-pager -l || true
        echo ""
    done
fi

# ============================================================================
# Summary
# ============================================================================
print_section "Summary"

if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}${BOLD}DRY RUN MODE - No changes were made${NC}"
    echo ""
    echo -e "To actually create the services, run without --dry-run:"
    echo -e "${CYAN}sudo ./scripts/create-systemd-services.sh${NC}"
else
    echo -e "${GREEN}${BOLD}Systemd services created successfully!${NC}"
    echo ""
    echo -e "${CYAN}${BOLD}Created Services:${NC}"
    echo -e "  ${GREEN}✓${NC} mezon-stt-service (Port 8000)"
    echo -e "  ${GREEN}✓${NC} mezon-orchestrator-service (Port 8001)"
    echo -e "  ${GREEN}✓${NC} mezon-agents-service"
    echo ""
    
    if [ "$ENABLE_SERVICES" = true ]; then
        echo -e "${CYAN}${BOLD}Services Status:${NC}"
        echo -e "  ${GREEN}✓${NC} Services enabled to start on boot"
    fi
    
    if [ "$START_SERVICES" = true ]; then
        echo -e "  ${GREEN}✓${NC} Services started"
    fi
    
    echo ""
    echo -e "${CYAN}${BOLD}Useful Commands:${NC}"
    echo ""
    echo -e "  # Check service status"
    echo -e "  ${CYAN}sudo systemctl status mezon-stt-service${NC}"
    echo -e "  ${CYAN}sudo systemctl status mezon-orchestrator-service${NC}"
    echo -e "  ${CYAN}sudo systemctl status mezon-agents-service${NC}"
    echo ""
    echo -e "  # Start all services"
    echo -e "  ${CYAN}sudo systemctl start mezon-stt-service mezon-orchestrator-service mezon-agents-service${NC}"
    echo ""
    echo -e "  # Stop all services"
    echo -e "  ${CYAN}sudo systemctl stop mezon-stt-service mezon-orchestrator-service mezon-agents-service${NC}"
    echo ""
    echo -e "  # Restart all services"
    echo -e "  ${CYAN}sudo systemctl restart mezon-stt-service mezon-orchestrator-service mezon-agents-service${NC}"
    echo ""
    echo -e "  # Enable all services to start on boot"
    echo -e "  ${CYAN}sudo systemctl enable mezon-stt-service mezon-orchestrator-service mezon-agents-service${NC}"
    echo ""
    echo -e "  # Disable all services from starting on boot"
    echo -e "  ${CYAN}sudo systemctl disable mezon-stt-service mezon-orchestrator-service mezon-agents-service${NC}"
    echo ""
    echo -e "  # View live logs (all services)"
    echo -e "  ${CYAN}sudo journalctl -u mezon-stt-service -u mezon-orchestrator-service -u mezon-agents-service -f${NC}"
    echo ""
    echo -e "  # View logs for specific service"
    echo -e "  ${CYAN}sudo journalctl -u mezon-stt-service -f${NC}"
    echo -e "  ${CYAN}sudo journalctl -u mezon-orchestrator-service -f${NC}"
    echo -e "  ${CYAN}sudo journalctl -u mezon-agents-service -f${NC}"
    echo ""
    echo -e "  # View recent logs (last 50 lines)"
    echo -e "  ${CYAN}sudo journalctl -u mezon-stt-service -n 50${NC}"
    echo -e "  ${CYAN}sudo journalctl -u mezon-orchestrator-service -n 50${NC}"
    echo -e "  ${CYAN}sudo journalctl -u mezon-agents-service -n 50${NC}"
    echo ""
    echo -e "  # View logs since last boot"
    echo -e "  ${CYAN}sudo journalctl -u mezon-stt-service -b${NC}"
    echo ""
fi

print_success "Done! 🎉"