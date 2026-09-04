# -*- coding: utf-8 -*-
"""
primary_agent/procurement_agent.py
Scripted procurement sequence for the demo (Section 10).
Goal: "Book a CRM license and a hotel for an offsite. Budget Rs.35,000."

This agent intentionally does NOT have its own LLM reasoning loop — the spec
explicitly says a fixed sequence is correct here; free-form planning is out of scope.
The agent only calls the proxy.  It never knows Aegis exists.
"""
from __future__ import annotations

import asyncio
import io
import sys
import uuid
import os
from dotenv import load_dotenv

load_dotenv()

# Force UTF-8 output so the Rs. symbol prints correctly on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx

# db/client.py lives at the repo root. Running this file directly
# (`python primary_agent/procurement_agent.py ...`) only puts primary_agent/
# on sys.path, not the repo root, so `import db` fails with
# ModuleNotFoundError even though `python -m primary_agent.procurement_agent`
# (what run_demo.py uses) works fine. Insert the repo root explicitly so
# both invocation styles work the same way.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from db.client import reset_workflow
from primary_agent.planner import plan_procurement

PROXY_URL = "http://localhost:8000"
# PROXY_API_KEY is the canonical variable name — it's what proxy/mcp_proxy.py
# itself reads. API_KEY is kept only as a fallback for anyone with an older
# .env; if both are unset, this still matches the proxy's own default so the
# demo works out of the box. See .env.example for the full explanation.
API_KEY = os.getenv("PROXY_API_KEY") or os.getenv("API_KEY", "test-key-123")
HEADERS = {"X-API-Key": API_KEY}


async def run_procurement(workflow_id: str | None = None) -> None:
    wid = workflow_id or str(uuid.uuid4())

    # A workflow_id is only ever passed in here explicitly (via CLI arg —
    # see __main__ below) when a demo operator wants to keep re-running
    # against the same dashboard URL instead of pasting a new UUID each
    # time. create_workflow()'s INSERT ... ON CONFLICT DO NOTHING means a
    # reused id's budget_used otherwise carries over silently from the
    # previous run, so every payment in the new run gets rejected as
    # MANDATE EXCEEDED against stale leftover budget. Server-generated ids
    # (the `workflow_id is None` branch above) never hit this — they're
    # fresh every time, so there is nothing to reset.
    if workflow_id is not None:
        print(f"[Aegis Demo] Reusing workflow_id {wid} — resetting its budget/step state before running.")
        await reset_workflow(wid)

    async with httpx.AsyncClient(timeout=30.0) as client:

        # ------------------------------------------------------------------
        # 0. Declare workflow with a ₹35,000 budget
        # ------------------------------------------------------------------
        print(f"\n[Aegis Demo] Starting workflow {wid}")
        print(f"[Aegis Demo] Goal: Book a CRM license + Hotel for offsite | Budget: ₹35,000\n")

        await client.post(f"{PROXY_URL}/init_workflow", headers=HEADERS, json={
            "workflow_id": wid,
            "budget_limit": 35000.0,
        })

        # ------------------------------------------------------------------
        # 1. Pay Merchant A — CRM license (₹10,000)
        # ------------------------------------------------------------------
        print("[Primary Agent] Paying Merchant A (CRM license) — ₹10,000 ...")
        resp = await client.post(f"{PROXY_URL}/pay", headers=HEADERS, json={
            "workflow_id": wid,
            "idempotency_key": str(uuid.uuid4()),
            "merchant_id": "merchant_a",
            "expected": {
                "amount": 10000.0,
                "currency": "INR",
                "payee": "CRM Corp",
                "item": "CRM license (annual)",
            },
        })
        data = resp.json()
        recon = data.get("reconciliation", {})
        budget = data.get("budget", {})
        print(f"  → Settlement: {data.get('merchant_response', {}).get('settlement_ref')}")
        print(f"  → Reconciliation: {'✓ MATCH' if recon.get('match') else '✗ MISMATCH: ' + str(recon.get('mismatch_type'))}")
        print(f"  → Budget used: ₹{budget.get('used', 0):,.0f} / ₹{budget.get('limit', 0):,.0f}\n")

        # ------------------------------------------------------------------
        # 2. Pay Merchant B — Hotel (₹20,000)
        # ------------------------------------------------------------------
        print("[Primary Agent] Paying Merchant B (Hotel booking) — ₹20,000 ...")
        resp = await client.post(f"{PROXY_URL}/pay", headers=HEADERS, json={
            "workflow_id": wid,
            "idempotency_key": str(uuid.uuid4()),
            "merchant_id": "merchant_b",
            "expected": {
                "amount": 20000.0,
                "currency": "INR",
                "payee": "Grand Hotel",
                "item": "Hotel booking (2 nights)",
            },
        })
        data = resp.json()
        recon = data.get("reconciliation", {})
        budget = data.get("budget", {})
        print(f"  → Settlement: {data.get('merchant_response', {}).get('settlement_ref')}")
        print(f"  → Reconciliation: {'✓ MATCH' if recon.get('match') else '✗ MISMATCH: ' + str(recon.get('mismatch_type'))}")
        print(f"  → Budget used: ₹{budget.get('used', 0):,.0f} / ₹{budget.get('limit', 0):,.0f}\n")

        # ------------------------------------------------------------------
        # 3. Attempt third payment — Flights (₹12,000) → will exceed budget
        #    ₹10,000 + ₹20,000 + ₹12,000 = ₹42,000 > ₹35,000 limit
        # ------------------------------------------------------------------
        print("[Primary Agent] Attempting Merchant C (Domain/Flights) — ₹12,000 ...")
        print("[Primary Agent] (This will push total to ₹42,000 — budget limit is ₹35,000)")
        resp = await client.post(f"{PROXY_URL}/pay", headers=HEADERS, json={
            "workflow_id": wid,
            "idempotency_key": str(uuid.uuid4()),
            "merchant_id": "merchant_c",
            "expected": {
                "amount": 12000.0,
                "currency": "INR",
                "payee": "Domain Registrar",
                "item": "Domain registration + hosting",
            },
        })

        if resp.status_code == 403:
            error = resp.json()
            print(f"\n  ✗ HARD FAILURE: {error.get('error_type')}")
            print(f"    {error.get('detail')}")
            print("\n[Primary Agent] CRASHED — ₹30,000 is now stranded in uncoordinated bookings.")
            print("[Aegis]         Compensating agent triggered automatically. Watch the dashboard.\n")
        else:
            data = resp.json()
            print(f"  → Unexpected success: {data}")

        print(f"\n[Aegis Demo] Workflow ID: {wid}")
        print(f"[Aegis Demo] Connect the dashboard to ws://localhost:8000/ws/{wid} to see live events.")


