import os
import socket
import subprocess
import sys
import time

from mock_merchants.registry import MERCHANTS

REQUIRED_PORTS = {
    "Redis (redis-aegis)": ("localhost", int(os.getenv("REDIS_PORT", "6380"))),
    "Postgres (postgres-aegis)": ("localhost", int(os.getenv("PG_PORT", "5433"))),
    "RabbitMQ (rabbitmq-aegis)": ("localhost", 5673),
}


def _wait_for_port(name: str, host: str, port: int, timeout_seconds: int = 30) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def check_infra_or_exit() -> None:
    """
    Cheap insurance against the "9 moving parts, any one of which can be
    the reason it fails" risk: fail fast with a clear message here rather
    than letting the demo run 15+ seconds deep and then have /pay throw an
    opaque connection-refused exception mid-run.
    """
    print("Checking infrastructure is reachable...")
    failures = []
    for name, (host, port) in REQUIRED_PORTS.items():
        if _wait_for_port(name, host, port):
            print(f"  [ok] {name} ({host}:{port})")
        else:
            print(f"  [FAIL] {name} ({host}:{port}) — not reachable after 30s")
            failures.append(name)

    if failures:
        print(
            f"\nAborting: {', '.join(failures)} not reachable. "
            f"Check `docker compose ps` and container logs "
            f"(`docker compose logs <service>`) before retrying."
        )
        sys.exit(1)


print("Starting Infrastructure...")
subprocess.run(["docker", "compose", "up", "-d", "redis-aegis", "postgres-aegis", "rabbitmq-aegis"])
check_infra_or_exit()

procs = []
print("Starting Merchants...")
for merchant_id, spec in MERCHANTS.items():
    procs.append(subprocess.Popen(
        [sys.executable, "-m", "uvicorn", spec.app_target, "--port", str(spec.port)]
    ))

print("Starting Services...")
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "engine.policy_service:app", "--port", "8004"]))
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "engine.anomaly_service:app", "--port", "8005"]))
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "proxy.mcp_proxy:app", "--port", "8000"]))
procs.append(subprocess.Popen([sys.executable, "-m", "compensating_agent.worker"]))

APP_PORTS = {
    f"{merchant_id} ({spec.payee})": ("localhost", spec.port)
    for merchant_id, spec in MERCHANTS.items()
}
APP_PORTS.update({
    "Policy extractor service": ("localhost", 8004),
    "Anomaly detector service": ("localhost", 8005),
    "Aegis MCP Proxy": ("localhost", 8000),
})


def check_app_ports_or_exit() -> None:
    """Same fail-fast principle as check_infra_or_exit(), for the Python-side
    processes started above — a merchant or the proxy failing to bind (e.g.
    a stale process already holding the port) should stop the demo here,
    not surface as a mysterious /pay failure two steps later."""
    print("Checking application services are reachable...")
    failures = []
    for name, (host, port) in APP_PORTS.items():
        if _wait_for_port(name, host, port, timeout_seconds=15):
            print(f"  [ok] {name} ({host}:{port})")
        else:
            print(f"  [FAIL] {name} ({host}:{port}) — did not come up in time")
            failures.append(name)

    if failures:
        print(f"\nAborting: {', '.join(failures)} never became reachable.")
        for p in procs:
            p.terminate()
        sys.exit(1)


print("Waiting for warm-up (10 seconds)...")
time.sleep(10)
check_app_ports_or_exit()

print("\n--- RUNNING DEMO CLIENT ---\n")
result = subprocess.run([sys.executable, "-m", "primary_agent.procurement_agent"])

print("\n--- DEMO FINISHED ---")

for p in procs:
    p.terminate()
