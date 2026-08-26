"""
proxy/mcp_proxy.py
The Aegis MCP Proxy — sits between the Primary Agent and the mock merchant APIs.

Endpoints:
  POST /pay            — intercepts payments, writes log, runs reconciliation
  POST /init_workflow  — declare a workflow with its budget limit
  GET  /workflow/{id}  — get all steps for a workflow
  WS   /ws/{id}        — WebSocket: live event stream via Redis pub/sub
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx
import redis.asyncio as aioredis
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv()

from engine.reconciliation import reconcile
from proxy.schemas import (
    ActualPayment,
    ExpectedPayment,
    ReconciliationResult,
    TransactionLogEntry,
)
from state_log.redis_client import (
    get_seen_settlement_refs,
    get_workflow_steps,
    publish_event,
    write_step,
)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6380"))

MERCHANT_URLS: dict[str, str] = {
    "merchant_a": "http://localhost:8001",
    "merchant_b": "http://localhost:8002",
    "merchant_c": "http://localhost:8003",
}

MERCHANT_PAYEES: dict[str, str] = {
    "merchant_a": "CRM Corp",
    "merchant_b": "Grand Hotel",
    "merchant_c": "Domain Registrar",
}

# In-memory budget tracking: {workflow_id: {"limit": float, "used": float}}
_budgets: dict[str, dict[str, float]] = {}

app = FastAPI(title="Aegis MCP Proxy")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class InitWorkflowRequest(BaseModel):
    workflow_id: str
    budget_limit: float


class PayRequest(BaseModel):
    workflow_id: str
    merchant_id: str
    expected: dict[str, Any]  # ExpectedPayment fields


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/init_workflow")
def init_workflow(req: InitWorkflowRequest):
    _budgets[req.workflow_id] = {"limit": req.budget_limit, "used": 0.0}
    publish_event(req.workflow_id, "workflow_init", {
        "budget_limit": req.budget_limit,
        "workflow_id": req.workflow_id,
    })
    return {"status": "ok", "workflow_id": req.workflow_id, "budget_limit": req.budget_limit}


@app.post("/pay")
async def pay(req: PayRequest, background_tasks: BackgroundTasks):
    wid = req.workflow_id
    merchant_id = req.merchant_id
    expected_data = req.expected

    amount: float = float(expected_data.get("amount", 0))
    payee: str = expected_data.get("payee", MERCHANT_PAYEES.get(merchant_id, "Unknown"))
    item: str = expected_data.get("item", "")
    currency: str = expected_data.get("currency", "INR")

    expected = ExpectedPayment(amount=amount, currency=currency, payee=payee, item=item)

    # ---- Budget check (triggers hard failure = "loud break") ----
    budget = _budgets.get(wid, {"limit": float("inf"), "used": 0.0})
    if budget["used"] + amount > budget["limit"]:
        raw_response = {
            "status_code": 403,
            "error_type": "mandate_limit_exceeded",
            "detail": (
                f"Payment of ₹{amount:,.0f} would exceed workflow budget "
                f"(₹{budget['used']:,.0f} used of ₹{budget['limit']:,.0f} limit)."
            ),
        }
        failing_entry = TransactionLogEntry(
            workflow_id=wid,
            action_type="payment",
            merchant_id=merchant_id,
            expected=expected,
            actual=ActualPayment(
                amount=amount,
                currency=currency,
                payee=payee,
                settlement_ref="",
                status="failed",
            ),
            raw_gateway_response=raw_response,
        )
        write_step(failing_entry)
        publish_event(wid, "mandate_exceeded", {
            "step_id": failing_entry.step_id,
            "amount": amount,
            "budget_used": budget["used"],
            "budget_limit": budget["limit"],
        })

        # Auto-trigger compensating agent in background
        background_tasks.add_task(_trigger_compensation, wid, failing_entry.model_dump())

        return JSONResponse(status_code=403, content={
            "error_type": "mandate_limit_exceeded",
            "step_id": failing_entry.step_id,
            **raw_response,
        })

    # ---- Forward to merchant ----
    base_url = MERCHANT_URLS.get(merchant_id)
    if not base_url:
        return JSONResponse(status_code=404, content={"error": f"Unknown merchant: {merchant_id}"})

    publish_event(wid, "payment_attempt", {
        "merchant_id": merchant_id, "amount": amount, "payee": payee, "item": item
    })

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(f"{base_url}/charge", json={
                "amount": amount, "item": item, "workflow_id": wid
            })
        merchant_data = resp.json()
        status_code = resp.status_code
    except httpx.TimeoutException:
        merchant_data = {}
        status_code = 408
        merchant_data["error_type"] = "timeout"
    except Exception as exc:
        merchant_data = {"error": str(exc)}
        status_code = 503

    raw_response = {**merchant_data, "status_code": status_code}

    # ---- Build actual payment record ----
    if status_code == 200:
        actual_status = merchant_data.get("status", "failed")
        actual_amount = float(merchant_data.get("amount", amount))
        actual_payee = merchant_data.get("payee", payee)
        settlement_ref = merchant_data.get("settlement_ref", "")
    else:
        actual_status = "failed"
        actual_amount = amount
        actual_payee = payee
        settlement_ref = ""

    actual = ActualPayment(
        amount=actual_amount,
        currency=currency,
        payee=actual_payee,
        settlement_ref=settlement_ref,
        status=actual_status,
    )

    entry = TransactionLogEntry(
        workflow_id=wid,
        action_type="payment",
        merchant_id=merchant_id,
        expected=expected,
        actual=actual,
        raw_gateway_response=raw_response,
    )
    write_step(entry)

    # ---- Update budget only on success ----
    if actual_status == "settled":
        _budgets.setdefault(wid, {"limit": float("inf"), "used": 0.0})
        _budgets[wid]["used"] += actual_amount

    # ---- Reconcile ----
    seen_refs = get_seen_settlement_refs(wid) - {settlement_ref}
    recon_result: ReconciliationResult = reconcile(entry, previously_seen_refs=seen_refs)

    publish_event(wid, "reconciliation_result", {
        "step_id": entry.step_id,
        "match": recon_result.match,
        "mismatch_type": recon_result.mismatch_type,
        "merchant_id": merchant_id,
        "amount": amount,
        "status": actual_status,
    })

    return {
        "step_id": entry.step_id,
        "merchant_response": merchant_data,
        "reconciliation": recon_result.model_dump(),
        "budget": {"used": _budgets.get(wid, {}).get("used", 0), "limit": _budgets.get(wid, {}).get("limit", 0)},
    }


@app.get("/workflow/{workflow_id}")
def get_workflow(workflow_id: str):
    steps = get_workflow_steps(workflow_id)
    return {"workflow_id": workflow_id, "steps": [s.model_dump() for s in steps]}


# ---------------------------------------------------------------------------
# WebSocket — live event stream (no polling)
# ---------------------------------------------------------------------------

@app.websocket("/ws/{workflow_id}")
async def websocket_endpoint(websocket: WebSocket, workflow_id: str):
    await websocket.accept()
    async_redis = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = async_redis.pubsub()
    await pubsub.subscribe(f"workflow:{workflow_id}:events")

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(f"workflow:{workflow_id}:events")
        await async_redis.aclose()


# ---------------------------------------------------------------------------
# Background task: trigger compensating agent
# ---------------------------------------------------------------------------

async def _trigger_compensation(workflow_id: str, failing_step: dict) -> None:
    from compensating_agent.graph import run_compensation

    publish_event(workflow_id, "compensation_started", {
        "workflow_id": workflow_id,
        "trigger": "mandate_limit_exceeded",
    })
    try:
        result = await run_compensation(workflow_id, failing_step)
        publish_event(workflow_id, "compensation_complete", {
            "workflow_id": workflow_id,
            "has_udir": result.get("udir_payload") is not None,
            "has_liability_report": result.get("liability_report") is not None,
        })
    except Exception as exc:
        publish_event(workflow_id, "compensation_error", {"error": str(exc)})
