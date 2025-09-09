# Development run script (Windows PowerShell)

Write-Host "🚀 Starting Mezon Call Translation in Development Mode" -ForegroundColor Green

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
    Write-Host "⚠️  .env file not found. Running setup..." -ForegroundColor Yellow
    .\scripts\setup.ps1
}

# Kiểm tra Vosk model
if (-not (Test-Path "models/vosk-model/vosk-model-small-en-us-0.15")) {
    Write-Host "⚠️  Vosk model not found. Downloading..." -ForegroundColor Yellow
    .\scripts\download-vosk-model.ps1
}

Write-Host "Starting services with hot reload..." -ForegroundColor Cyan
docker-compose -f docker-compose.yml -f docker-compose.override.yml up
