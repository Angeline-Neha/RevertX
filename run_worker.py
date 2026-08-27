"""
run_worker.py
Launcher for compensating_agent.worker — equivalent to
`python -m compensating_agent.worker`, kept as a convenience entrypoint.

Use --debug for verbose logging (previously a separate run_worker_debug.py
script; folded in here so there's one worker launcher, not three near-
identical ones).
"""
import argparse
import asyncio
import logging

from compensating_agent.worker import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Enable DEBUG-level logging")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, force=True)

    try:
        asyncio.run(main())
    except Exception as exc:
        if args.debug:
            print(f"CRASH: {exc}")
        raise
