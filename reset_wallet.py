"""
reset_wallet.py
One-off demo-day utility: resets today's Agent Wallet spend to 0 and
optionally raises the daily cap, so repeated rehearsal runs don't get
blocked by check_wallet_authority (which only resets spent_today on a
calendar-date change — see authorization/wallet.py).

Usage (from the RevertX repo root, same venv as the rest of the project):
    python reset_wallet.py
    python reset_wallet.py --daily-limit 500000
    python reset_wallet.py --agent-id primary_agent --daily-limit 500000 --per-txn-limit 50000

Reads PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DB from .env, same as db/client.py,
so it always points at the same database the proxy itself uses.
"""
from __future__ import annotations

import argparse
import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

PG_USER = os.getenv("PG_USER", "aegis")
PG_PASSWORD = os.getenv("PG_PASSWORD", "aegispassword")
PG_DB = os.getenv("PG_DB", "aegis")
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5433")


def main():
    parser = argparse.ArgumentParser(description="Reset Agent Wallet daily spend (and optionally limits).")
    parser.add_argument("--agent-id", default="primary_agent")
    parser.add_argument("--daily-limit", type=float, default=None, help="If set, also raises daily_limit to this value.")
    parser.add_argument("--per-txn-limit", type=float, default=None, help="If set, also raises per_txn_limit to this value.")
    args = parser.parse_args()

    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD, dbname=PG_DB)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_wallet SET spent_today = 0.0, last_reset = CURRENT_DATE WHERE agent_id = %s",
                (args.agent_id,),
            )
            print(f"spent_today reset to 0 for agent_id={args.agent_id} ({cur.rowcount} row(s) updated)")

            if args.daily_limit is not None:
                cur.execute(
                    "UPDATE agent_wallet SET daily_limit = %s WHERE agent_id = %s",
                    (args.daily_limit, args.agent_id),
                )
                print(f"daily_limit set to {args.daily_limit}")

            if args.per_txn_limit is not None:
                cur.execute(
                    "UPDATE agent_wallet SET per_txn_limit = %s WHERE agent_id = %s",
                    (args.per_txn_limit, args.agent_id),
                )
                print(f"per_txn_limit set to {args.per_txn_limit}")

            cur.execute(
                "SELECT agent_id, per_txn_limit, daily_limit, spent_today, last_reset FROM agent_wallet WHERE agent_id = %s",
                (args.agent_id,),
            )
            row = cur.fetchone()
            print("Current wallet state:", row)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
