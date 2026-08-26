"""
mock_merchants/merchant_a_crm.py
Merchant A — CRM software license.
Behaviour: instant, unconditional refund (the "easy case" in the demo).
Port: 8001
"""
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Merchant A — CRM")

# In-memory charge store: settlement_ref -> charge dict
_charges: dict[str, dict] = {}


class ChargeRequest(BaseModel):
    amount: float
    item: str = "CRM license"
    workflow_id: str = ""


class RefundRequest(BaseModel):
    settlement_ref: str


@app.post("/charge")
def charge(req: ChargeRequest):
    ref = f"crm_{uuid.uuid4().hex[:8]}"
    _charges[ref] = {"amount": req.amount, "item": req.item}
    return {
        "settlement_ref": ref,
        "status": "settled",
        "amount": req.amount,
        "payee": "CRM Corp",
        "status_code": 200,
    }


@app.post("/refund")
def refund(req: RefundRequest):
    charge = _charges.get(req.settlement_ref)
    if not charge:
        raise HTTPException(status_code=404, detail="Settlement ref not found")
    return {
        "status": "refunded",
        "amount": charge["amount"],
        "settlement_ref": req.settlement_ref,
    }
