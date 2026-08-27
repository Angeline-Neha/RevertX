"""
compensating_agent/graph.py
LangGraph StateGraph implementing the Aegis compensation workflow.

Node order (spec Section 9.4):
  load_workflow_log
  → select_next_step_to_undo
  → fetch_policy  (or skip to attempt_refund for Merchant A)
  → extract_policy_terms
  → compute_refund_amount
  → attempt_refund
  → loop_or_end
        if more steps → select_next_step_to_undo
        if done       → classify_and_route
                            → generate_udir_payload   (network_fault)
                            → generate_liability_report (agent_fault)
  → END

Every node logs its input/output to Redis compensation_trace, which the
WebSocket endpoint forwards to the frontend in real time.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional
import os

import httpx
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

from proxy.schemas import FaultClassification, LiabilityReport, UDIRPayload
from engine.fault_classifier import classify_fault
from refund_math import compute_refund
from state_log.redis_client import (
    get_workflow_steps,
    publish_event,
    write_compensation_trace,
)
import structlog
from prometheus_client import Counter

logger = structlog.get_logger()
try:
    FALSE_DISPUTES_METRIC = Counter('aegis_false_disputes_total', 'Number of network_faults flagged for client errors (4xx)')
    REFUND_SUCCESS_METRIC = Counter('aegis_refund_success_total', 'Successful refunds via gateway', ['merchant_id'])
    REFUND_FAILURE_METRIC = Counter('aegis_refund_failure_total', 'Failed refunds via gateway', ['merchant_id'])
except ValueError:
    from prometheus_client import REGISTRY
    FALSE_DISPUTES_METRIC = REGISTRY._names_to_collectors['aegis_false_disputes_total']
    REFUND_SUCCESS_METRIC = REGISTRY._names_to_collectors['aegis_refund_success_total']
    REFUND_FAILURE_METRIC = REGISTRY._names_to_collectors['aegis_refund_failure_total']

# ---------------------------------------------------------------------------
# Merchant base URLs
# ---------------------------------------------------------------------------
MERCHANT_URLS: dict[str, str] = {
    "merchant_a": "http://localhost:8001",
    "merchant_b": "http://localhost:8002",
    "merchant_c": "http://localhost:8003",
}

# Merchants that have a /policy endpoint
MERCHANTS_WITH_POLICY: frozenset[str] = frozenset({"merchant_b", "merchant_c"})


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------
class CompensationState(TypedDict):
    workflow_id: str
    failing_step: dict           # the original step that triggered compensation
    steps_to_undo: list[dict]    # reversed list of successful step dicts
    current_step: Optional[dict]
    policy_text: Optional[str]
    policy_terms: Optional[dict]
    refund_amount: Optional[float]
    compensation_results: list[dict]  # accumulated undo results
    fault_classification: Optional[dict]
    udir_payload: Optional[dict]
    liability_report: Optional[dict]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trace(workflow_id: str, node: str, status: str, data: dict) -> None:
    """Publish a trace event to Redis and the WebSocket feed."""
    entry = {
        "node": node,
        "status": status,  # "start" | "end" | "error" | "skip"
        "workflow_id": workflow_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    write_compensation_trace(workflow_id, entry)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def load_workflow_log(state: CompensationState) -> dict:
    wid = state["workflow_id"]
    _trace(wid, "load_workflow_log", "start", {})

    steps = get_workflow_steps(wid)
    # Reverse so we undo most-recent first; skip the failing step itself
    failing_ref = state["failing_step"].get("step_id", "")
    reversed_steps = [
        s.model_dump() for s in reversed(steps)
        if s.step_id != failing_ref and s.actual.status == "settled"
    ]

    _trace(wid, "load_workflow_log", "end", {"steps_found": len(reversed_steps)})
    return {
        "steps_to_undo": reversed_steps,
        "compensation_results": [],
    }


def select_next_step_to_undo(state: CompensationState) -> dict:
    wid = state["workflow_id"]
    remaining = state["steps_to_undo"]

    if not remaining:
        _trace(wid, "select_next_step_to_undo", "end", {"selected": None})
        return {"current_step": None}

    current = remaining[0]
    _trace(wid, "select_next_step_to_undo", "end", {
        "selected": current.get("step_id"),
        "merchant_id": current.get("merchant_id"),
    })
    return {
        "current_step": current,
        "steps_to_undo": remaining[1:],
        "policy_text": None,
        "policy_terms": None,
        "refund_amount": None,
    }


def _route_after_select(state: CompensationState) -> str:
    if state.get("current_step") is None:
        return "classify_and_route"
    return "fetch_policy"


async def fetch_policy(state: CompensationState) -> dict:
    wid = state["workflow_id"]
    step = state["current_step"]
    merchant_id = step["merchant_id"]

    _trace(wid, "fetch_policy", "start", {"merchant_id": merchant_id})

    if merchant_id not in MERCHANTS_WITH_POLICY:
        _trace(wid, "fetch_policy", "skip", {"reason": "merchant has no /policy endpoint"})
        return {"policy_text": None}

    base_url = MERCHANT_URLS.get(merchant_id, "")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/policy")
            resp.raise_for_status()
            policy_data = resp.json()
        policy_text = policy_data.get("policy", "")
        _trace(wid, "fetch_policy", "end", {"policy_text": policy_text})
        return {"policy_text": policy_text}
    except Exception as exc:
        _trace(wid, "fetch_policy", "error", {"error": str(exc)})
        return {"policy_text": None}


def _route_after_fetch_policy(state: CompensationState) -> str:
    if state.get("policy_text"):
        return "extract_policy_terms"
    return "attempt_refund"


async def extract_policy_terms_node(state: CompensationState) -> dict:
    wid = state["workflow_id"]
    policy_text = state.get("policy_text")
    if not policy_text:
        return {"policy_terms": None}

    _trace(wid, "extract_policy", "start", {})
    
    # Decoupled via HTTP microservice
    policy_url = os.getenv("POLICY_SERVICE_URL", "http://localhost:8004")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{policy_url}/extract", json={"policy_text": policy_text})
            resp.raise_for_status()
            terms_data = resp.json()
            
            # Reconstruct the dict matching what downstream nodes expect
            policy_terms = {
                "refundable": terms_data.get("refundable", False),
                "penalty_percentage": terms_data.get("penalty_percentage"),
                "conditions": terms_data.get("conditions", "")
            }
            _trace(wid, "extract_policy", "end", policy_terms)
            return {"policy_terms": policy_terms}
    except Exception as exc:
        _trace(wid, "extract_policy", "error", {"error": str(exc)})
        return {"policy_terms": {"refundable": False, "penalty_percentage": None, "conditions": "Fallback due to extraction service failure"}}


def compute_refund_amount_node(state: CompensationState) -> dict:
    """Deterministic arithmetic only — no LLM."""
    wid = state["workflow_id"]
    step = state["current_step"]
    terms = state.get("policy_terms") or {}

    original_amount: float = step["expected"]["amount"]
    refundable: bool = terms.get("refundable", False)
    penalty_pct: float | None = terms.get("penalty_percentage")

    _trace(wid, "compute_refund_amount", "start", {
        "original_amount": original_amount,
        "refundable": refundable,
        "penalty_percentage": penalty_pct,
    })

    amount = compute_refund(original_amount, penalty_pct, refundable)

    if amount is not None:
        penalty_val = original_amount * ((penalty_pct or 0) / 100)
        math_line = (
            f"₹{original_amount:,.2f}"
            + (f" × {penalty_pct}% penalty = ₹{penalty_val:,.2f} → " if penalty_pct else " (no penalty) → ")
            + f"refund ₹{amount:,.2f}"
        )
    else:
        math_line = f"Non-refundable. No refund issued."

    publish_event(wid, "math_computation", {"formula": math_line})
    _trace(wid, "compute_refund_amount", "end", {"refund_amount": amount, "formula": math_line})
    return {"refund_amount": amount}


async def attempt_refund_node(state: CompensationState) -> dict:
    wid = state["workflow_id"]
    step = state["current_step"]
    merchant_id = step["merchant_id"]
    settlement_ref = step["actual"]["settlement_ref"]
    refund_amount = state.get("refund_amount")

    _trace(wid, "attempt_refund", "start", {
        "merchant_id": merchant_id,
        "settlement_ref": settlement_ref,
        "refund_amount": refund_amount,
    })

    base_url = MERCHANT_URLS.get(merchant_id, "")
    results = state.get("compensation_results") or []
    
    import db.client as db
    cb_state = await db.check_circuit_breaker(merchant_id)
    if cb_state == "open":
        await db.write_dlq_entry(wid, merchant_id, step["step_id"], refund_amount or 0.0, settlement_ref, "circuit_breaker_open")
        result = {
            "step_id": step["step_id"], "merchant_id": merchant_id, "outcome": "dlq", 
            "amount_recovered": 0.0, "message": "Circuit breaker open. Sent to DLQ."
        }
        _trace(wid, "attempt_refund", "dlq_queued", result)
        return {"compensation_results": results + [result]}

    if refund_amount is None and state.get("policy_terms") is not None:
        # Non-refundable — policy explicitly blocks it
        result = {
            "step_id": step["step_id"],
            "merchant_id": merchant_id,
            "outcome": "non_refundable",
            "message": "Policy prevents refund. Auto-reversal aborted to avoid penalizing the merchant.",
            "amount_recovered": 0.0,
        }
        _trace(wid, "attempt_refund", "end", result)
        publish_event(wid, "refund_halted", result)
        return {"compensation_results": results + [result]}

    if refund_amount is None:
        # Merchant A path — no policy, attempt full refund
        refund_amount = step["actual"]["amount"]

    try:
        payload: dict[str, Any] = {"settlement_ref": settlement_ref}
        if merchant_id == "merchant_c":
            payload["amount"] = refund_amount

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{base_url}/refund", json=payload)
        data = resp.json()
        await db.reset_circuit_breaker(merchant_id)

        if resp.status_code == 200 and data.get("status") in ("refunded", "success"):
            result = {
                "step_id": step["step_id"],
                "merchant_id": merchant_id,
                "outcome": "refunded",
                "amount_recovered": refund_amount,
                "message": f"Refund successful via gateway ({resp.status_code})"
            }
            publish_event(wid, "refund_success", result)
            REFUND_SUCCESS_METRIC.labels(merchant_id=merchant_id).inc()
        else:
            result = {
                "step_id": step["step_id"],
                "merchant_id": merchant_id,
                "outcome": "failed",
                "amount_recovered": 0.0,
                "message": f"Gateway rejected refund ({resp.status_code}): {resp.text}"
            }
            publish_event(wid, "refund_failed", result)
            REFUND_FAILURE_METRIC.labels(merchant_id=merchant_id).inc()

    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        new_state = await db.record_merchant_failure(merchant_id)
        await db.write_dlq_entry(wid, merchant_id, step["step_id"], refund_amount, settlement_ref, str(exc))
        result = {
            "step_id": step["step_id"],
            "merchant_id": merchant_id,
            "outcome": "dlq",
            "amount_recovered": 0.0,
            "message": f"Merchant error. DLQ queued. CB State: {new_state}",
        }
    except Exception as exc:
        result = {
            "step_id": step["step_id"],
            "merchant_id": merchant_id,
            "outcome": "error",
            "amount_recovered": 0.0,
            "message": str(exc),
        }

    _trace(wid, "attempt_refund", "end", result)
    return {"compensation_results": results + [result]}


def _route_loop_or_end(state: CompensationState) -> str:
    if state.get("steps_to_undo"):
        return "select_next_step_to_undo"
    return "classify_and_route"


def classify_and_route_node(state: CompensationState) -> dict:
    """Classify the original failure and decide UDIR vs Liability Report."""
    wid = state["workflow_id"]
    failing_step = state["failing_step"]
    step_id = failing_step.get("step_id", "unknown")
    raw_response = failing_step.get("raw_gateway_response", {})

    _trace(wid, "classify_and_route", "start", {"step_id": step_id})

    classification = classify_fault(step_id, raw_response)
    _trace(wid, "classify_and_route", "end", {"fault_type": classification.fault_type})
    
    # If the fault is a 4xx error but classified as network_fault, it's a false dispute
    status_code = failing_step.get("actual", {}).get("status_code", 500)
    if classification.fault_type == "network_fault" and status_code < 500:
        FALSE_DISPUTES_METRIC.inc()

    return {"fault_classification": classification.model_dump()}


def _route_after_classify(state: CompensationState) -> str:
    fc = state.get("fault_classification") or {}
    return "generate_udir_payload" if fc.get("fault_type") == "network_fault" else "generate_liability_report"


def generate_udir_payload_node(state: CompensationState) -> dict:
    """Only called for network_fault.  Never called for agent_fault."""
    wid = state["workflow_id"]
    failing_step = state["failing_step"]

    _trace(wid, "generate_udir_payload", "start", {})

    payload = UDIRPayload(
        complaint_type="TRANSACTION_MISMATCH",
        reason_code="DRC-04",  # closest UDIR reason: deemed dispute
        original_txn_ref=failing_step.get("actual", {}).get("settlement_ref", ""),
        expected_amount=failing_step.get("expected", {}).get("amount", 0),
        actual_amount=failing_step.get("actual", {}).get("amount", 0),
        npci_txn_id=failing_step.get("actual", {}).get("settlement_ref", ""),
        evidence_log_ref=f"redis://workflow:{wid}:step:{failing_step.get('step_id')}",
    ).model_dump()

    publish_event(wid, "final_output", {
        "type": "udir_payload",
        "label": "Network-Fault → Filing UDIR complaint",
        "payload": payload,
    })
    _trace(wid, "generate_udir_payload", "end", {"payload": payload})
    return {"udir_payload": payload}


async def generate_liability_report_node(state: CompensationState) -> dict:
    wid = state["workflow_id"]
    _trace(wid, "generate_liability_report", "start", {})
    failing_step = state["failing_step"]
    results = state.get("compensation_results") or []
    
    from state_log.redis_client import get_workflow_steps
    all_steps = get_workflow_steps(wid)
    total_unrecovered = 0.0
    for r in results:
        if r.get("outcome") != "refunded":
            step_obj = next((s for s in all_steps if s.step_id == r.get("step_id")), None)
            if step_obj and getattr(step_obj, "actual", None):
                total_unrecovered += getattr(step_obj.actual, "amount", 0.0)

    report = LiabilityReport(
        workflow_id=wid,
        step_id=failing_step.get("step_id", ""),
        what_happened=(
            f"Agent attempted a payment that exceeded the declared budget limit. "
            f"The proxy rejected it with mandate_limit_exceeded (403). "
            f"Compensation attempted for {len(results)} prior step(s)."
        ),
        expected_vs_actual={
            "expected": failing_step.get("expected", {}),
            "actual": failing_step.get("actual", {}),
        },
        agent_reasoning_trace=json.dumps(results, indent=2),
        financial_impact=total_unrecovered,
        recommended_action=(
            "Tighten mandate scope: add a pre-flight budget check before each payment "
            "step so the agent cannot commit to a third transaction without verifying "
            "remaining budget. Add human approval for bookings above ₹15,000."
        ),
    ).model_dump()

    publish_event(wid, "final_output", {
        "type": "liability_report",
        "label": "Agent-Fault → Internal report only, no complaint filed",
        "payload": report,
    })
    _trace(wid, "generate_liability_report", "end", {"report": report})

    # Advisory-only, non-blocking: the liability report above is already
    # complete and published. The anomaly check runs as a background task
    # against its own isolated service (engine/anomaly_service.py) with its
    # own timeout, so a slow or failing LLM call can never delay a report a
    # human reviewer actually needs. If it fails or times out, nothing about
    # the liability report is affected — this only ever adds an extra flag.
    asyncio.create_task(_check_anomalies_background(wid, results))

    return {"liability_report": report}


async def _check_anomalies_background(wid: str, results: list[dict]) -> None:
    """Fire-and-forget call to the isolated anomaly service. See module
    docstring in engine/anomaly_service.py for why this is a separate
    service rather than an inline import."""
    anomaly_service_url = os.getenv("ANOMALY_SERVICE_URL", "http://localhost:8005")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{anomaly_service_url}/flag_anomalies",
                json={"workflow_id": wid, "steps": results},
            )
            resp.raise_for_status()
            anomaly_result = resp.json()
    except Exception as exc:
        _trace(wid, "anomaly_check", "error", {"error": str(exc)})
        return

    if anomaly_result.get("is_anomalous"):
        publish_event(wid, "anomaly_detected", anomaly_result)
        _trace(wid, "anomaly_check", "anomaly_flagged", anomaly_result)
    else:
        _trace(wid, "anomaly_check", "end", anomaly_result)


# ---------------------------------------------------------------------------
# Build and compile the graph
# ---------------------------------------------------------------------------

def build_graph(checkpointer=None) -> "CompiledGraph":  # type: ignore[name-defined]
    g = StateGraph(CompensationState)

    g.add_node("load_workflow_log", load_workflow_log)
    g.add_node("select_next_step_to_undo", select_next_step_to_undo)
    g.add_node("fetch_policy", fetch_policy)
    g.add_node("extract_policy_terms", extract_policy_terms_node)
    g.add_node("compute_refund_amount", compute_refund_amount_node)
    g.add_node("attempt_refund", attempt_refund_node)
    g.add_node("classify_and_route", classify_and_route_node)
    g.add_node("generate_udir_payload", generate_udir_payload_node)
    g.add_node("generate_liability_report", generate_liability_report_node)

    g.set_entry_point("load_workflow_log")
    g.add_edge("load_workflow_log", "select_next_step_to_undo")

    g.add_conditional_edges(
        "select_next_step_to_undo",
        _route_after_select,
        {"classify_and_route": "classify_and_route", "fetch_policy": "fetch_policy"},
    )

    g.add_conditional_edges(
        "fetch_policy",
        _route_after_fetch_policy,
        {"extract_policy_terms": "extract_policy_terms", "attempt_refund": "attempt_refund"},
    )

    g.add_edge("extract_policy_terms", "compute_refund_amount")
    g.add_edge("compute_refund_amount", "attempt_refund")

    g.add_conditional_edges(
        "attempt_refund",
        _route_loop_or_end,
        {
            "select_next_step_to_undo": "select_next_step_to_undo",
            "classify_and_route": "classify_and_route",
        },
    )

    g.add_conditional_edges(
        "classify_and_route",
        _route_after_classify,
        {
            "generate_udir_payload": "generate_udir_payload",
            "generate_liability_report": "generate_liability_report",
        },
    )

    g.add_edge("generate_udir_payload", END)
    g.add_edge("generate_liability_report", END)

    return g.compile(checkpointer=checkpointer)


compiled_graph = build_graph()


async def run_compensation(workflow_id: str, failing_step: dict) -> dict:
    """Entry point called by mcp_proxy as a background task."""
    initial_state = CompensationState(
        workflow_id=workflow_id,
        failing_step=failing_step,
        steps_to_undo=[],
        current_step=None,
        policy_text=None,
        policy_terms=None,
        refund_amount=None,
        compensation_results=[],
        fault_classification=None,
        udir_payload=None,
        liability_report=None,
    )
    result = await compiled_graph.ainvoke(initial_state)
    return result