async def _pay(client: httpx.AsyncClient, wid: str, merchant_id: str, amount: float, payee: str, item: str) -> dict:
    """Shared /pay call + console reporting, used by both the fixed script
    above and the goal-driven planner path below — Phase 7.3's point that
    a real planning agent needs zero changes to the proxy call itself,
    only to what decides the merchant/amount/item ahead of time."""
    print(f"[Primary Agent] Paying {merchant_id} ({payee}) — ₹{amount:,.0f} ({item}) ...")
    resp = await client.post(f"{PROXY_URL}/pay", headers=HEADERS, json={
        "workflow_id": wid,
        "idempotency_key": str(uuid.uuid4()),
        "merchant_id": merchant_id,
        "expected": {
            "amount": amount,
            "currency": "INR",
            "payee": payee,
            "item": item,
        },
    })

    if resp.status_code == 403:
        error = resp.json()
        print(f"\n  ✗ HARD FAILURE: {error.get('error_type')}")
        print(f"    {error.get('detail')}")
        return {"status_code": 403, "error": error}

    data = resp.json()
    recon = data.get("reconciliation", {})
    budget = data.get("budget", {})
    print(f"  → Settlement: {data.get('merchant_response', {}).get('settlement_ref')}")
    print(f"  → Reconciliation: {'✓ MATCH' if recon.get('match') else '✗ MISMATCH: ' + str(recon.get('mismatch_type'))}")
    print(f"  → Budget used: ₹{budget.get('used', 0):,.0f} / ₹{budget.get('limit', 0):,.0f}\n")
    return {"status_code": resp.status_code, "data": data}


async def run_procurement_with_plan(goal: str, budget_limit: float, workflow_id: str | None = None) -> None:
    """
    Phase 7 — goal-driven replacement for the fixed script above.
    Calls primary_agent/planner.py to turn a plain-English goal + budget
    into an ordered payment list, then feeds that list into the exact same
    /pay proxy calls run_procurement() already makes (via the shared _pay
    helper above) — no changes to the proxy, budget tracking, or Aegis
    compensation, matching Phase 7.3.
    """
    wid = workflow_id or str(uuid.uuid4())

    if workflow_id is not None:
        print(f"[Aegis Demo] Reusing workflow_id {wid} — resetting its budget/step state before running.")
        await reset_workflow(wid)

    from primary_agent.catalog import CATALOG_BY_ID

    print(f"\n[Aegis Demo] Starting workflow {wid}")
    print(f"[Aegis Demo] Goal: {goal} | Budget: ₹{budget_limit:,.0f}\n")

    print("[Primary Agent] Asking the planner for a payment sequence...")
    plan = await plan_procurement(goal, budget_limit)
    if not plan:
        print("[Primary Agent] Planner produced no usable line items — nothing to pay.")
        return
    print(f"[Primary Agent] Plan: {len(plan)} line item(s):")
    for p in plan:
        print(f"    - {p.merchant_id} ({CATALOG_BY_ID[p.merchant_id].payee}): ₹{p.amount:,.0f} — {p.item}")
    print()

    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.post(f"{PROXY_URL}/init_workflow", headers=HEADERS, json={
            "workflow_id": wid,
            "budget_limit": budget_limit,
        })

        for p in plan:
            payee = CATALOG_BY_ID[p.merchant_id].payee
            result = await _pay(client, wid, p.merchant_id, p.amount, payee, p.item)
            if result["status_code"] == 403:
                print("\n[Primary Agent] CRASHED — earlier payments are now stranded.")
                print("[Aegis]         Compensating agent triggered automatically. Watch the dashboard.\n")
                break

        print(f"\n[Aegis Demo] Workflow ID: {wid}")
        print(f"[Aegis Demo] Connect the dashboard to ws://localhost:8000/ws/{wid} to see live events.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the RevertX/Aegis demo procurement agent.")
    parser.add_argument("workflow_id", nargs="?", default=None, help="Reuse an existing workflow_id (resets its state first).")
    parser.add_argument("--goal", default=None, help="Plain-English goal — runs the Phase 7 planner instead of the fixed demo script.")
    parser.add_argument("--budget", type=float, default=35000.0, help="Budget limit for --goal runs (default ₹35,000).")
    args = parser.parse_args()

    if args.goal:
        asyncio.run(run_procurement_with_plan(args.goal, args.budget, args.workflow_id))
    else:
        asyncio.run(run_procurement(args.workflow_id))


