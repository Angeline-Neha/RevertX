"""
proxy/schemas.py
Pydantic models for Sections 5.1-5.5 of the Aegis spec.
Field names are verbatim — test_harness/run_batch_eval.py imports these directly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Section 5.1 — Transaction Log Entry
# ---------------------------------------------------------------------------

class ExpectedPayment(BaseModel):
    amount: float
    currency: str = "INR"
    payee: str
    item: str


class ActualPayment(BaseModel):
    amount: float
    currency: str = "INR"
    payee: str
    settlement_ref: str
    status: str  # "settled" | "failed" | "pending"


class TransactionLogEntry(BaseModel):
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    action_type: str  # "payment" | "refund"
    merchant_id: str
    expected: ExpectedPayment
    actual: ActualPayment
    rail: str = "upi"  # "upi" | "card" | "netbanking"
    raw_gateway_response: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Section 5.2 — Reconciliation Result
# ---------------------------------------------------------------------------

class ReconciliationResult(BaseModel):
    step_id: str
    match: bool
    mismatch_type: Optional[str] = None
    # mismatch_type is one of:
    #   "amount_mismatch" | "payee_mismatch" | "duplicate_settlement" | "hard_error"


# ---------------------------------------------------------------------------
# Section 5.3 — Fault Classification Result
# ---------------------------------------------------------------------------

class FaultClassification(BaseModel):
    step_id: str
    fault_type: str  # "network_fault" | "agent_fault"
    classification_basis: str
    confidence_note: str


# ---------------------------------------------------------------------------
# Section 5.4 — UDIR-Shaped Evidence Payload (network_fault only)
# ---------------------------------------------------------------------------

class UDIRPayload(BaseModel):
    complaint_type: str = "TRANSACTION_MISMATCH"
    reason_code: str
    original_txn_ref: str
    expected_amount: float
    actual_amount: float
    npci_txn_id: str
    reported_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    tat_expected_hours: int = 48
    evidence_log_ref: str


# ---------------------------------------------------------------------------
# Section 5.5 — Internal Agent Liability Report (agent_fault only)
# ---------------------------------------------------------------------------

class LiabilityReport(BaseModel):
    workflow_id: str
    step_id: str
    what_happened: str
    expected_vs_actual: dict[str, Any]
    agent_reasoning_trace: Optional[str] = None
    financial_impact: float
    recommended_action: str
