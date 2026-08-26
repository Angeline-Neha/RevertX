"""
test_harness/generate_scenarios.py
Generates 50+ synthetic TransactionLogEntry records with ground_truth_label.

Distribution (per spec Section 9.6):
  30 clean records          (no_mismatch)
   8 amount_mismatch        (agent_fault)
   5 payee_mismatch         (agent_fault)
   4 network_fault (5xx)    (network_fault)
   3 duplicate_settlement   (agent_fault)
  ——
  50 total
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from proxy.schemas import ActualPayment, ExpectedPayment, TransactionLogEntry


def _make_entry(
    *,
    merchant_id: str = "merchant_a",
    expected_amount: float = 10000.0,
    actual_amount: float = 10000.0,
    expected_payee: str = "CRM Corp",
    actual_payee: str = "CRM Corp",
    status: str = "settled",
    settlement_ref: str | None = None,
    raw_gateway_response: dict[str, Any] | None = None,
    ground_truth_label: str,
    workflow_id: str | None = None,
    # Offset timestamp so ordering is deterministic
    ts_offset_seconds: int = 0,
) -> dict:
    ref = settlement_ref or f"ref_{uuid.uuid4().hex[:10]}"
    ts = (datetime.now(timezone.utc) + timedelta(seconds=ts_offset_seconds)).isoformat()
    wid = workflow_id or str(uuid.uuid4())

    entry = TransactionLogEntry(
        workflow_id=wid,
        timestamp=ts,
        action_type="payment",
        merchant_id=merchant_id,
        expected=ExpectedPayment(
            amount=expected_amount, currency="INR", payee=expected_payee, item="test item"
        ),
        actual=ActualPayment(
            amount=actual_amount,
            currency="INR",
            payee=actual_payee,
            settlement_ref=ref,
            status=status,
        ),
        raw_gateway_response=raw_gateway_response or {"status_code": 200},
    )
    record = entry.model_dump()
    record["ground_truth_label"] = ground_truth_label
    return record


def generate() -> list[dict]:
    records: list[dict] = []
    shared_wid = str(uuid.uuid4())  # used for duplicate_settlement records

    # ------------------------------------------------------------------ #
    # 1. 30 clean records — all fields match, status settled
    # ------------------------------------------------------------------ #
    for i in range(30):
        records.append(_make_entry(
            merchant_id="merchant_a",
            expected_amount=10000.0 + i * 100,
            actual_amount=10000.0 + i * 100,
            expected_payee="CRM Corp",
            actual_payee="CRM Corp",
            status="settled",
            raw_gateway_response={"status_code": 200},
            ground_truth_label="no_mismatch",
        ))

    # ------------------------------------------------------------------ #
    # 2. 8 amount_mismatch records — 2xx gateway but settled amount differs
    # ------------------------------------------------------------------ #
    for i in range(8):
        records.append(_make_entry(
            merchant_id="merchant_b",
            expected_amount=20000.0,
            actual_amount=18000.0 + i * 100,  # < expected
            expected_payee="Grand Hotel",
            actual_payee="Grand Hotel",
            status="settled",
            raw_gateway_response={"status_code": 200},
            ground_truth_label="agent_fault",
        ))

    # ------------------------------------------------------------------ #
    # 3. 5 payee_mismatch records — 2xx but wrong payee name
    # ------------------------------------------------------------------ #
    wrong_payees = [
        "Grand Motel", "Hotel Grand", "Grand Htl", "GRAND HOTEL LTD", "Grand Hotel (GST)"
    ]
    for i in range(5):
        records.append(_make_entry(
            merchant_id="merchant_b",
            expected_amount=20000.0,
            actual_amount=20000.0,
            expected_payee="Grand Hotel",
            actual_payee=wrong_payees[i],
            status="settled",
            raw_gateway_response={"status_code": 200},
            ground_truth_label="agent_fault",
        ))

    # ------------------------------------------------------------------ #
    # 4. 4 network_fault records — 5xx / timeout raw response
    # ------------------------------------------------------------------ #
    network_errors = [
        {"status_code": 500, "error": "Internal Server Error"},
        {"status_code": 503, "error": "Service Unavailable"},
        {"status_code": 502, "error": "Bad Gateway"},
        {"status_code": 408, "error_type": "timeout", "error": "Gateway timeout"},
    ]
    for i in range(4):
        records.append(_make_entry(
            merchant_id="merchant_c",
            expected_amount=12000.0,
            actual_amount=12000.0,
            expected_payee="Domain Registrar",
            actual_payee="Domain Registrar",
            status="failed",
            raw_gateway_response=network_errors[i],
            ground_truth_label="network_fault",
        ))

    # ------------------------------------------------------------------ #
    # 5. duplicate_settlement — 1 clean original + 3 true duplicates
    #    The evaluator sees the original first (adds ref to seen_refs), then
    #    catches all 3 subsequent occurrences as duplicate_settlement.
    # ------------------------------------------------------------------ #
    duplicate_ref = f"ref_DUPLICATE_{uuid.uuid4().hex[:6]}"
    # Original (clean) — must come first so seen_refs is seeded before duplicates
    records.append(_make_entry(
        merchant_id="merchant_a",
        expected_amount=10000.0,
        actual_amount=10000.0,
        expected_payee="CRM Corp",
        actual_payee="CRM Corp",
        status="settled",
        settlement_ref=duplicate_ref,
        raw_gateway_response={"status_code": 200},
        ground_truth_label="no_mismatch",
        workflow_id=shared_wid,
    ))
    # True duplicates — all 3 will be caught once the original ref is in seen_refs
    for i in range(3):
        records.append(_make_entry(
            merchant_id="merchant_a",
            expected_amount=10000.0,
            actual_amount=10000.0,
            expected_payee="CRM Corp",
            actual_payee="CRM Corp",
            status="settled",
            settlement_ref=duplicate_ref,
            raw_gateway_response={"status_code": 200},
            ground_truth_label="agent_fault",
            workflow_id=shared_wid,
        ))

    assert len(records) == 51, f"Expected 51 records, got {len(records)}"
    return records


if __name__ == "__main__":
    out_path = Path(__file__).parent / "synthetic_records.json"
    data = generate()
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Generated {len(data)} synthetic records → {out_path}")
