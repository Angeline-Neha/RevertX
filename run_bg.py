import subprocess
import time
import sys

from mock_merchants.registry import MERCHANTS

print("Starting Services...")
for merchant_id, spec in MERCHANTS.items():
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", spec.app_target, "--port", str(spec.port)]
    )
subprocess.Popen([sys.executable, "-m", "uvicorn", "engine.policy_service:app", "--port", "8004"])
subprocess.Popen([sys.executable, "-m", "uvicorn", "engine.anomaly_service:app", "--port", "8005"])
subprocess.Popen([sys.executable, "-m", "uvicorn", "proxy.mcp_proxy:app", "--port", "8000"])
subprocess.Popen([sys.executable, "-m", "compensating_agent.worker"])

print("All services are running in background.")
