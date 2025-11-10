# Setup script for Mezon Call Translation Docker (Windows PowerShell)

Write-Host "Setting up Mezon Call Translation Docker Environment" -ForegroundColor Green

# Create necessary directories
Write-Host "Creating necessary directories..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "models" | Out-Null
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
New-Item -ItemType Directory -Force -Path "scripts" | Out-Null

# Copy environment file if not exists
if (-not (Test-Path ".env")) {
    Write-Host "Creating .env file from template..." -ForegroundColor Yellow
    Copy-Item "env.example" ".env"
    Write-Host "Please edit .env file with your actual configuration" -ForegroundColor Red
}

# Create Vosk models directory
Write-Host "Creating models directory structure..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "models/vosk-model" | Out-Null

Write-Host "Setup completed!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Edit .env file with your LiveKit credentials" -ForegroundColor White
Write-Host "2. Download Vosk model and place it in models/vosk-model/" -ForegroundColor White
Write-Host "3. Run: docker-compose up -d" -ForegroundColor White
Write-Host ""
Write-Host "For development with hot reload:" -ForegroundColor Cyan
Write-Host "docker-compose -f docker-compose.yml -f docker-compose.override.yml up" -ForegroundColor White
