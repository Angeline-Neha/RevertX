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

# Force UTF-8 output so the Rs. symbol prints correctly on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx

PROXY_URL = "http://localhost:8000"


async def run_procurement(workflow_id: str | None = None) -> None:
    wid = workflow_id or str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=30.0) as client:

        # ------------------------------------------------------------------
        # 0. Declare workflow with a ₹35,000 budget
        # ------------------------------------------------------------------
        print(f"\n[Aegis Demo] Starting workflow {wid}")
        print(f"[Aegis Demo] Goal: Book a CRM license + Hotel for offsite | Budget: ₹35,000\n")

        await client.post(f"{PROXY_URL}/init_workflow", json={
            "workflow_id": wid,
            "budget_limit": 35000.0,
        })

        # ------------------------------------------------------------------
        # 1. Pay Merchant A — CRM license (₹10,000)
        # ------------------------------------------------------------------
        print("[Primary Agent] Paying Merchant A (CRM license) — ₹10,000 ...")
        resp = await client.post(f"{PROXY_URL}/pay", json={
            "workflow_id": wid,
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
        resp = await client.post(f"{PROXY_URL}/pay", json={
            "workflow_id": wid,
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
        resp = await client.post(f"{PROXY_URL}/pay", json={
            "workflow_id": wid,
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


if __name__ == "__main__":
    wid = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(run_procurement(wid))
