"""
mock_merchants/merchant_g_catering.py
Merchant G — Spice Route Catering.
Behaviour: cancellations made within 3 days of booking incur a 50%
cancellation fee; 3 or more days out, fully refundable. Different
window/percentage pair from both Merchant C (10%/48h) and Merchant D
(30%/5 days), and part of the "event launch" vendor set alongside
merchant_f_venue.py (Phase 6.3).
Server-side validates the refund amount, same pattern as Merchant C/D.
Port: 8009
"""
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Merchant G — Spice Route Catering")

_charges: dict[str, dict] = {}

POLICY_TEXT = (
    "Cancellations made within 3 days of booking incur a 50% cancellation "
    "fee on the original charge amount. Cancellations made 3 or more days "
    "after booking are fully refundable."
)
PENALTY_PERCENTAGE = 50.0
PENALTY_WINDOW_DAYS = 3


class ChargeRequest(BaseModel):
    amount: float
    item: str = "catering"
    workflow_id: str = ""


class RefundRequest(BaseModel):
    settlement_ref: str
    amount: float  # caller must supply the computed refund amount


@app.post("/charge")
def charge(req: ChargeRequest):
    ref = f"catering_{uuid.uuid4().hex[:8]}"
    _charges[ref] = {
        "amount": req.amount,
        "booked_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "settlement_ref": ref,
        "status": "settled",
        "amount": req.amount,
        "payee": "Spice Route Catering",
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
    booked_at = datetime.fromisoformat(charge["booked_at"])
    days_since_booking = (datetime.now(timezone.utc) - booked_at).days

    if days_since_booking < PENALTY_WINDOW_DAYS:
        expected_refund = round(original * (1 - PENALTY_PERCENTAGE / 100), 2)
    else:
        expected_refund = original

    tolerance = 0.01
    if abs(req.amount - expected_refund) > tolerance:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Refund amount mismatch. "
                f"Expected ₹{expected_refund} "
                f"(original ₹{original}"
                + (f" - {PENALTY_PERCENTAGE}% penalty" if days_since_booking < PENALTY_WINDOW_DAYS else "")
                + f"), got ₹{req.amount}. Refund rejected."
            ),
        )

    return {"status": "refunded", "amount": req.amount, "settlement_ref": req.settlement_ref}
