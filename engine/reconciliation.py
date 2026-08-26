"""
engine/reconciliation.py
Deterministic comparison of expected vs actual payment fields.
Each check is a separate named comparison (spec Section 6.2 / 9.3).
"""
from __future__ import annotations

from proxy.schemas import ReconciliationResult, TransactionLogEntry


def reconcile(
    entry: TransactionLogEntry,
    previously_seen_refs: set[str] | None = None,
) -> ReconciliationResult:
    """
    Compare the expected and actual fields of a log entry.
    Returns the FIRST failing check as mismatch_type, or match=True.

    Checks are ordered from most severe to least:
        hard_error → amount_mismatch → payee_mismatch → hard_error(status) → duplicate_settlement
    """
    if previously_seen_refs is None:
        previously_seen_refs = set()

    raw = entry.raw_gateway_response
    status_code: int = raw.get("status_code", 200)
    error_type: str = raw.get("error_type", "")

    # ---- 1. Hard gateway error (4xx/5xx or explicit error_type) ----
    if error_type in ("timeout", "connection_reset", "network_error", "mandate_limit_exceeded"):
        return ReconciliationResult(
            step_id=entry.step_id, match=False, mismatch_type="hard_error"
        )
    if isinstance(status_code, int) and status_code >= 400:
        return ReconciliationResult(
            step_id=entry.step_id, match=False, mismatch_type="hard_error"
        )

    # ---- 2. Amount mismatch ----
    amount_match: bool = entry.expected.amount == entry.actual.amount
    if not amount_match:
        return ReconciliationResult(
            step_id=entry.step_id, match=False, mismatch_type="amount_mismatch"
        )

    # ---- 3. Payee mismatch ----
    payee_match: bool = entry.expected.payee == entry.actual.payee
    if not payee_match:
        return ReconciliationResult(
            step_id=entry.step_id, match=False, mismatch_type="payee_mismatch"
        )

    # ---- 4. Status must be "settled" ----
    status_ok: bool = entry.actual.status == "settled"
    if not status_ok:
        return ReconciliationResult(
            step_id=entry.step_id, match=False, mismatch_type="hard_error"
        )

    # ---- 5. Duplicate settlement ref ----
    no_duplicate: bool = entry.actual.settlement_ref not in previously_seen_refs
    if not no_duplicate:
        return ReconciliationResult(
            step_id=entry.step_id, match=False, mismatch_type="duplicate_settlement"
        )

    return ReconciliationResult(step_id=entry.step_id, match=True, mismatch_type=None)
