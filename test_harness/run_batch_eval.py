"""
test_harness/run_batch_eval.py
Runs reconciliation + fault classification over all 50 synthetic records,
compares against ground_truth_label, and prints/writes a report.

Metrics (exactly as required by spec Section 9.6):
  - total_records
  - match_rate               : no_mismatch correctly identified / total clean records
  - mismatch_detection_rate  : mismatches correctly caught / total mismatch records
  - fault_classification_accuracy : correct label / total mismatch records
  - false_dispute_rate       : records labelled network_fault when truth is agent_fault
  - unresolved               : records the system could not confidently resolve
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.fault_classifier import classify_fault
from engine.reconciliation import reconcile
from proxy.schemas import TransactionLogEntry
from test_harness.generate_scenarios import generate


def run_eval() -> dict:
    records = generate()

    # Track seen settlement refs per workflow for duplicate detection
    seen_refs_by_workflow: dict[str, set[str]] = {}

    total = len(records)
    clean_count = 0
    clean_correct = 0
    mismatch_count = 0
    mismatch_detected = 0
    fault_correct = 0
    false_disputes = 0  # agent_fault truth but we said network_fault
    unresolved: list[dict] = []

    for rec in records:
        label: str = rec["ground_truth_label"]
        entry = TransactionLogEntry.model_validate({k: v for k, v in rec.items() if k != "ground_truth_label"})

        wid = entry.workflow_id
        seen = seen_refs_by_workflow.setdefault(wid, set())

        recon = reconcile(entry, previously_seen_refs=seen)

        if entry.actual.settlement_ref:
            seen.add(entry.actual.settlement_ref)

        # ---- Count clean records ----
        if label == "no_mismatch":
            clean_count += 1
            if recon.match:
                clean_correct += 1
            else:
                unresolved.append({
                    "step_id": entry.step_id,
                    "ground_truth": label,
                    "reconciliation_match": recon.match,
                    "mismatch_type": recon.mismatch_type,
                    "issue": "False positive — clean record flagged as mismatch",
                })
            continue

        # ---- Count mismatch records ----
        mismatch_count += 1
        if not recon.match:
            mismatch_detected += 1
        else:
            unresolved.append({
                "step_id": entry.step_id,
                "ground_truth": label,
                "reconciliation_match": recon.match,
                "mismatch_type": recon.mismatch_type,
                "issue": "False negative — mismatch not detected",
            })
            continue

        # ---- Fault classification (only for detected mismatches) ----
        fc = classify_fault(entry.step_id, entry.raw_gateway_response)
        predicted = fc.fault_type

        if label == "network_fault" and predicted == "network_fault":
            fault_correct += 1
        elif label == "agent_fault" and predicted == "agent_fault":
            fault_correct += 1
        elif label == "agent_fault" and predicted == "network_fault":
            # This is the critical metric — filing a false UDIR dispute
            false_disputes += 1
            unresolved.append({
                "step_id": entry.step_id,
                "ground_truth": label,
                "predicted_fault": predicted,
                "issue": "FALSE DISPUTE: agent_fault misclassified as network_fault",
            })
        else:
            # network_fault predicted as agent_fault (under-filing, acceptable per spec)
            unresolved.append({
                "step_id": entry.step_id,
                "ground_truth": label,
                "predicted_fault": predicted,
                "issue": "Under-filing: network_fault classified as agent_fault (conservative, acceptable)",
            })

    match_rate = round(clean_correct / clean_count, 4) if clean_count else 0.0
    mismatch_detection_rate = round(mismatch_detected / mismatch_count, 4) if mismatch_count else 0.0
    fault_classification_accuracy = round(fault_correct / mismatch_count, 4) if mismatch_count else 0.0
    false_dispute_rate = round(false_disputes / mismatch_count, 4) if mismatch_count else 0.0

    report = {
        "total_records": total,
        "clean_records": clean_count,
        "mismatch_records": mismatch_count,
        "match_rate": match_rate,
        "mismatch_detection_rate": mismatch_detection_rate,
        "fault_classification_accuracy": fault_classification_accuracy,
        "false_dispute_rate": false_dispute_rate,
        "false_disputes_count": false_disputes,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
    }
    return report


def main() -> None:
    report = run_eval()

    # Print human-readable summary
    print("\n" + "=" * 60)
    print("  AEGIS BATCH EVALUATION RESULTS")
    print("=" * 60)
    print(f"  Total records              : {report['total_records']}")
    print(f"  Clean records              : {report['clean_records']}")
    print(f"  Mismatch records           : {report['mismatch_records']}")
    print("-" * 60)
    print(f"  Match rate                 : {report['match_rate']:.1%}")
    print(f"  Mismatch detection rate    : {report['mismatch_detection_rate']:.1%}")
    print(f"  Fault classification acc.  : {report['fault_classification_accuracy']:.1%}")
    print(f"  False-dispute rate [*]     : {report['false_dispute_rate']:.1%}  ({report['false_disputes_count']} records)")
    print(f"  Unresolved                 : {report['unresolved_count']}")
    print("=" * 60)

    if report["unresolved"]:
        print("\n  Unresolved cases:")
        for u in report["unresolved"]:
            print(f"    - {u['step_id'][:8]}... -- {u['issue']}")

    # Write results.json to repo root
    out = Path(__file__).parent.parent / "results.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Full report written to: {out}\n")


if __name__ == "__main__":
    main()
