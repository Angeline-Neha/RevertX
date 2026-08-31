"""
demo_silent_break.py
Live trigger for spec §10 step 7 / Failure Mode 2 — the "Silent Break":

    Merchant C accepts and settles a charge internally, but the response
    the primary agent actually receives looks exactly like a gateway
    timeout — no settlement_ref, no confirmation, nothing the agent's own
    code can trust as a success. The agent's own record of this call says
    "failed". Merchant C's own ledger says "settled". No exception, no
    4xx/5xx the agent's own flow treats as a hard stop — just a quiet
    mismatch between belief and reality.

    This is the gap the spec calls out explicitly: existing agent-payment
    authorization systems (Google AP2, Visa TAP, Mastercard AP4M) verify
    that a payment was *authorized* — they do not verify that the
    *outcome matched intent*. Aegis's reconciliation engine is what
    catches it, and the deterministic fault classifier correctly tags it
    network_fault (based on the raw timeout code in raw_gateway_response),
    which routes to a UDIR-shaped dispute payload rather than an internal
    liability report — the merchant's gateway is at fault here, not the
    agent.

Why this is a separate script instead of a flag on proxy/mcp_proxy.py's
live /pay endpoint: that endpoint is the well-tested, judge-facing
primary path (the subject of Phases 1-3's fixes). Threading a demo-only
"pretend this timed out" flag through its budget/idempotency/
reconciliation logic risks a regression to it for something the spec
itself marks "(optional, if time allows)". Instead, this script performs
the same real steps mcp_proxy.py's /pay does — reserve_budget,
write_transaction_step, reconcile, classify_fault, publish_event,
publish a compensation_requests message — directly, using the exact same
engine functions mcp_proxy.py uses. The dashboard and compensating agent
see a completely real, live run: this is not a synthetic/offline fixture
like test_harness/generate_scenarios.py's equivalent record, which never
touches Redis, Postgres, RabbitMQ, or the dashboard at all.

Usage:
    python demo_silent_break.py [workflow_id]

    Paste the printed workflow_id into the dashboard (or pass one you've
    already got open) to watch it live. Requires the same infrastructure
    as run_demo.py (Postgres, Redis, RabbitMQ) and Merchant C
    (mock_merchants.merchant_c_domain) already running — run_demo.py or
    start_servers.ps1 will have started both.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

import httpx

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import db.client as db
from proxy.schemas import ActualPayment, ExpectedPayment, TransactionLogEntry
from engine.reconciliation import reconcile
from engine.fault_classifier import classify_fault
from state_log.redis_client import get_seen_settlement_refs, publish_event

MERCHANT_C_URL = "http://localhost:8003"
BUDGET_LIMIT = 15000.0
AMOUNT = 12000.0
ITEM = "domain registration"
PAYEE = "Domain Registrar"


async def publish_compensation_request(workflow_id: str, failing_step: dict) -> None:
    """Same RabbitMQ publish proxy/mcp_proxy.py's own helper does — kept
    local here rather than imported, since mcp_proxy's version is a
    module-level function tangled up with that module's FastAPI app
    object, not meant to be imported standalone."""
    import aio_pika

    connection = await aio_pika.connect_robust("amqp://guest:guest@localhost:5673/")
    async with connection:
        channel = await connection.channel()
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps({"workflow_id": workflow_id, "failing_step": failing_step}).encode()
            ),
            routing_key="compensation_requests",
        )


async def main(workflow_id: str | None = None) -> None:
    wid = workflow_id or str(uuid.uuid4())
    await db.init_pool()

    print(f"\n[Silent Break Demo] Workflow: {wid}")
    print(f"[Silent Break Demo] Connect the dashboard to ws://localhost:8000/ws/{wid} to watch live.\n")

    await db.create_workflow(wid, BUDGET_LIMIT)
    publish_event(wid, "workflow_init", {"budget_limit": BUDGET_LIMIT})

    expected = ExpectedPayment(amount=AMOUNT, payee=PAYEE, item=ITEM)
    publish_event(wid, "payment_attempt", {
        "merchant_id": "merchant_c", "amount": AMOUNT, "payee": PAYEE, "item": ITEM,
    })

    print(f"[Primary Agent] Paying Merchant C ({ITEM}) — ₹{AMOUNT:,.0f} ...")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{MERCHANT_C_URL}/charge", json={
            "amount": AMOUNT, "item": ITEM, "workflow_id": wid,
            "simulate_silent_timeout": True,
        })
    print(f"[Primary Agent] Agent-visible response: HTTP {resp.status_code} — {resp.json()}")
    print("[Primary Agent] From here, this payment looks FAILED. No settlement_ref, no confirmation.\n")

    # --- This is the actual "Aegis catches it" moment ---
    print("[Aegis] Reconciliation: checking Merchant C's settlement ledger directly...")
    async with httpx.AsyncClient(timeout=15.0) as client:
        ledger_resp = await client.get(f"{MERCHANT_C_URL}/internal_ledger/{wid}")
    charges = ledger_resp.json().get("charges", [])
    if not charges:
        print("[Aegis] No ground-truth charge found on Merchant C's ledger for this workflow.")
        print("[Aegis] (Is mock_merchants.merchant_c_domain actually running on :8003?) Aborting.")
        return

    truth = charges[0]
    print(
        f"[Aegis] MISMATCH FOUND: Merchant C actually settled "
        f"{truth['settlement_ref']} for ₹{truth['amount']:,.0f} — "
        f"the agent's own record says this failed.\n"
    )

    reserved, used, limit = await db.reserve_budget(wid, AMOUNT)
    if not reserved:
        print(f"[Aegis] Budget reservation unexpectedly failed (used={used}, limit={limit}). Aborting.")
        return

    actual = ActualPayment(
        amount=truth["amount"], payee=truth["payee"],
        settlement_ref=truth["settlement_ref"], status="settled",
    )
    raw_gateway_response = {
        "status_code": 504,
        "error_type": "timeout",
        "detail": "Gateway timeout — no confirmation received from processor.",
    }
    entry = TransactionLogEntry(
        workflow_id=wid, action_type="payment", merchant_id="merchant_c",
        expected=expected, actual=actual, raw_gateway_response=raw_gateway_response,
    )

    await db.write_transaction_step(entry.model_dump())
    publish_event(wid, "step_written", entry.model_dump())

    seen_refs = get_seen_settlement_refs(wid) - {actual.settlement_ref}
    recon_result = reconcile(entry, previously_seen_refs=seen_refs)
    publish_event(wid, "reconciliation_result", {
        "step_id": entry.step_id, "match": recon_result.match,
        "mismatch_type": recon_result.mismatch_type, "merchant_id": "merchant_c",
        "amount": AMOUNT, "status": "settled",
    })
    print(f"[Aegis] Reconciliation result: match={recon_result.match}, mismatch_type={recon_result.mismatch_type}")
    if recon_result.match or recon_result.mismatch_type != "hard_error":
        print(
            "[Aegis] UNEXPECTED: reconciliation didn't flag this as a hard_error "
            "mismatch. Something about the ground-truth wiring above doesn't "
            "match what reconcile() checks for — this demo is supposed to prove "
            "the deterministic engine catches this class of failure, so treat "
            "this as a real bug, not a quirky demo outcome."
        )
        return

    classification = classify_fault(entry.step_id, raw_gateway_response)
    print(f"[Aegis] Fault classification: {classification.fault_type} ({classification.classification_basis})")
    if classification.fault_type != "network_fault":
        print(
            f"[Aegis] UNEXPECTED: expected network_fault (raw timeout code), got "
            f"{classification.fault_type}. Same as above — this is a real bug if "
            f"it happens, not an acceptable demo variation."
        )
        return

    print("\n[Aegis] Triggering compensating agent — expect a UDIR dispute payload, NOT a liability report")
    print("        (Merchant C's gateway is at fault here; the agent did nothing wrong).\n")
    await publish_compensation_request(wid, entry.model_dump())

    print(f"[Silent Break Demo] Done. Watch ws://localhost:8000/ws/{wid} for the UDIR payload.")
    await db.close_pool()


if __name__ == "__main__":
    arg_workflow_id = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(main(arg_workflow_id))
