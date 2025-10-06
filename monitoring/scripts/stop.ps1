# Stop Monitoring Stack Script (PowerShell)

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Stopping Mezon Monitoring Stack" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Stop services
Write-Host ""
Write-Host "Stopping monitoring services..." -ForegroundColor Yellow
docker-compose down

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "✅ Monitoring Stack Stopped Successfully!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "To start again:" -ForegroundColor Yellow
Write-Host "  .\scripts\start.ps1" -ForegroundColor White
Write-Host ""
Write-Host "To remove all data (WARNING: This deletes all metrics and dashboards):" -ForegroundColor Yellow
Write-Host "  docker-compose down -v" -ForegroundColor White
Write-Host "==========================================" -ForegroundColor Cyan
