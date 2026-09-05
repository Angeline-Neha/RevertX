"""
stop_bg.py
Counterpart to run_bg.py, which previously had no way to stop what it
started — every service was spawned with a bare subprocess.Popen() and
never recorded anywhere, so a run_bg.py session could only be killed by
manually hunting down each PID. Reads back logs/run_bg_pids.txt (written
by the current run_bg.py) and terminates each one.
"""
from __future__ import annotations

import os
import signal
import sys

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_PID_FILE = os.path.join(_REPO_ROOT, "logs", "run_bg_pids.txt")


def _kill(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            import subprocess
            # /T kills the process tree (uvicorn workers can spawn their
            # own children), /F forces it.
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, OSError):
        return False


def main() -> None:
    if not os.path.exists(_PID_FILE):
        print(f"No {_PID_FILE} found — nothing to stop (or it was already cleaned up).")
        return

    with open(_PID_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    for line in lines:
        pid_str, _, name = line.partition("\t")
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        ok = _kill(pid)
        print(f"  {'Stopped' if ok else 'Already gone'}: {name or 'unknown'} (PID {pid})")

    os.remove(_PID_FILE)
    print("Done.")


if __name__ == "__main__":
    main()
