# Download Vosk STT Model
# PowerShell script for Windows

param(
    [string]$Model = "vosk-model-small-en-us-0.15",
    [string]$OutputDir = "models\vosk-model",
    [switch]$Force,
    [switch]$List,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

function Show-Help {
    Write-Host ""
    Write-Host "Download Vosk STT Model"
    Write-Host ""
    Write-Host "USAGE:"
    Write-Host "    .\scripts\download-vosk-model.ps1 [options]"
    Write-Host ""
    Write-Host "OPTIONS:"
    Write-Host "    -Model <name>       Model name to download (default: vosk-model-small-en-us-0.15)"
    Write-Host "    -OutputDir <path>   Output directory (default: models\vosk-model)"
    Write-Host "    -Force              Force re-download even if model exists"
    Write-Host "    -List               List available models"
    Write-Host "    -Help               Show this help message"
    Write-Host ""
    Write-Host "EXAMPLES:"
    Write-Host "    # Download default small model"
    Write-Host "    .\scripts\download-vosk-model.ps1"
    Write-Host ""
    Write-Host "    # Download large model"
    Write-Host "    .\scripts\download-vosk-model.ps1 -Model vosk-model-en-us-0.22"
    Write-Host ""
    Write-Host "    # List available models"
    Write-Host "    .\scripts\download-vosk-model.ps1 -List"
    Write-Host ""
}

# Show help if requested
if ($Help) {
    Show-Help
    exit 0
}

# Get project root
$ProjectRoot = Split-Path -Parent $PSScriptRoot

# Print header
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "    Vosk STT Model Downloader" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check Python
Write-Host "[INFO] Checking Python installation..." -ForegroundColor Cyan
try {
    $PythonVersion = & python --version 2>&1
    Write-Host "[OK] Python: $PythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python not found. Please install Python 3.8 or higher." -ForegroundColor Red
    exit 1
}

Write-Host ""

# Build command arguments
$PythonScript = Join-Path $ProjectRoot "scripts\download-vosk-model.py"
$Args = @($PythonScript)

if ($Model) {
    $Args += "--model"
    $Args += $Model
}

if ($OutputDir) {
    $Args += "--output"
    $Args += $OutputDir
}

if ($Force) {
    $Args += "--force"
}

if ($List) {
    $Args += "--list"
}

# Run Python script
Write-Host "[INFO] Running download script..." -ForegroundColor Cyan
Write-Host ""

try {
    & python $Args
    $ExitCode = $LASTEXITCODE
    
    if ($ExitCode -eq 0) {
        Write-Host ""
        Write-Host "[OK] Script completed successfully!" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "[ERROR] Script failed with exit code: $ExitCode" -ForegroundColor Red
    }
    
    exit $ExitCode
    
} catch {
    Write-Host ""
    $ErrorMessage = $_.Exception.Message
    Write-Host "[ERROR] Failed to run Python script: $ErrorMessage" -ForegroundColor Red
    exit 1
}
