"""
mock_merchants/merchant_c_domain.py
Merchant C — Domain registration / CRM alt.
Behaviour: 10% cancellation penalty within 48 hours.
Server-side validates the refund amount to catch any LLM math errors.
Port: 8003
"""
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Merchant C — Domain")

_charges: dict[str, dict] = {}

POLICY_TEXT = (
    "Cancellations within 48 hours of purchase incur a 10% penalty "
    "on the original charge amount."
)
PENALTY_PERCENTAGE = 10.0


class ChargeRequest(BaseModel):
    amount: float
    item: str = "domain registration"
    workflow_id: str = ""
    # Demo-only: simulates spec Failure Mode 2 ("Silent Break", §10 step 7).
    # When True, the charge is still recorded internally as settled (this
    # IS a real, ground-truth charge) but the HTTP response withholds that
    # and instead looks exactly like a gateway timeout — no settlement_ref,
    # a 504 status, and a raw timeout error_type. This is what "no error is
    # thrown to the agent, but the outcome doesn't match what was intended"
    # means concretely: the caller's own record of this call will say
    # "failed", while Merchant C's internal ledger says "settled". See
    # demo_silent_break.py, which drives this end-to-end against the real
    # dashboard and compensating agent.
    simulate_silent_timeout: bool = False


class RefundRequest(BaseModel):
    settlement_ref: str
    amount: float  # caller must supply the computed refund amount


@app.post("/charge")
def charge(req: ChargeRequest):
    ref = f"domain_{uuid.uuid4().hex[:8]}"
    charged_at = datetime.now(timezone.utc)
    _charges[ref] = {
        "amount": req.amount,
        "charged_at": charged_at.isoformat(),
        "payee": "Domain Registrar",
        "workflow_id": req.workflow_id,
        "status": "settled",
    }

    if req.simulate_silent_timeout:
        # Deliberately do NOT return settlement_ref, status, or payee here —
        # a real caller in this situation genuinely has no way to know the
        # charge went through. The only way to discover it is the
        # ground-truth check at GET /internal_ledger/{workflow_id} below,
        # which is what Aegis's reconciliation is meant to catch.
        return JSONResponse(
            status_code=504,
            content={
                "error_type": "timeout",
                "detail": "Gateway timeout — no confirmation received from processor.",
            },
        )

    return {
        "settlement_ref": ref,
        "status": "settled",
        "amount": req.amount,
        "payee": "Domain Registrar",
        "status_code": 200,
    }


@app.get("/internal_ledger/{workflow_id}")
def internal_ledger(workflow_id: str):
    """
    Ground truth for reconciliation — represents what a real integration
    would get from a settlement-file/webhook reconciliation job, not
    something a live agent-facing API would normally expose. Demo-only:
    lets demo_silent_break.py discover a charge that /charge's response
    hid, without the demo needing to inspect this process's memory
    directly (keeps it a real HTTP round trip like everything else here).
    """
    charges = [
        {"settlement_ref": ref, **data}
        for ref, data in _charges.items()
        if data.get("workflow_id") == workflow_id
    ]
    return {"workflow_id": workflow_id, "charges": charges}


@app.get("/policy")
def get_policy():
    return {"policy": POLICY_TEXT}


@app.post("/refund")
def refund(req: RefundRequest):
    charge = _charges.get(req.settlement_ref)
    if not charge:
        raise HTTPException(status_code=404, detail="Settlement ref not found")

    original = charge["amount"]
    charged_at = datetime.fromisoformat(charge["charged_at"])
    hours_since = (datetime.now(timezone.utc) - charged_at).total_seconds() / 3600

    # Within 48 hours → 10% penalty applies
    if hours_since < 48:
        expected_refund = round(original * (1 - PENALTY_PERCENTAGE / 100), 2)
        tolerance = 0.01  # floating-point tolerance
        if abs(req.amount - expected_refund) > tolerance:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Refund amount mismatch. "
                    f"Expected ₹{expected_refund} (original ₹{original} - 10% penalty), "
                    f"got ₹{req.amount}. Refund rejected."
                ),
            )
        return {"status": "refunded", "amount": req.amount, "settlement_ref": req.settlement_ref}

    # Outside 48 hours → full refund, no penalty
    return {"status": "refunded", "amount": original, "settlement_ref": req.settlement_ref}
