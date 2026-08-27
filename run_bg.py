import subprocess
import time
import sys

print("Starting Services...")
subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_merchants.merchant_a_crm:app", "--port", "8001"])
subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_merchants.merchant_b_hotel:app", "--port", "8002"])
subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_merchants.merchant_c_domain:app", "--port", "8003"])
subprocess.Popen([sys.executable, "-m", "uvicorn", "engine.policy_service:app", "--port", "8004"])
subprocess.Popen([sys.executable, "-m", "uvicorn", "engine.anomaly_service:app", "--port", "8005"])
subprocess.Popen([sys.executable, "-m", "uvicorn", "proxy.mcp_proxy:app", "--port", "8000"])
subprocess.Popen([sys.executable, "-m", "compensating_agent.worker"])

print("All services are running in background.")
