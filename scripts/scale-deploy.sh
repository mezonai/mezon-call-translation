#!/usr/bin/env bash

# Mezon Call Translation - Scaling Management Script
# Usage: ./scripts/scale-deploy.sh [start|stop|restart|scale] [number_of_servers]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Default number of server instances
DEFAULT_SERVERS=3

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if Docker and Docker Compose are available
check_requirements() {
    print_status "Checking requirements..."
    
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed or not in PATH"
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        print_error "Docker Compose is not installed or not in PATH"
        exit 1
    fi
    
    print_success "Requirements check passed"
}

# Function to start the scaled deployment
start_deployment() {
    local num_servers=${1:-$DEFAULT_SERVERS}
    
    print_status "Starting deployment with $num_servers server instances..."
    
    cd "$PROJECT_DIR"
    
    # Build images first
    print_status "Building Docker images..."
    docker-compose build
    
    # Start with scaling
    print_status "Starting services with scaling..."
    docker-compose up -d --scale server=$num_servers
    
    print_success "Deployment started successfully!"
    show_status
}

# Function to stop the deployment
stop_deployment() {
    print_status "Stopping deployment..."
    
    cd "$PROJECT_DIR"
    docker-compose down
    
    print_success "Deployment stopped successfully!"
}

# Function to restart the deployment
restart_deployment() {
    local num_servers=${1:-$DEFAULT_SERVERS}
    
    print_status "Restarting deployment..."
    stop_deployment
    sleep 2
    start_deployment "$num_servers"
}

# Function to scale existing deployment
scale_deployment() {
    local num_servers=${1:-$DEFAULT_SERVERS}
    
    print_status "Scaling server instances to $num_servers..."
    
    cd "$PROJECT_DIR"
    docker-compose up -d --scale server=$num_servers
    
    print_success "Scaling completed!"
    show_status
}

# Function to show current status
show_status() {
    print_status "Current deployment status:"
    echo ""
    
    cd "$PROJECT_DIR"
    
    # Show running containers
    print_status "Running containers:"
    docker-compose ps
    echo ""
    
    # Show server instances
    local server_count=$(docker-compose ps server | grep -c "Up" || echo "0")
    print_status "Server instances running: $server_count"
    
    # Test connectivity
    print_status "Testing connectivity..."
    sleep 5
    
    if curl -s http://localhost:8000/health/simple > /dev/null; then
        print_success "Health check passed - Load balancer is working!"
    else
        print_warning "Health check failed - Service may still be starting up"
    fi
}

# Function to show help
show_help() {
    echo "Mezon Call Translation - Scaling Management Script"
    echo ""
    echo "Usage: $0 [COMMAND] [OPTIONS]"
    echo ""
    echo "Commands:"
    echo "  start [NUM]    Start deployment with NUM server instances (default: $DEFAULT_SERVERS)"
    echo "  stop           Stop the entire deployment"
    echo "  restart [NUM]  Restart deployment with NUM server instances"
    echo "  scale [NUM]    Scale existing deployment to NUM server instances"
    echo "  status         Show current deployment status"
    echo "  logs [SERVICE] Show logs for SERVICE (server, nginx, agent, or all)"
    echo "  help           Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 start 5                 # Start with 5 server instances"
    echo "  $0 scale 10                # Scale to 10 server instances"
    echo "  $0 restart 3               # Restart with 3 server instances"
    echo "  $0 logs server             # Show server logs"
    echo ""
}

# Function to show logs
show_logs() {
    local service=${1:-""}
    
    cd "$PROJECT_DIR"
    
    if [ -z "$service" ] || [ "$service" = "all" ]; then
        print_status "Showing logs for all services..."
        docker-compose logs -f
    else
        print_status "Showing logs for $service..."
        docker-compose logs -f "$service"
    fi
}

# Main script logic
main() {
    local command=${1:-"help"}
    local param2=${2:-""}
    
    case "$command" in
        "start")
            check_requirements
            start_deployment "$param2"
            ;;
        "stop")
            check_requirements
            stop_deployment
            ;;
        "restart")
            check_requirements
            restart_deployment "$param2"
            ;;
        "scale")
            check_requirements
            scale_deployment "$param2"
            ;;
        "status")
            show_status
            ;;
        "logs")
            show_logs "$param2"
            ;;
        "help"|"--help"|"-h")
            show_help
            ;;
        *)
            print_error "Unknown command: $command"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"