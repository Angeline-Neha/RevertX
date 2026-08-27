import subprocess
import time
import sys
import uuid

print("Starting Services...")
procs = []
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_merchants.merchant_a_crm:app", "--port", "8001"]))
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_merchants.merchant_b_hotel:app", "--port", "8002"]))
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_merchants.merchant_c_domain:app", "--port", "8003"]))
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "engine.policy_service:app", "--port", "8004"]))
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "engine.anomaly_service:app", "--port", "8005"]))
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "proxy.mcp_proxy:app", "--port", "8000"]))
worker_proc = subprocess.Popen([sys.executable, "run_worker_debug.py"], stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
procs.append(worker_proc)

time.sleep(5)
print("Running demo...")
subprocess.run([sys.executable, "-m", "primary_agent.procurement_agent"])

print("Waiting for worker to process (15 seconds)...")
time.sleep(15)

print("Worker stderr:")
worker_proc.terminate()
stdout, stderr = worker_proc.communicate()
print(stderr)
print(stdout)

for p in procs:
    p.terminate()
