# Production run script (Windows PowerShell)

Write-Host "🚀 Starting Mezon Call Translation in Production Mode" -ForegroundColor Green

# Kiểm tra Docker có chạy không
try {
    docker --version | Out-Null
    Write-Host "✅ Docker is running" -ForegroundColor Green
} catch {
    Write-Host "❌ Docker is not running. Please start Docker Desktop first." -ForegroundColor Red
    exit 1
}

# Kiểm tra file .env
if (-not (Test-Path ".env")) {
    Write-Host "❌ .env file not found. Please run setup first:" -ForegroundColor Red
    Write-Host ".\scripts\setup.ps1" -ForegroundColor Yellow
    exit 1
}

# Kiểm tra Vosk model
if (-not (Test-Path "models/vosk-model/vosk-model-small-en-us-0.15")) {
    Write-Host "❌ Vosk model not found. Please download first:" -ForegroundColor Red
    Write-Host ".\scripts\download-vosk-model.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host "Building and starting services..." -ForegroundColor Cyan
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d

Write-Host "✅ Services started in background!" -ForegroundColor Green
Write-Host ""
Write-Host "To view logs:" -ForegroundColor Cyan
Write-Host "docker-compose logs -f" -ForegroundColor White
Write-Host ""
Write-Host "To stop services:" -ForegroundColor Cyan
Write-Host "docker-compose down" -ForegroundColor White
