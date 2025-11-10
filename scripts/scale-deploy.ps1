# Mezon Call Translation - Scaling Management Script (PowerShell)
# Usage: .\scripts\scale-deploy.ps1 [start|stop|restart|scale] [number_of_servers]

param(
    [Parameter(Position=0)]
    [string]$Command = "help",
    
    [Parameter(Position=1)]
    [int]$NumServers = 3
)

# Default configuration
$DEFAULT_SERVERS = 3
$PROJECT_DIR = Split-Path -Parent $PSScriptRoot

# Function to print colored output
function Write-Status {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Blue
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

# Function to check if Docker and Docker Compose are available
function Test-Requirements {
    Write-Status "Checking requirements..."
    
    try {
        $null = Get-Command docker -ErrorAction Stop
    }
    catch {
        Write-Error-Custom "Docker is not installed or not in PATH"
        exit 1
    }
    
    try {
        $null = Get-Command docker-compose -ErrorAction Stop
    }
    catch {
        try {
            docker compose version | Out-Null
        }
        catch {
            Write-Error-Custom "Docker Compose is not installed or not in PATH"
            exit 1
        }
    }
    
    Write-Success "Requirements check passed"
}

# Function to start the scaled deployment
function Start-Deployment {
    param([int]$NumServers = $DEFAULT_SERVERS)
    
    Write-Status "Starting deployment with $NumServers server instances..."
    
    Set-Location $PROJECT_DIR
    
    # Build images first
    Write-Status "Building Docker images..."
    docker-compose build
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Failed to build Docker images"
        exit 1
    }
    
    # Start with scaling
    Write-Status "Starting services with scaling..."
    docker-compose up -d --scale server=$NumServers
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Failed to start services"
        exit 1
    }
    
    Write-Success "Deployment started successfully!"
    Show-Status
}

# Function to stop the deployment
function Stop-Deployment {
    Write-Status "Stopping deployment..."
    
    Set-Location $PROJECT_DIR
    docker-compose down
    
    Write-Success "Deployment stopped successfully!"
}

# Function to restart the deployment
function Restart-Deployment {
    param([int]$NumServers = $DEFAULT_SERVERS)
    
    Write-Status "Restarting deployment..."
    Stop-Deployment
    Start-Sleep -Seconds 2
    Start-Deployment -NumServers $NumServers
}

# Function to scale existing deployment
function Scale-Deployment {
    param([int]$NumServers = $DEFAULT_SERVERS)
    
    Write-Status "Scaling server instances to $NumServers..."
    
    Set-Location $PROJECT_DIR
    docker-compose up -d --scale server=$NumServers
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Failed to scale deployment"
        exit 1
    }
    
    Write-Success "Scaling completed!"
    Show-Status
}

# Function to show current status
function Show-Status {
    Write-Status "Current deployment status:"
    Write-Host ""
    
    Set-Location $PROJECT_DIR
    
    # Show running containers
    Write-Status "Running containers:"
    docker-compose ps
    Write-Host ""
    
    # Show server instances
    $serverContainers = docker-compose ps server | Select-String "Up"
    $serverCount = if ($serverContainers) { $serverContainers.Count } else { 0 }
    Write-Status "Server instances running: $serverCount"
    
    # Test connectivity
    Write-Status "Testing connectivity..."
    Start-Sleep -Seconds 5
    
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8000/health/simple" -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -eq 200) {
            Write-Success "Health check passed - Load balancer is working!"
        }
        else {
            Write-Warning "Health check returned status: $($response.StatusCode)"
        }
    }
    catch {
        Write-Warning "Health check failed - Service may still be starting up"
    }
}

# Function to show help
function Show-Help {
    Write-Host "Mezon Call Translation - Scaling Management Script (PowerShell)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Usage: .\scripts\scale-deploy.ps1 [COMMAND] [OPTIONS]" -ForegroundColor White
    Write-Host ""
    Write-Host "Commands:" -ForegroundColor White
    Write-Host "  start [NUM]    Start deployment with NUM server instances (default: $DEFAULT_SERVERS)" -ForegroundColor Gray
    Write-Host "  stop           Stop the entire deployment" -ForegroundColor Gray
    Write-Host "  restart [NUM]  Restart deployment with NUM server instances" -ForegroundColor Gray
    Write-Host "  scale [NUM]    Scale existing deployment to NUM server instances" -ForegroundColor Gray
    Write-Host "  status         Show current deployment status" -ForegroundColor Gray
    Write-Host "  logs [SERVICE] Show logs for SERVICE (server, nginx, agent, or all)" -ForegroundColor Gray
    Write-Host "  help           Show this help message" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Examples:" -ForegroundColor White
    Write-Host "  .\scripts\scale-deploy.ps1 start 5         # Start with 5 server instances" -ForegroundColor Gray
    Write-Host "  .\scripts\scale-deploy.ps1 scale 10        # Scale to 10 server instances" -ForegroundColor Gray
    Write-Host "  .\scripts\scale-deploy.ps1 restart 3       # Restart with 3 server instances" -ForegroundColor Gray
    Write-Host "  .\scripts\scale-deploy.ps1 logs server     # Show server logs" -ForegroundColor Gray
    Write-Host ""
}

# Function to show logs
function Show-Logs {
    param([string]$Service = "")
    
    Set-Location $PROJECT_DIR
    
    if ([string]::IsNullOrEmpty($Service) -or $Service -eq "all") {
        Write-Status "Showing logs for all services..."
        docker-compose logs -f
    }
    else {
        Write-Status "Showing logs for $Service..."
        docker-compose logs -f $Service
    }
}

# Main script logic
function Main {
    switch ($Command.ToLower()) {
        "start" {
            Test-Requirements
            Start-Deployment -NumServers $NumServers
        }
        "stop" {
            Test-Requirements
            Stop-Deployment
        }
        "restart" {
            Test-Requirements
            Restart-Deployment -NumServers $NumServers
        }
        "scale" {
            Test-Requirements
            Scale-Deployment -NumServers $NumServers
        }
        "status" {
            Show-Status
        }
        "logs" {
            $logService = if ($args.Length -gt 1) { $args[1] } else { "" }
            Show-Logs -Service $logService
        }
        { $_ -in @("help", "--help", "-h") } {
            Show-Help
        }
        default {
            Write-Error-Custom "Unknown command: $Command"
            Write-Host ""
            Show-Help
            exit 1
        }
    }
}

# Run main function
Main