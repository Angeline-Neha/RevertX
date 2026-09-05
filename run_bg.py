import os
import subprocess
import sys

from mock_merchants.registry import MERCHANTS

# Same problem start.ps1 had: every service below used to be spawned with
# subprocess.Popen and never recorded anywhere, so there was no way to
# stop them short of hunting PIDs manually (Ctrl+C only kills this
# launcher script itself, not its detached children). Every PID now gets
# written to logs/run_bg_pids.txt — run stop_bg.py to actually shut all of
# this back down.
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = os.path.join(_REPO_ROOT, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_PID_FILE = os.path.join(_LOG_DIR, "run_bg_pids.txt")

_procs: list[tuple[int, str]] = []


def _spawn(args: list[str], name: str) -> None:
    proc = subprocess.Popen(args)
    _procs.append((proc.pid, name))


print("Starting Services...")
for merchant_id, spec in MERCHANTS.items():
    _spawn([sys.executable, "-m", "uvicorn", spec.app_target, "--port", str(spec.port)], merchant_id)
_spawn([sys.executable, "-m", "uvicorn", "engine.policy_service:app", "--port", "8004"], "policy_service")
_spawn([sys.executable, "-m", "uvicorn", "engine.anomaly_service:app", "--port", "8005"], "anomaly_service")
_spawn([sys.executable, "-m", "uvicorn", "proxy.mcp_proxy:app", "--port", "8000"], "proxy")
_spawn([sys.executable, "-m", "compensating_agent.worker"], "compensation_worker")

with open(_PID_FILE, "w", encoding="utf-8") as f:
    for pid, name in _procs:
        f.write(f"{pid}\t{name}\n")

print("All services are running in background.")
print(f"PIDs recorded in {_PID_FILE} — run `python stop_bg.py` to stop them all.")
