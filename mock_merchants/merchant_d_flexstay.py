"""
mock_merchants/merchant_d_flexstay.py
Merchant D — Flex-Stay Hotels (flexible-cancellation hotel).
Behaviour: 30% cancellation fee if cancelled within 5 days of the booking
date, otherwise fully refundable. This is the first merchant whose refund
math is genuinely partial (Merchant C's 10% is also partial, but only within
a 48-hour window sized for a fast demo turnaround — this one exercises a
different window/percentage combo so the demo can show the math isn't
hardcoded to one specific pair of numbers).
Server-side validates the refund amount, same as Merchant C, to catch any
LLM math errors before money moves.
Port: 8006
"""
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Merchant D — Flex-Stay Hotels")

_charges: dict[str, dict] = {}

POLICY_TEXT = (
    "Bookings cancelled within 5 days of the booking date incur a 30% "
    "cancellation fee on the original charge amount. Cancellations made "
    "5 or more days after booking are fully refundable."
)
PENALTY_PERCENTAGE = 30.0
PENALTY_WINDOW_DAYS = 5


class ChargeRequest(BaseModel):
    amount: float
    item: str = "hotel booking (flexible)"
    workflow_id: str = ""


class RefundRequest(BaseModel):
    settlement_ref: str
    amount: float  # caller must supply the computed refund amount


@app.post("/charge")
def charge(req: ChargeRequest):
    ref = f"flexstay_{uuid.uuid4().hex[:8]}"
    booked_at = datetime.now(timezone.utc)
    _charges[ref] = {"amount": req.amount, "booked_at": booked_at.isoformat()}
    return {
        "settlement_ref": ref,
        "status": "settled",
        "amount": req.amount,
        "payee": "Flex-Stay Hotels",
        "status_code": 200,
    }


@app.get("/policy")
def get_policy(booking_date: str = ""):
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

    tolerance = 0.01  # floating-point tolerance
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
