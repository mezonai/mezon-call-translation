# Start Monitoring Stack Script (PowerShell)

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Starting Mezon Monitoring Stack" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Check if .env exists
if (-not (Test-Path .env)) {
    Write-Host "⚠️  .env file not found. Creating from .env.example..." -ForegroundColor Yellow
    Copy-Item .env.example .env
    Write-Host "✅ Created .env file. Please review and update if needed." -ForegroundColor Green
}

# Check if main application is running
Write-Host ""
Write-Host "Checking main application..." -ForegroundColor Yellow
$networks = docker network ls --format "{{.Name}}"
if ($networks -notcontains "mezon-call-translation_mezon-network") {
    Write-Host "⚠️  Main application network not found!" -ForegroundColor Red
    Write-Host "Please start the main application first:" -ForegroundColor Yellow
    Write-Host "  cd .. && docker-compose up -d" -ForegroundColor White
    exit 1
}

Write-Host "✅ Main application network found" -ForegroundColor Green

# Start monitoring stack
Write-Host ""
Write-Host "Starting monitoring services..." -ForegroundColor Yellow
docker-compose up -d

# Wait for services to be healthy
Write-Host ""
Write-Host "Waiting for services to be healthy..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

# Check Prometheus
Write-Host ""
Write-Host "Checking Prometheus..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:9090/-/healthy" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Write-Host "✅ Prometheus is healthy" -ForegroundColor Green
    }
} catch {
    Write-Host "❌ Prometheus is not responding" -ForegroundColor Red
}

# Check Grafana
Write-Host ""
Write-Host "Checking Grafana..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:3000/api/health" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Write-Host "✅ Grafana is healthy" -ForegroundColor Green
    }
} catch {
    Write-Host "❌ Grafana is not responding" -ForegroundColor Red
}

# Check AlertManager
Write-Host ""
Write-Host "Checking AlertManager..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:9093/-/healthy" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Write-Host "✅ AlertManager is healthy" -ForegroundColor Green
    }
} catch {
    Write-Host "❌ AlertManager is not responding" -ForegroundColor Red
}

# Display access information
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "✅ Monitoring Stack Started Successfully!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Access URLs:" -ForegroundColor Yellow
Write-Host "  Prometheus:   http://localhost:9090" -ForegroundColor White
Write-Host "  Grafana:      http://localhost:3000" -ForegroundColor White
Write-Host "  AlertManager: http://localhost:9093" -ForegroundColor White
Write-Host ""
Write-Host "Grafana Credentials:" -ForegroundColor Yellow
Write-Host "  Username: admin" -ForegroundColor White
Write-Host "  Password: admin (change in .env)" -ForegroundColor White
Write-Host ""
Write-Host "To view logs:" -ForegroundColor Yellow
Write-Host "  docker-compose logs -f" -ForegroundColor White
Write-Host ""
Write-Host "To stop:" -ForegroundColor Yellow
Write-Host "  docker-compose down" -ForegroundColor White
Write-Host "==========================================" -ForegroundColor Cyan
