# stop.ps1 — Aegis full stack shutdown
# Run from the repo root in PowerShell, after start.ps1.
#
# start.ps1 previously told you to run:
#   Get-Process python | Stop-Job; docker compose down
# That's broken: Stop-Job only accepts PowerShell *job* objects (from
# Start-Job). Every service here is launched with Start-Process, which
# returns real OS Process objects, so Stop-Job silently did nothing —
# every uvicorn server, the compensation worker, and the dashboard kept
# running as orphans after "stopping" everything, which is why a process
# would seem to just get stuck around instead of actually exiting.
#
# This script instead reads back the PIDs start.ps1 recorded in
# logs\pids.txt and kills each one directly — using `taskkill /T /F` so
# the two processes started as visible PowerShell windows (the
# compensation worker, the dashboard) also take their child python/node
# process down with them, instead of just closing an empty window.

$dir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $dir -or -not (Test-Path $dir)) { $dir = $PWD.Path }
Set-Location $dir

$pidFile = "$dir\logs\pids.txt"

if (-not (Test-Path $pidFile)) {
    Write-Host "[Aegis] No logs\pids.txt found — nothing tracked from start.ps1 to stop." -ForegroundColor Yellow
    Write-Host "[Aegis] Still bringing down Redis..." -ForegroundColor Cyan
    docker compose down
    exit
}

Write-Host "[Aegis] Stopping tracked processes..." -ForegroundColor Cyan

Get-Content $pidFile | ForEach-Object {
    if (-not $_.Trim()) { return }
    $parts = $_ -split "`t"
    $procId = $parts[0]
    $name = if ($parts.Count -gt 1) { $parts[1] } else { "unknown" }

    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
        # /T kills the whole process tree (so e.g. the powershell window
        # running `python -m compensating_agent.worker` also takes that
        # python process down), /F forces it, matching what Ctrl+C in each
        # window would eventually do but reliably and without needing 9
        # separate windows closed by hand.
        taskkill /PID $procId /T /F 2>&1 | Out-Null
        Write-Host "  Stopped $name (PID $procId)" -ForegroundColor DarkGray
    } else {
        Write-Host "  $name (PID $procId) already gone" -ForegroundColor DarkGray
    }
}

Remove-Item -Force -ErrorAction SilentlyContinue $pidFile

Write-Host "[Aegis] Stopping Redis..." -ForegroundColor Cyan
docker compose down

Write-Host ""
Write-Host "[Aegis] Stopped." -ForegroundColor Green
Write-Host ""
Write-Host "[Aegis] If anything still looks stuck (rare — a hung network call" -ForegroundColor DarkGray
Write-Host "  inside a service can occasionally survive taskkill /F), check:" -ForegroundColor DarkGray
Write-Host "  Get-Process python,node -ErrorAction SilentlyContinue | Format-Table Id,ProcessName,StartTime" -ForegroundColor DarkGray
