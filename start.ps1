# start.ps1 — Aegis full stack launcher
# Run from D:\RevertX in PowerShell.
# Starts all services as background daemons in this session.

$dir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $dir -or -not (Test-Path $dir)) { $dir = $PWD.Path }
Set-Location $dir

$py = (Get-Command python -ErrorAction Stop).Source

Write-Host ""
Write-Host "[Aegis] Starting Redis on port 6380..." -ForegroundColor Cyan
docker compose up -d 2>&1 | Where-Object { $_ -notmatch "obsolete|What's next|Filter|dashboard" } | Write-Host
Start-Sleep -Seconds 2

Write-Host "[Aegis] Starting services..." -ForegroundColor Cyan

# Start each uvicorn as a daemon background task (IsDaemon=true equivalent via nohup-style)
function Start-Service($module, $port, $name) {
    Start-Process -FilePath $py `
        -ArgumentList "-m uvicorn $module --port $port --log-level warning" `
        -WorkingDirectory $dir `
        -WindowStyle Hidden `
        -RedirectStandardOutput "$dir\logs\$name.log" `
        -RedirectStandardError  "$dir\logs\$name.err"
    Write-Host "  Started $name on :$port (log: logs\$name.log)" -ForegroundColor DarkGray
}

New-Item -ItemType Directory -Force -Path "$dir\logs" | Out-Null

Start-Service "mock_merchants.merchant_a_crm:app"   8001 "merchant_a"
Start-Service "mock_merchants.merchant_b_hotel:app"  8002 "merchant_b"
Start-Service "mock_merchants.merchant_c_domain:app" 8003 "merchant_c"
Start-Sleep -Seconds 1
Start-Service "proxy.mcp_proxy:app"                  8000 "proxy"
Start-Sleep -Seconds 4

# Health check
Write-Host ""
$allOk = $true
@(8000,8001,8002,8003) | ForEach-Object {
    $port = $_
    try {
        $null = Invoke-WebRequest "http://localhost:$port/docs" -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop
        Write-Host "  [OK] :$port" -ForegroundColor Green
    } catch {
        Write-Host "  [!!] :$port not ready - check logs\$port.err" -ForegroundColor Red
        $allOk = $false
    }
}

# Start dashboard in a separate visible window so you can see Vite output
Start-Process powershell -ArgumentList "-NoExit -Command `"Set-Location '$dir\dashboard'; npm run dev`""

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
Write-Host "[Aegis] Run the demo:" -ForegroundColor Cyan
Write-Host "  python -X utf8 primary_agent/procurement_agent.py" -ForegroundColor Yellow
Write-Host ""
Write-Host "[Aegis] Stop everything:" -ForegroundColor DarkGray
Write-Host "  Get-Process python | Stop-Job; docker compose down" -ForegroundColor DarkGray