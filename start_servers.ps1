Write-Host "Starting Infrastructure (Docker Compose)..."
docker compose up -d redis-aegis postgres-aegis rabbitmq-aegis
Start-Sleep -Seconds 5

Write-Host "Starting Mock Merchants..."
Start-Process python -ArgumentList "-m uvicorn mock_merchants.merchant_a_crm:app --port 8001"
Start-Process python -ArgumentList "-m uvicorn mock_merchants.merchant_b_hotel:app --port 8002"
Start-Process python -ArgumentList "-m uvicorn mock_merchants.merchant_c_domain:app --port 8003"

Write-Host "Starting Services..."
Start-Process python -ArgumentList "-m uvicorn engine.policy_service:app --port 8004"
Start-Process python -ArgumentList "-m uvicorn proxy.mcp_proxy:app --port 8000"

Write-Host "Starting Worker..."
Start-Process python -ArgumentList "-m compensating_agent.worker"

Write-Host "All services started in separate windows! You can now run the procurement agent."
