"""
mock_merchants/merchant_f_venue.py
Merchant F — Riverside Events Hall (venue booking).
Behaviour: booking deposits are non-refundable under any circumstances —
no time window, unlike Merchant B. Part of the "event launch" vendor set
(Merchant F + merchant_g_catering.py) that proves nothing in the dashboard
or compensation engine is hardcoded to the original CRM/Hotel/Domain names
(Phase 6.3).
Port: 8008
"""
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Merchant F — Riverside Events Hall")

_charges: dict[str, dict] = {}

POLICY_TEXT = "Venue booking deposits are non-refundable under any circumstances."


class ChargeRequest(BaseModel):
    amount: float
    item: str = "venue booking"
    workflow_id: str = ""


class RefundRequest(BaseModel):
    settlement_ref: str


@app.post("/charge")
def charge(req: ChargeRequest):
    ref = f"venue_{uuid.uuid4().hex[:8]}"
    _charges[ref] = {
        "amount": req.amount,
        "charged_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "settlement_ref": ref,
        "status": "settled",
        "amount": req.amount,
        "payee": "Riverside Events Hall",
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
    # Always rejected — no window to check, unlike merchant_b_hotel.py.
    return {
        "status": "rejected",
        "reason": "Non-refundable: venue booking deposits cannot be refunded under any circumstances.",
        "settlement_ref": req.settlement_ref,
    }
