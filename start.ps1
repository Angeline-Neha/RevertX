# start.ps1 - Aegis full stack launcher
# Run from D:\RevertX in PowerShell.
# Starts all services as background daemons in this session.

$dir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $dir -or -not (Test-Path $dir)) { $dir = $PWD.Path }
Set-Location $dir

$py = (Get-Command python -ErrorAction Stop).Source

Write-Host ""
Write-Host "[Aegis] Starting Redis on port 6380..." -ForegroundColor Cyan
docker compose up -d redis-aegis postgres-aegis rabbitmq-aegis 2>&1 | Where-Object { $_ -notmatch "obsolete|What's next|Filter|dashboard" } | Write-Host
Start-Sleep -Seconds 2

Write-Host "[Aegis] Starting services..." -ForegroundColor Cyan

# PID tracking - every process this script starts (servers, worker,
# dashboard) gets its PID appended here. stop.ps1 reads this file back to
# know exactly what to kill. Without this, "stop everything" had no way to
# find these processes at all: they're started via Start-Process (real OS
# processes), not Start-Job, so `Get-Process python | Stop-Job` (the old
# advice printed at the bottom of this script) silently did nothing -
# Stop-Job only accepts job objects, not process objects - leaving every
# uvicorn server, the worker, and the dashboard running as orphans after
# every single demo session.
$pidFile = "$dir\logs\pids.txt"
New-Item -ItemType Directory -Force -Path "$dir\logs" | Out-Null
Remove-Item -Force -ErrorAction SilentlyContinue $pidFile

function Save-Pid($procId, $name) {
    Add-Content -Path $pidFile -Value "$procId`t$name"
}

# Start each uvicorn as a daemon background task (IsDaemon=true equivalent via nohup-style)
function Start-Service($module, $port, $name) {
    $proc = Start-Process -FilePath $py `
        -ArgumentList "-m uvicorn $module --port $port --log-level warning" `
        -WorkingDirectory $dir `
        -WindowStyle Hidden `
        -RedirectStandardOutput "$dir\logs\$name.log" `
        -RedirectStandardError  "$dir\logs\$name.err" `
        -PassThru
    Save-Pid $proc.Id $name
    Write-Host "  Started $name on :$port (log: logs\$name.log)" -ForegroundColor DarkGray
}

Start-Service "mock_merchants.merchant_a_crm:app"     8001 "merchant_a"
Start-Service "mock_merchants.merchant_b_hotel:app"   8002 "merchant_b"
Start-Service "mock_merchants.merchant_c_domain:app"  8003 "merchant_c"
Start-Service "engine.policy_service:app"             8004 "policy_service"
# Phase 6 merchants - partial-penalty, flaky, and event-launch vendor set
Start-Service "mock_merchants.merchant_d_flexstay:app" 8006 "merchant_d"
Start-Service "mock_merchants.merchant_e_flaky:app"    8007 "merchant_e"
Start-Service "mock_merchants.merchant_f_venue:app"    8008 "merchant_f"
Start-Service "mock_merchants.merchant_g_catering:app" 8009 "merchant_g"
Start-Sleep -Seconds 1
Start-Service "proxy.mcp_proxy:app"                    8000 "proxy"
Start-Sleep -Seconds 4

# Start compensation worker hidden in the background (logs go to logs\worker.log)
$workerProc = Start-Process -FilePath $py `
    -ArgumentList "-X utf8 -m compensating_agent.worker" `
    -WorkingDirectory $dir `
    -WindowStyle Hidden `
    -RedirectStandardOutput "$dir\logs\worker.log" `
    -RedirectStandardError  "$dir\logs\worker.err" `
    -PassThru
Save-Pid $workerProc.Id "compensation_worker"
Write-Host "  Started compensation worker (log: logs\worker.log)" -ForegroundColor DarkGray

# Health check - all services
Write-Host ""
$allOk = $true
@(8000,8001,8002,8003,8004,8006,8007,8008,8009) | ForEach-Object {
    $port = $_
    try {
        $null = Invoke-WebRequest "http://localhost:$port/docs" -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop
        Write-Host "  [OK] :$port" -ForegroundColor Green
    } catch {
        Write-Host "  [!!] :$port not ready - check logs\$port.err" -ForegroundColor Red
        $allOk = $false
    }
}

# Start dashboard hidden in the background (logs go to logs\dashboard.log)
# npm on Windows is npm.cmd, a shell script - Start-Process can't exec that
# directly (fails with "%1 is not a valid Win32 application"), so route it
# through cmd.exe /c the way a normal shell invocation would.
$dashProc = Start-Process -FilePath "$env:WINDIR\System32\cmd.exe" `
    -ArgumentList "/c npm run dev" `
    -WorkingDirectory "$dir\dashboard" `
    -WindowStyle Hidden `
    -RedirectStandardOutput "$dir\logs\dashboard.log" `
    -RedirectStandardError  "$dir\logs\dashboard.err" `
    -PassThru
Save-Pid $dashProc.Id "dashboard"
Write-Host "  Started dashboard (log: logs\dashboard.log)" -ForegroundColor DarkGray

Write-Host ""
if ($allOk) {
    Write-Host "[Aegis] All services ready!" -ForegroundColor Green
} else {
    Write-Host "[Aegis] Some services failed. Check logs\ directory." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  Dashboard : http://localhost:5173" -ForegroundColor White
Write-Host "  Proxy API : http://localhost:8000/docs" -ForegroundColor White
Write-Host ""
Write-Host "[Aegis] Stop everything:" -ForegroundColor DarkGray
Write-Host "  .\stop.ps1" -ForegroundColor Yellow