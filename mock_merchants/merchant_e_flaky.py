"""
mock_merchants/merchant_e_flaky.py
Merchant E — Apex Print & Signage (flaky vendor).
Behaviour: charges and refunds normally (fully refundable, no penalty), but
its /policy endpoint intermittently fails — alternating between a 500 and
a request that hangs past the caller's client timeout (simulating a real
gateway timeout), succeeding on every other call.

Purpose: exercises compensating_agent/graph.py's fetch_policy fail-safe path
(see the comment on that function) — a /policy call that errors is treated
as an unknown-therefore-non-refundable outcome, not silently defaulted to a
full refund. Also gives engine/anomaly_detector.py something real to flag
instead of always seeing a clean run.

Failure pattern: the counter below resets to 0 each time this process
starts (run_demo.py/run_bg.py launch a fresh uvicorn process per merchant),
so the FIRST call to /policy in any fresh demo run deterministically fails —
this is what makes it usable as a reliable, repeatable demo trigger (see
Phase 8's scenario 5) despite genuinely alternating pass/fail behaviour on
subsequent calls within the same process lifetime, which is what "flaky"
actually means here.
Port: 8007
"""
import asyncio
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

app = FastAPI(title="Merchant E — Apex Print & Signage")

_charges: dict[str, dict] = {}
_policy_call_count = 0

POLICY_TEXT = (
    "Orders are fully refundable if cancelled before the print run begins."
)


class ChargeRequest(BaseModel):
    amount: float
    item: str = "signage printing"
    workflow_id: str = ""


class RefundRequest(BaseModel):
    settlement_ref: str


@app.post("/charge")
def charge(req: ChargeRequest):
    ref = f"apexprint_{uuid.uuid4().hex[:8]}"
    _charges[ref] = {
        "amount": req.amount,
        "charged_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "settlement_ref": ref,
        "status": "settled",
        "amount": req.amount,
        "payee": "Apex Print & Signage",
        "status_code": 200,
    }


@app.get("/policy")
async def get_policy():
    global _policy_call_count
    _policy_call_count += 1
    call_number = _policy_call_count

    if call_number % 2 == 1:
        # Odd calls fail — alternate between the two failure modes the
        # real world actually produces, so both branches of a caller's
        # generic `except Exception` get exercised across repeated runs.
        if (call_number // 2) % 2 == 0:
            raise HTTPException(status_code=500, detail="Internal error fetching policy — please retry.")
        else:
            # compensating_agent/graph.py's fetch_policy uses a 10s client
            # timeout; sleeping past that produces a real httpx.ReadTimeout
            # on the caller side, not a simulated one.
            await asyncio.sleep(15)

    return {"policy": POLICY_TEXT}


@app.post("/refund")
def refund(req: RefundRequest):
    charge = _charges.get(req.settlement_ref)
    if not charge:
        raise HTTPException(status_code=404, detail="Settlement ref not found")
    return {"status": "refunded", "amount": charge["amount"], "settlement_ref": req.settlement_ref}
