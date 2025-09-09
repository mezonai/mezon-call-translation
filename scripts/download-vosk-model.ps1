# Script to download Vosk model (Windows PowerShell)

Write-Host "Downloading Vosk model..." -ForegroundColor Green

# Create models directory if not exists
New-Item -ItemType Directory -Force -Path "models/vosk-model" | Out-Null

# Change to models directory
Set-Location "models/vosk-model"

Write-Host "Downloading English model (small)..." -ForegroundColor Yellow
$modelUrl = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
$modelFile = "vosk-model-small-en-us-0.15.zip"

try {
    Invoke-WebRequest -Uri $modelUrl -OutFile $modelFile
    Write-Host "Download completed!" -ForegroundColor Green
} catch {
    Write-Host "Download failed: $($_.Exception.Message)" -ForegroundColor Red
    Set-Location "../.."
    exit 1
}

Write-Host "Extracting model..." -ForegroundColor Yellow
try {
    Expand-Archive -Path $modelFile -DestinationPath "." -Force
    Write-Host "Extraction completed!" -ForegroundColor Green
} catch {
    Write-Host "Extraction failed: $($_.Exception.Message)" -ForegroundColor Red
    Set-Location "../.."
    exit 1
}

Write-Host "Cleaning up..." -ForegroundColor Yellow
Remove-Item $modelFile

Write-Host "Vosk model downloaded successfully!" -ForegroundColor Green
Write-Host "Model location: $(Get-Location)/vosk-model-small-en-us-0.15" -ForegroundColor Cyan

# Return to root directory
Set-Location "../.."

Write-Host ""
Write-Host "Available models:" -ForegroundColor Cyan
Write-Host "- English (small): https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" -ForegroundColor White
Write-Host "- English (medium): https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip" -ForegroundColor White
Write-Host "- Vietnamese: https://alphacephei.com/vosk/models/vosk-model-small-vi-0.4.zip" -ForegroundColor White
Write-Host ""
Write-Host "To download other models, modify this script or download manually to models/vosk-model/" -ForegroundColor Yellow