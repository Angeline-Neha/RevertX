$root = $PSScriptRoot

# Pre-flight cleanup. Without this, re-running the script after a previous
# session (whose jobs are still tracked, or whose child python processes
# were orphaned when the window was closed instead of properly stopped)
# leaves the old listeners holding ports 8000-8005. The new Start-Job calls
# below then fail to bind, fail silently into "Application shutdown
# complete", and every request for the rest of the session gets served by
# the STALE OLD PROCESSES instead of whatever's on disk right now — with no
# error surfaced anywhere obvious. Clear both possible causes before
# starting anything new.
Write-Host "Cleaning up any previous background jobs..."
Get-Job | Stop-Job -ErrorAction SilentlyContinue
Get-Job | Remove-Job -ErrorAction SilentlyContinue

Write-Host "Checking for stale processes still holding required ports..."
$requiredPorts = 8000, 8001, 8002, 8003, 8004, 8005
foreach ($port in $requiredPorts) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        Write-Host "  Port $port is held by PID $($c.OwningProcess) — stopping it."
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Starting Infrastructure (Docker Compose)..."
docker compose up -d redis-aegis postgres-aegis rabbitmq-aegis
Start-Sleep -Seconds 5

Write-Host "Starting Mock Merchants..."
Start-Job { param($r) Set-Location $r; python -m uvicorn mock_merchants.merchant_a_crm:app --port 8001 } -ArgumentList $root | Out-Null
Start-Job { param($r) Set-Location $r; python -m uvicorn mock_merchants.merchant_b_hotel:app --port 8002 } -ArgumentList $root | Out-Null
Start-Job { param($r) Set-Location $r; python -m uvicorn mock_merchants.merchant_c_domain:app --port 8003 } -ArgumentList $root | Out-Null

Write-Host "Starting Services..."
Start-Job { param($r) Set-Location $r; python -m uvicorn engine.policy_service:app --port 8004 } -ArgumentList $root | Out-Null
Start-Job { param($r) Set-Location $r; python -m uvicorn engine.anomaly_service:app --port 8005 } -ArgumentList $root | Out-Null
Start-Job { param($r) Set-Location $r; python -m uvicorn proxy.mcp_proxy:app --port 8000 } -ArgumentList $root | Out-Null

Write-Host "Starting Worker..."
Start-Job { param($r) Set-Location $r; python -m compensating_agent.worker } -ArgumentList $root | Out-Null

Write-Host "Waiting for services to come up..."
Start-Sleep -Seconds 8
$failed = @()
foreach ($port in $requiredPorts) {
    $listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $listening) {
        $failed += $port
    }
}
if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "WARNING: the following ports never came up: $($failed -join ', ')"
    Write-Host "Run 'Get-Job | Receive-Job' below to see each job's actual error output."
    Write-Host ""
}

Write-Host "All services running as background jobs in THIS window."
Write-Host "Tail all output live with:  Get-Job | Receive-Job -Wait"
Write-Host "Stop everything with:       Get-Job | Stop-Job; Get-Job | Remove-Job"

Get-Job | Receive-Job -Wait