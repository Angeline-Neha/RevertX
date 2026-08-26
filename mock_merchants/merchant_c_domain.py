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


class RefundRequest(BaseModel):
    settlement_ref: str
    amount: float  # caller must supply the computed refund amount


@app.post("/charge")
def charge(req: ChargeRequest):
    ref = f"domain_{uuid.uuid4().hex[:8]}"
    charged_at = datetime.now(timezone.utc)
    _charges[ref] = {"amount": req.amount, "charged_at": charged_at.isoformat()}
    return {
        "settlement_ref": ref,
        "status": "settled",
        "amount": req.amount,
        "payee": "Domain Registrar",
        "status_code": 200,
    }


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
