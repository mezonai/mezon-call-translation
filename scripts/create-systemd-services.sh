#!/bin/bash
# Create systemd service files for Mezon Call Translation Services

set -e

# ============================================================================
# Configuration
# ============================================================================

# Colors
CYAN='\033[96m' GREEN='\033[92m' YELLOW='\033[93m' 
RED='\033[91m' BOLD='\033[1m' NC='\033[0m'

# Directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ARCH_DIR="$PROJECT_ROOT/Architect_MultiClient_Server"
SERVER_VOSK_DIR="$ARCH_DIR/server_vosk"
AGENTS_DIR="$ARCH_DIR/agents"
SYSTEMD_DIR="/etc/systemd/system"

# Default options
SERVICE_USER="${SUDO_USER:-${USER:-$(whoami)}}"
SERVICE_GROUP=$(id -gn "$SERVICE_USER")
ENABLE_SERVICES=false
START_SERVICES=false
DRY_RUN=false
SKIP_VALIDATION=false

# ============================================================================
# Helper Functions
# ============================================================================

print_header() {
    echo -e "\n${CYAN}${BOLD}============================================================"
    echo -e "    Mezon Call Translation - Systemd Service Creator"
    echo -e "============================================================${NC}\n"
}

print_section() { echo -e "\n${CYAN}${BOLD}------------------------------------------------------------\n  $1\n------------------------------------------------------------${NC}\n"; }
print_info() { echo -e "${CYAN}ℹ️  $1${NC}"; }
print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error() { echo -e "${RED}❌ $1${NC}"; }

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
    sudo ./scripts/create-systemd-services.sh --enable --start
    sudo ./scripts/create-systemd-services.sh --dry-run

NOTE: This script must be run with sudo. Run setup.sh first.
EOF
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "This script must be run as root (use sudo)"
        echo && show_help && exit 1
    fi
}

validate_setup() {
    local errors=0
    print_section "Validating Setup"
    
    # Check directories and virtual environments
    for dir in "$SERVER_VOSK_DIR" "$AGENTS_DIR"; do
        [ ! -d "$dir" ] && print_error "Directory not found: $dir" && ((errors++)) && continue
        print_success "Found: $(basename "$dir")"
        
        [ ! -d "$dir/venv" ] && print_error "Virtual environment not found: $dir/venv" && ((errors++)) && continue
        print_success "Found venv for: $(basename "$dir")"
        
        [ ! -f "$dir/venv/bin/python" ] && print_error "Python not found in venv: $dir" && ((errors++))
        [ ! -f "$dir/.env" ] && print_warning ".env file not found: $dir/.env"
    done
    
    # Check models directory
    [ ! -d "$PROJECT_ROOT/models" ] && print_warning "Models directory not found" || print_success "Models directory found"
    
    if [ $errors -gt 0 ]; then
        echo && print_error "Setup validation failed with $errors error(s)."
        print_error "Please run setup.sh first: ${CYAN}./scripts/setup.sh${NC}" && exit 1
    fi
    
    print_success "Setup validation passed!"
}

# ============================================================================
# Service Creation Functions
# ============================================================================

create_agents_service() {
    local service_file="$SYSTEMD_DIR/mezon-agents-service.service"
    print_info "Creating service file: mezon-agents-service.service"
    
    local content="[Unit]
Description=LiveKit Agents Service
After=network.target

[Service]
Type=simple
User=nccsoft
Group=nccsoft
WorkingDirectory=$PROJECT_ROOT
Environment=\"PATH=$PROJECT_ROOT/venv/bin\"
Environment=\"PYTHONPATH=$ARCH_DIR\"
ExecStart=$PROJECT_ROOT/venv/bin/python $ARCH_DIR/agents/main.py start
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"
    
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}[DRY RUN] Would create: $service_file${NC}\n$content\n"
    else
        echo "$content" > "$service_file" && chmod 644 "$service_file"
        print_success "Created: $service_file"
    fi
}

create_vosk_service() {
    local service_file="$SYSTEMD_DIR/mezon-server-vosk-service.service"
    print_info "Creating service file: mezon-server-vosk-service.service"
    
    local content="[Unit]
Description=Mezon Call Translation - Server Vosk STT Service
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$ARCH_DIR
Environment=\"PATH=$SERVER_VOSK_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin\"
Environment=\"PYTHONUNBUFFERED=1\"
ExecStart=$SERVER_VOSK_DIR/venv/bin/python -m uvicorn server_vosk.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=mezon-server-vosk-service

# Security settings
ProtectSystem=strict
ProtectHome=false
ReadWritePaths=$PROJECT_ROOT

# Resource limits
LimitNOFILE=65536
LimitNPROC=4096

[Install]
WantedBy=multi-user.target
"
    
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}[DRY RUN] Would create: $service_file${NC}\n$content\n"
    else
        echo "$content" > "$service_file" && chmod 644 "$service_file"
        print_success "Created: $service_file"
    fi
}

