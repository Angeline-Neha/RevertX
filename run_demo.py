import subprocess
import time
import sys

print("Starting Infrastructure...")
subprocess.run(["docker", "compose", "up", "-d", "redis-aegis", "postgres-aegis", "rabbitmq-aegis"])
time.sleep(5)

procs = []
print("Starting Merchants...")
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_merchants.merchant_a_crm:app", "--port", "8001"]))
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_merchants.merchant_b_hotel:app", "--port", "8002"]))
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_merchants.merchant_c_domain:app", "--port", "8003"]))

print("Starting Services...")
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "engine.policy_service:app", "--port", "8004"]))
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "proxy.mcp_proxy:app", "--port", "8000"]))
procs.append(subprocess.Popen([sys.executable, "-m", "compensating_agent.worker"]))

print("Waiting for warm-up (10 seconds)...")
time.sleep(10)

print("\n--- RUNNING DEMO CLIENT ---\n")
result = subprocess.run([sys.executable, "-m", "primary_agent.procurement_agent"])

print("\n--- DEMO FINISHED ---")

for p in procs:
    p.terminate()
