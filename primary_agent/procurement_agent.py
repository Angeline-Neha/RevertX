# -*- coding: utf-8 -*-
"""
primary_agent/procurement_agent.py
Scripted procurement sequence for the demo (Section 10).
Goal: "Book a CRM license and a hotel for an offsite. Budget Rs.35,000."

This agent intentionally does NOT have its own LLM reasoning loop â€” the spec
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

PROXY_URL = "http://localhost:8000"
# PROXY_API_KEY is the canonical variable name — it's what proxy/mcp_proxy.py
# itself reads. API_KEY is kept only as a fallback for anyone with an older
# .env; if both are unset, this still matches the proxy's own default so the
# demo works out of the box. See .env.example for the full explanation.
API_KEY = os.getenv("PROXY_API_KEY") or os.getenv("API_KEY", "test-key-123")
HEADERS = {"X-API-Key": API_KEY}


async def run_procurement(workflow_id: str | None = None) -> None:
    wid = workflow_id or str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=30.0) as client:

        # ------------------------------------------------------------------
        # 0. Declare workflow with a â‚¹35,000 budget
        # ------------------------------------------------------------------
        print(f"\n[Aegis Demo] Starting workflow {wid}")
        print(f"[Aegis Demo] Goal: Book a CRM license + Hotel for offsite | Budget: â‚¹35,000\n")

        await client.post(f"{PROXY_URL}/init_workflow", headers=HEADERS, json={
            "workflow_id": wid,
            "budget_limit": 35000.0,
        })

        # ------------------------------------------------------------------
        # 1. Pay Merchant A â€” CRM license (â‚¹10,000)
        # ------------------------------------------------------------------
        print("[Primary Agent] Paying Merchant A (CRM license) â€” â‚¹10,000 ...")
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
        print(f"  â†’ Settlement: {data.get('merchant_response', {}).get('settlement_ref')}")
        print(f"  â†’ Reconciliation: {'âœ“ MATCH' if recon.get('match') else 'âœ— MISMATCH: ' + str(recon.get('mismatch_type'))}")
        print(f"  â†’ Budget used: â‚¹{budget.get('used', 0):,.0f} / â‚¹{budget.get('limit', 0):,.0f}\n")

        # ------------------------------------------------------------------
        # 2. Pay Merchant B â€” Hotel (â‚¹20,000)
        # ------------------------------------------------------------------
        print("[Primary Agent] Paying Merchant B (Hotel booking) â€” â‚¹20,000 ...")
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
        print(f"  â†’ Settlement: {data.get('merchant_response', {}).get('settlement_ref')}")
        print(f"  â†’ Reconciliation: {'âœ“ MATCH' if recon.get('match') else 'âœ— MISMATCH: ' + str(recon.get('mismatch_type'))}")
        print(f"  â†’ Budget used: â‚¹{budget.get('used', 0):,.0f} / â‚¹{budget.get('limit', 0):,.0f}\n")

        # ------------------------------------------------------------------
        # 3. Attempt third payment â€” Flights (â‚¹12,000) â†’ will exceed budget
        #    â‚¹10,000 + â‚¹20,000 + â‚¹12,000 = â‚¹42,000 > â‚¹35,000 limit
        # ------------------------------------------------------------------
        print("[Primary Agent] Attempting Merchant C (Domain/Flights) â€” â‚¹12,000 ...")
        print("[Primary Agent] (This will push total to â‚¹42,000 â€” budget limit is â‚¹35,000)")
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
            print(f"\n  âœ— HARD FAILURE: {error.get('error_type')}")
            print(f"    {error.get('detail')}")
            print("\n[Primary Agent] CRASHED â€” â‚¹30,000 is now stranded in uncoordinated bookings.")
            print("[Aegis]         Compensating agent triggered automatically. Watch the dashboard.\n")
        else:
            data = resp.json()
            print(f"  â†’ Unexpected success: {data}")

        print(f"\n[Aegis Demo] Workflow ID: {wid}")
        print(f"[Aegis Demo] Connect the dashboard to ws://localhost:8000/ws/{wid} to see live events.")


if __name__ == "__main__":
    wid = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(run_procurement(wid))


