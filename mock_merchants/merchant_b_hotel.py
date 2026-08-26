"""
mock_merchants/merchant_b_hotel.py
Merchant B — Hotel booking.
Behaviour: non-refundable if cancellation is within 7 days of booking date.
This server-side rejection is what makes the demo "halt" moment real.
Port: 8002
"""
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Merchant B — Hotel")

_charges: dict[str, dict] = {}

POLICY_TEXT = (
    "Bookings are non-refundable if cancellation is requested "
    "within 7 days of the booking date."
)


class ChargeRequest(BaseModel):
    amount: float
    item: str = "hotel booking"
    workflow_id: str = ""


class RefundRequest(BaseModel):
    settlement_ref: str


@app.post("/charge")
def charge(req: ChargeRequest):
    ref = f"hotel_{uuid.uuid4().hex[:8]}"
    booked_at = datetime.now(timezone.utc)
    _charges[ref] = {"amount": req.amount, "booked_at": booked_at.isoformat()}
    return {
        "settlement_ref": ref,
        "status": "settled",
        "amount": req.amount,
        "payee": "Grand Hotel",
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

    booked_at = datetime.fromisoformat(charge["booked_at"])
    now = datetime.now(timezone.utc)
    days_since_booking = (now - booked_at).days

    if days_since_booking < 7:
        # Server-side rejection — non-refundable window active
        return {
            "status": "rejected",
            "reason": (
                f"Non-refundable: cancellation requested {days_since_booking} day(s) "
                "after booking, which is within the 7-day non-refundable window."
            ),
            "settlement_ref": req.settlement_ref,
        }

    return {
        "status": "refunded",
        "amount": charge["amount"],
        "settlement_ref": req.settlement_ref,
    }
