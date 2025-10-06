# Check Prometheus Targets Script (PowerShell)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Checking Prometheus Targets" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Check if Prometheus is running
try {
    $response = Invoke-WebRequest -Uri "http://localhost:9090/-/healthy" -UseBasicParsing -TimeoutSec 5
    Write-Host ""
    Write-Host "✅ Prometheus is running" -ForegroundColor Green
} catch {
    Write-Host ""
    Write-Host "❌ Prometheus is not running!" -ForegroundColor Red
    Write-Host "Please start monitoring stack first: .\scripts\start.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host ""

# Get targets from Prometheus API
Write-Host "Fetching targets from Prometheus..." -ForegroundColor Yellow
Write-Host ""

try {
    $targetsResponse = Invoke-RestMethod -Uri "http://localhost:9090/api/v1/targets" -Method Get
    
    Write-Host "Active Targets:" -ForegroundColor Cyan
    Write-Host "----------------------------------------" -ForegroundColor Gray
    Write-Host ("{0,-30} {1,-40} {2,-10} {3}" -f "JOB", "TARGET", "HEALTH", "LAST ERROR") -ForegroundColor White
    Write-Host "----------------------------------------" -ForegroundColor Gray
    
    foreach ($target in $targetsResponse.data.activeTargets) {
        $job = $target.labels.job
        $scrapeUrl = $target.scrapeUrl
        $health = $target.health
        $lastError = if ($target.lastError) { $target.lastError } else { "none" }
        
        if ($health -eq "up") {
            Write-Host ("{0,-30} {1,-40} {2,-10} {3}" -f $job, $scrapeUrl, $health, $lastError) -ForegroundColor Green
        } else {
            Write-Host ("{0,-30} {1,-40} {2,-10} {3}" -f $job, $scrapeUrl, $health, $lastError) -ForegroundColor Red
        }
    }
} catch {
    Write-Host "Failed to fetch targets: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Server Instances Discovery" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Check Docker network
Write-Host ""
Write-Host "Checking Docker containers in mezon-network..." -ForegroundColor Yellow
try {
    $networkInfo = docker network inspect mezon-call-translation_mezon-network --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}' 2>$null
    if ($networkInfo) {
        Write-Host $networkInfo -ForegroundColor White
    } else {
        Write-Host "Network not found" -ForegroundColor Red
    }
} catch {
    Write-Host "Error inspecting network" -ForegroundColor Red
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "DNS Resolution Test" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Test DNS resolution from Prometheus container
Write-Host ""
Write-Host "Testing DNS resolution from Prometheus container..." -ForegroundColor Yellow
Write-Host ""
Write-Host "Resolving 'server':" -ForegroundColor White
docker exec mezon-prometheus nslookup server 2>$null
Write-Host ""
Write-Host "Resolving 'tasks.server':" -ForegroundColor White
docker exec mezon-prometheus nslookup tasks.server 2>$null

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Connectivity Test" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Test connectivity to server
Write-Host ""
Write-Host "Testing connectivity to server:8000..." -ForegroundColor Yellow
$healthCheck = docker exec mezon-prometheus wget -q -O- --timeout=5 http://server:8000/health 2>$null
if ($healthCheck) {
    Write-Host "✅ Can connect to server:8000" -ForegroundColor Green
} else {
    Write-Host "❌ Cannot connect to server:8000" -ForegroundColor Red
}

Write-Host ""
Write-Host "Testing metrics endpoint..." -ForegroundColor Yellow
$metricsCheck = docker exec mezon-prometheus wget -q -O- --timeout=5 http://server:8000/metrics 2>$null
if ($metricsCheck) {
    $metricsCheck.Split("`n") | Select-Object -First 5 | ForEach-Object { Write-Host $_ -ForegroundColor Gray }
    Write-Host "..." -ForegroundColor Gray
    Write-Host "✅ Metrics endpoint is accessible" -ForegroundColor Green
} else {
    Write-Host "❌ Cannot access metrics endpoint" -ForegroundColor Red
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "To view in Prometheus UI:" -ForegroundColor Yellow
Write-Host "  http://localhost:9090/targets" -ForegroundColor White
Write-Host "==========================================" -ForegroundColor Cyan
