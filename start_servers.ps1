$root = $PSScriptRoot

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

Write-Host "All services running as background jobs in THIS window."
Write-Host "Tail all output live with:  Get-Job | Receive-Job -Wait"
Write-Host "Stop everything with:       Get-Job | Stop-Job; Get-Job | Remove-Job"

Get-Job | Receive-Job -Wait