# Download Kokoro-82M TTS Model
# PowerShell script for Windows

param(
    [string]$OutputDir = "models\kokoro_models",
    [string]$Voices = "",
    [switch]$AllVoices,
    [switch]$Force,
    [switch]$List,
    [switch]$Info,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

function Write-Header {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "    Kokoro-82M TTS Model Downloader" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Write-InfoMsg {
    param([string]$Message)
    Write-Host "ℹ️  $Message" -ForegroundColor Cyan
}

function Write-SuccessMsg {
    param([string]$Message)
    Write-Host "✅ $Message" -ForegroundColor Green
}

function Write-WarningMsg {
    param([string]$Message)
    Write-Host "⚠️  $Message" -ForegroundColor Yellow
}

function Write-ErrorMsg {
    param([string]$Message)
    Write-Host "❌ $Message" -ForegroundColor Red
}

function Show-Help {
    Write-Host @"
Download Kokoro-82M TTS Model

USAGE:
    .\scripts\download-kokoro-model.ps1 [options]

OPTIONS:
    -OutputDir <path>    Output directory (default: models\kokoro_models)
    -Voices <list>       Comma-separated voice names (e.g., af_heart,am_adam)
    -AllVoices          Download all available voices
    -Force              Force re-download even if files exist
    -List               List downloaded voices
    -Info               Show model information
    -Help               Show this help message

EXAMPLES:
    # Download model with default voices
    .\scripts\download-kokoro-model.ps1

    # Download specific voices
    .\scripts\download-kokoro-model.ps1 -Voices "af_heart,af_bella,am_adam"

    # Download all available voices
    .\scripts\download-kokoro-model.ps1 -AllVoices

    # Force re-download
    .\scripts\download-kokoro-model.ps1 -Force

    # List downloaded voices
    .\scripts\download-kokoro-model.ps1 -List

AVAILABLE VOICES:
    American Female: af_heart, af_bella, af_sarah, af_nicole, af_sky
    American Male:   am_adam, am_michael, am_liam
    British Female:  bf_emma, bf_isabella
    British Male:    bm_george, bm_lewis

MORE INFO:
    https://huggingface.co/hexgrad/Kokoro-82M
"@
}

# Show help if requested
if ($Help) {
    Show-Help
    exit 0
}

# Get project root
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ModelDir = Join-Path $ProjectRoot $OutputDir

# Handle --list flag
if ($List) {
    Write-Header
    $VoicesDir = Join-Path $ModelDir "voices"
    
    Write-Host "Downloaded Voices:" -ForegroundColor White
    Write-Host "============================================================"
    
    if (-not (Test-Path $VoicesDir)) {
        Write-WarningMsg "No voices directory found"
        exit 0
    }
    
    $VoiceFiles = Get-ChildItem -Path $VoicesDir -Filter "*.pt" | Sort-Object Name
    
    if ($VoiceFiles.Count -eq 0) {
        Write-WarningMsg "No voices downloaded yet"
        exit 0
    }
    
    foreach ($VoiceFile in $VoiceFiles) {
        $VoiceName = $VoiceFile.BaseName
        $SizeMB = [math]::Round($VoiceFile.Length / 1MB, 2)
        Write-Host "  ✓ $($VoiceName.PadRight(20)) ($SizeMB MB)" -ForegroundColor Green
    }
    
    Write-Host ""
    Write-Host "Total: $($VoiceFiles.Count) voices"
    exit 0
}

# Handle --info flag
if ($Info) {
    Write-Header
    
    Write-Host "Model Information:" -ForegroundColor White
    Write-Host "============================================================"
    Write-Host "Model directory: $ModelDir"
    Write-Host "Repository: hexgrad/Kokoro-82M"
    Write-Host ""
    
    $ModelPath = Join-Path $ModelDir "kokoro-v0_19.pth"
    $ConfigPath = Join-Path $ModelDir "config.json"
    $VoicesDir = Join-Path $ModelDir "voices"
    
    if (Test-Path $ModelPath) {
        $SizeMB = [math]::Round((Get-Item $ModelPath).Length / 1MB, 1)
        Write-Host "  ✓ Model: kokoro-v0_19.pth ($SizeMB MB)" -ForegroundColor Green
    } else {
        Write-Host "  ✗ Model: Not downloaded" -ForegroundColor Red
    }
    
    if (Test-Path $ConfigPath) {
        Write-Host "  ✓ Config: config.json" -ForegroundColor Green
    } else {
        Write-Host "  ✗ Config: Not downloaded" -ForegroundColor Red
    }
    
    if (Test-Path $VoicesDir) {
        $VoiceCount = (Get-ChildItem -Path $VoicesDir -Filter "*.pt").Count
        Write-Host "  ✓ Voices: $VoiceCount downloaded" -ForegroundColor Green
    } else {
        Write-Host "  ✗ Voices: 0 downloaded" -ForegroundColor Red
    }
    
    exit 0
}

# Print header
Write-Header

# Check Python
Write-InfoMsg "Checking Python installation..."
try {
    $PythonVersion = & python --version 2>&1
    Write-SuccessMsg "Python: $PythonVersion"
} catch {
    Write-ErrorMsg "Python not found. Please install Python 3.8 or higher."
    exit 1
}

Write-Host ""

# Build command arguments
$PythonScript = Join-Path $ProjectRoot "scripts\download-kokoro-model.py"
$Args = @($PythonScript)

if ($OutputDir) {
    $Args += "--output"
    $Args += $OutputDir
}

if ($Voices) {
    $Args += "--voices"
    $Args += $Voices
}

if ($AllVoices) {
    $Args += "--all-voices"
}

if ($Force) {
    $Args += "--force"
}

# Run Python script
Write-InfoMsg "Running download script..."
Write-Host ""

try {
    & python $Args
    $ExitCode = $LASTEXITCODE
    
    if ($ExitCode -eq 0) {
        Write-Host ""
        Write-SuccessMsg "Script completed successfully!"
    } else {
        Write-Host ""
        Write-ErrorMsg "Script failed with exit code: $ExitCode"
    }
    
    exit $ExitCode
    
} catch {
    Write-Host ""
    Write-ErrorMsg "Failed to run Python script: $_"
    exit 1
}
