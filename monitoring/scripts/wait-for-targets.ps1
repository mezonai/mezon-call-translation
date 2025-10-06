# Wait for Prometheus Targets to be Ready

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Waiting for Prometheus Targets" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

$maxAttempts = 12
$attempt = 0
$allUp = $false

while (-not $allUp -and $attempt -lt $maxAttempts) {
    $attempt++
    Write-Host "Attempt $attempt/$maxAttempts..." -ForegroundColor Yellow
    
    try {
        $targetsResponse = Invoke-RestMethod -Uri "http://localhost:9090/api/v1/targets" -Method Get -ErrorAction Stop
        
        $targets = $targetsResponse.data.activeTargets
        $totalTargets = $targets.Count
        $upTargets = ($targets | Where-Object { $_.health -eq "up" }).Count
        $unknownTargets = ($targets | Where-Object { $_.health -eq "unknown" }).Count
        $downTargets = ($targets | Where-Object { $_.health -eq "down" }).Count
        
        Write-Host "  Total: $totalTargets | Up: $upTargets | Unknown: $unknownTargets | Down: $downTargets" -ForegroundColor White
        
        if ($unknownTargets -eq 0 -and $downTargets -eq 0 -and $upTargets -gt 0) {
            $allUp = $true
            Write-Host ""
            Write-Host "✅ All targets are UP!" -ForegroundColor Green
            break
        }
        
        if ($downTargets -gt 0) {
            Write-Host ""
            Write-Host "⚠️  Some targets are DOWN:" -ForegroundColor Red
            foreach ($target in $targets | Where-Object { $_.health -eq "down" }) {
                Write-Host "  - $($target.labels.job): $($target.lastError)" -ForegroundColor Red
            }
        }
        
    } catch {
        Write-Host "  ❌ Cannot connect to Prometheus" -ForegroundColor Red
    }
    
    if (-not $allUp) {
        Start-Sleep -Seconds 5
    }
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Final Status" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

try {
    $targetsResponse = Invoke-RestMethod -Uri "http://localhost:9090/api/v1/targets" -Method Get
    
    Write-Host ("{0,-30} {1,-40} {2,-10}" -f "JOB", "TARGET", "HEALTH") -ForegroundColor White
    Write-Host "----------------------------------------" -ForegroundColor Gray
    
    foreach ($target in $targetsResponse.data.activeTargets) {
        $job = $target.labels.job
        $scrapeUrl = $target.scrapeUrl
        $health = $target.health
        
        $color = switch ($health) {
            "up" { "Green" }
            "down" { "Red" }
            "unknown" { "Yellow" }
            default { "White" }
        }
        
        Write-Host ("{0,-30} {1,-40} {2,-10}" -f $job, $scrapeUrl, $health) -ForegroundColor $color
    }
} catch {
    Write-Host "Failed to fetch final status" -ForegroundColor Red
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "View in Prometheus UI:" -ForegroundColor Yellow
Write-Host "  http://localhost:9090/targets" -ForegroundColor White
Write-Host "==========================================" -ForegroundColor Cyan
