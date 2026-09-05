$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dashboard = Join-Path $root "dashboard"
$dashboardPort = 5173

function Stop-PortProcess([int]$port) {
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($connection in $connections) {
        $pid = $connection.OwningProcess
        if ($pid -and $pid -ne $PID) {
            Write-Host "Stopping stale process $pid on port $port..."
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not (Test-Path $dashboard)) {
    throw "Dashboard directory not found: $dashboard"
}

Write-Host "Releasing dashboard port $dashboardPort..."
Stop-PortProcess $dashboardPort
Start-Sleep -Milliseconds 500

try {
    $backend = Invoke-WebRequest "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 3
    Write-Host "Backend proxy is reachable at http://localhost:8000."
} catch {
    Write-Warning "Backend proxy is not reachable at http://localhost:8000. Run .\start_servers.ps1 first."
}

Set-Location $dashboard
Write-Host "Starting RevertX dashboard at http://localhost:$dashboardPort ..."
npm run dev -- --host 0.0.0.0 --port $dashboardPort

Write-Host "Dashboard stopped. Cleaning up port $dashboardPort..."
Stop-PortProcess $dashboardPort