manage_services() {
    local action=$1
    local services=("mezon-server-vosk-service" "mezon-agents-service")
    
    for service in "${services[@]}"; do
        print_info "${action^}ing $service..."
        systemctl "$action" "$service"
        
        if [ "$action" = "start" ]; then
            sleep 2
            systemctl is-active --quiet "$service" && 
                print_success "$service started successfully" || 
                print_error "$service failed to start. Check: journalctl -u $service -n 50"
        else
            print_success "$service ${action}d"
        fi
    done
}

show_summary() {
    print_section "Summary"
    
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}${BOLD}DRY RUN MODE - No changes were made${NC}\n"
        echo -e "To create the services: ${CYAN}sudo ./scripts/create-systemd-services.sh${NC}"
        return
    fi
    
    echo -e "${GREEN}${BOLD}Systemd services created successfully!${NC}\n"
    echo -e "${CYAN}${BOLD}Created Services:${NC}"
    echo -e "  ${GREEN}✓${NC} mezon-server-vosk-service (Port 8000)"
    echo -e "  ${GREEN}✓${NC} mezon-agents-service\n"
    
    [ "$ENABLE_SERVICES" = true ] && echo -e "  ${GREEN}✓${NC} Services enabled to start on boot"
    [ "$START_SERVICES" = true ] && echo -e "  ${GREEN}✓${NC} Services started"
    
    cat <<EOF

${CYAN}${BOLD}Useful Commands:${NC}

  # Service management
  ${CYAN}sudo systemctl status mezon-server-vosk-service${NC}
  ${CYAN}sudo systemctl start mezon-server-vosk-service mezon-agents-service${NC}
  ${CYAN}sudo systemctl stop mezon-server-vosk-service mezon-agents-service${NC}
  ${CYAN}sudo systemctl restart mezon-server-vosk-service mezon-agents-service${NC}

  # Auto-start management
  ${CYAN}sudo systemctl enable mezon-server-vosk-service mezon-agents-service${NC}
  ${CYAN}sudo systemctl disable mezon-server-vosk-service mezon-agents-service${NC}

  # View logs
  ${CYAN}sudo journalctl -u mezon-server-vosk-service -u mezon-agents-service -f${NC}
  ${CYAN}sudo journalctl -u mezon-server-vosk-service -n 50${NC}
  ${CYAN}sudo journalctl -u mezon-agents-service -b${NC}

EOF
    print_success "Done! 🎉"
}

# ============================================================================
# Main Execution
# ============================================================================

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --user) SERVICE_USER="$2"; shift 2 ;;
        --group) SERVICE_GROUP="$2"; shift 2 ;;
        --enable) ENABLE_SERVICES=true; shift ;;
        --start) START_SERVICES=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --skip-validation) SKIP_VALIDATION=true; shift ;;
        -h|--help) show_help; exit 0 ;;
        *) print_error "Unknown option: $1" && echo && show_help && exit 1 ;;
    esac
done

# Initialize
[ "$DRY_RUN" = false ] && check_root
print_header
print_info "Service user: $SERVICE_USER"
print_info "Service group: $SERVICE_GROUP"
print_info "Project root: $PROJECT_ROOT"
echo

# Validate and create
[ "$SKIP_VALIDATION" = false ] && validate_setup || print_warning "Skipping setup validation"

print_section "Creating Systemd Service Files"
create_vosk_service
create_agents_service

# Reload systemd
if [ "$DRY_RUN" = false ]; then
    print_section "Reloading Systemd"
    systemctl daemon-reload
    print_success "Systemd daemon reloaded"
    
    [ "$ENABLE_SERVICES" = true ] && { print_section "Enabling Services"; manage_services "enable"; }
    [ "$START_SERVICES" = true ] && { print_section "Starting Services"; manage_services "start"; }
    [ "$START_SERVICES" = true ] && { 
        echo && print_section "Service Status"
        systemctl status mezon-server-vosk-service mezon-agents-service --no-pager -l || true
    }
fi

show_summary