#!/bin/bash
# Manage all Mezon Call Translation services
# Quick helper script to control all services at once

set -e

# Colors
CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Service names
SERVICES=(
    "mezon-stt-service"
    "mezon-orchestrator-service"
    "mezon-agents-service"
)

# Functions
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
Mezon Call Translation - Service Manager

USAGE:
    sudo ./scripts/manage-services.sh <command>

COMMANDS:
    start           Start all services
    stop            Stop all services
    restart         Restart all services
    status          Show status of all services
    enable          Enable all services to start on boot
    disable         Disable all services from starting on boot
    logs            Show logs from all services (last 20 lines each)
    follow          Follow logs from all services in real-time
    -h, --help      Show this help message

EXAMPLES:
    # Start all services
    sudo ./scripts/manage-services.sh start

    # Check status of all services
    sudo ./scripts/manage-services.sh status

    # Follow logs in real-time
    sudo ./scripts/manage-services.sh follow

EOF
}

# Check if running as root for commands that need it
check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "This command must be run as root (use sudo)"
        exit 1
    fi
}

# Execute command for all services
execute_for_all() {
    local command=$1
    local needs_root=$2
    
    if [ "$needs_root" = true ]; then
        check_root
    fi
    
    echo ""
    for service in "${SERVICES[@]}"; do
        print_info "Executing '$command' for $service..."
        
        case $command in
            start|stop|restart|enable|disable)
                if systemctl "$command" "$service" 2>&1; then
                    print_success "$service $command completed"
                else
                    print_error "$service $command failed"
                fi
                ;;
            status)
                systemctl status "$service" --no-pager || true
                ;;
        esac
        echo ""
    done
}

# Show logs
show_logs() {
    local lines=${1:-20}
    
    echo ""
    for service in "${SERVICES[@]}"; do
        echo -e "${CYAN}${BOLD}=== Logs for $service (last $lines lines) ===${NC}"
        journalctl -u "$service" -n "$lines" --no-pager || true
        echo ""
    done
}

# Follow logs
follow_logs() {
    print_info "Following logs for all services (Ctrl+C to stop)..."
    echo ""
    
    # Build journalctl command with all services
    local journal_cmd="journalctl -f"
    for service in "${SERVICES[@]}"; do
        journal_cmd="$journal_cmd -u $service"
    done
    
    eval "$journal_cmd"
}

# Parse command
if [ $# -eq 0 ]; then
    show_help
    exit 1
fi

COMMAND=$1

case $COMMAND in
    start)
        execute_for_all "start" true
        print_success "All services started!"
        ;;
    stop)
        execute_for_all "stop" true
        print_success "All services stopped!"
        ;;
    restart)
        execute_for_all "restart" true
        print_success "All services restarted!"
        ;;
    status)
        execute_for_all "status" false
        ;;
    enable)
        execute_for_all "enable" true
        print_success "All services enabled to start on boot!"
        ;;
    disable)
        execute_for_all "disable" true
        print_success "All services disabled from starting on boot!"
        ;;
    logs)
        show_logs 20
        ;;
    follow)
        follow_logs
        ;;
    -h|--help)
        show_help
        exit 0
        ;;
    *)
        print_error "Unknown command: $COMMAND"
        echo ""
        show_help
        exit 1
        ;;
esac
