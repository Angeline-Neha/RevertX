"""
proxy/mcp_proxy.py
The Aegis MCP Proxy — sits between the Primary Agent and the mock merchant APIs.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Security, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from prometheus_client import make_asgi_app
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
)
import db.client as db

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6380"))
PROXY_API_KEY = os.getenv("PROXY_API_KEY", "test-key-123")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

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

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != PROXY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return api_key

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.run_migrations()
    await db.init_pool()
    yield
    await db.close_pool()

app = FastAPI(title="Aegis MCP Proxy", lifespan=lifespan)
# Mount Prometheus metrics
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class InitWorkflowRequest(BaseModel):
    workflow_id: str
    budget_limit: float


class PayRequest(BaseModel):
    workflow_id: str
    merchant_id: str
    expected: dict[str, Any]
    idempotency_key: str


@app.post("/init_workflow", dependencies=[Depends(verify_api_key)])
async def init_workflow(req: InitWorkflowRequest):
    await db.create_workflow(req.workflow_id, req.budget_limit)
    publish_event(req.workflow_id, "workflow_init", {
        "budget_limit": req.budget_limit,
        "workflow_id": req.workflow_id,
    })
    return {"status": "ok", "workflow_id": req.workflow_id, "budget_limit": req.budget_limit}


@app.post("/pay", dependencies=[Depends(verify_api_key)])
async def pay(req: PayRequest, background_tasks: BackgroundTasks):
    wid = req.workflow_id
    merchant_id = req.merchant_id
    expected_data = req.expected
    idem_key = req.idempotency_key

    async_redis = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    
    # 0. Rate limiting (max 50 requests per minute per workflow_id)
    rl_key = f"rate_limit:{wid}"
    count = await async_redis.incr(rl_key)
    if count == 1:
        await async_redis.expire(rl_key, 60)
    if count > 50:
        await async_redis.aclose()
        return JSONResponse(status_code=429, content={"error": "Too Many Requests"})

    # 1. Idempotency Check (Redis Fast Path)
    cached = await async_redis.get(f"idem:{idem_key}")
    if cached:
        await async_redis.aclose()
        return JSONResponse(content=json.loads(cached))

    # 1b. Idempotency Check (Postgres fallback)
    cached_db = await db.get_idempotency_response(idem_key)
    if cached_db:
        await async_redis.setex(f"idem:{idem_key}", 86400, json.dumps(cached_db))
        await async_redis.aclose()
        return JSONResponse(content=cached_db)

    amount: float = float(expected_data.get("amount", 0))
    payee: str = expected_data.get("payee", MERCHANT_PAYEES.get(merchant_id, "Unknown"))
    item: str = expected_data.get("item", "")
    currency: str = expected_data.get("currency", "INR")

    expected = ExpectedPayment(amount=amount, currency=currency, payee=payee, item=item)

    # 2. Atomic Budget Reservation (replaces check-then-act dict)
    reserved, current_used, current_limit = await db.reserve_budget(wid, amount)
    if not reserved:
        raw_response = {
            "status_code": 403,
            "error_type": "mandate_limit_exceeded",
            "detail": (
                f"Payment of ₹{amount:,.0f} would exceed workflow budget "
                f"(₹{current_used:,.0f} used of ₹{current_limit:,.0f} limit)."
            ),
        }
        failing_entry = TransactionLogEntry(
            workflow_id=wid,
            action_type="payment",
            merchant_id=merchant_id,
            expected=expected,
            actual=ActualPayment(amount=amount, currency=currency, payee=payee, settlement_ref="", status="failed"),
            raw_gateway_response=raw_response,
        )
        
        await db.write_transaction_step(failing_entry.model_dump(), idem_key=idem_key)
        publish_event(wid, "step_written", failing_entry.model_dump())
        
        publish_event(wid, "mandate_exceeded", {
            "step_id": failing_entry.step_id, "amount": amount,
            "budget_used": current_used, "budget_limit": current_limit,
        })
        
        await publish_compensation_request(wid, failing_entry.model_dump())
        await async_redis.aclose()
        
        return JSONResponse(status_code=403, content={
            "error_type": "mandate_limit_exceeded",
            "step_id": failing_entry.step_id,
            **raw_response,
        })

    # 3. Forward to Merchant
    base_url = MERCHANT_URLS.get(merchant_id)
    if not base_url:
        await db.rollback_budget(wid, amount)
        await async_redis.aclose()
        return JSONResponse(status_code=404, content={"error": f"Unknown merchant: {merchant_id}"})

    publish_event(wid, "payment_attempt", {
        "merchant_id": merchant_id, "amount": amount, "payee": payee, "item": item
    })

    try:
        async with httpx.AsyncClient(timeout=8.0) as http_client:
            resp = await http_client.post(f"{base_url}/charge", json={
                "amount": amount, "item": item, "workflow_id": wid
            })
        merchant_data = resp.json()
        status_code = resp.status_code
    except httpx.TimeoutException:
        merchant_data = {"error_type": "timeout"}
        status_code = 408
    except Exception as exc:
        merchant_data = {"error": str(exc)}
        status_code = 503

    raw_response = {**merchant_data, "status_code": status_code}

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

    # 4. Budget Commit/Rollback
    if actual_status == "settled":
        await db.commit_budget(wid, amount, actual_amount)
    else:
        await db.rollback_budget(wid, amount)

    # 5. Write Transaction Step
    actual = ActualPayment(amount=actual_amount, currency=currency, payee=actual_payee, settlement_ref=settlement_ref, status=actual_status)
    entry = TransactionLogEntry(
        workflow_id=wid, action_type="payment", merchant_id=merchant_id,
        expected=expected, actual=actual, raw_gateway_response=raw_response,
    )
    
    await db.write_transaction_step(entry.model_dump(), idem_key=idem_key)
    publish_event(wid, "step_written", entry.model_dump())

    # 6. Reconcile
    seen_refs = get_seen_settlement_refs(wid) - {settlement_ref}
    recon_result: ReconciliationResult = reconcile(entry, previously_seen_refs=seen_refs)

    publish_event(wid, "reconciliation_result", {
        "step_id": entry.step_id, "match": recon_result.match,
        "mismatch_type": recon_result.mismatch_type, "merchant_id": merchant_id,
        "amount": amount, "status": actual_status,
    })

    response_data = {
        "step_id": entry.step_id,
        "merchant_response": merchant_data,
        "reconciliation": recon_result.model_dump(),
        "budget": {"used": current_used + (actual_amount if actual_status == "settled" else 0), "limit": current_limit},
    }

    # Cache idempotency
    await db.save_idempotency_response(idem_key, response_data, 200)
    await async_redis.setex(f"idem:{idem_key}", 86400, json.dumps(response_data))
    await async_redis.aclose()
    
    return response_data


@app.get("/workflow/{workflow_id}", dependencies=[Depends(verify_api_key)])
def get_workflow(workflow_id: str):
    steps = get_workflow_steps(workflow_id)
    return {"workflow_id": workflow_id, "steps": [s.model_dump() for s in steps]}


@app.websocket("/ws/{workflow_id}")
async def websocket_endpoint(websocket: WebSocket, workflow_id: str, token: str = Query(None)):
    if token != PROXY_API_KEY:
        await websocket.close(code=1008)
        return
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


async def publish_compensation_request(wid: str, failing_step: dict):
    import aio_pika
    connection = await aio_pika.connect_robust("amqp://guest:guest@localhost:5673/")
    async with connection:
        channel = await connection.channel()
        await channel.default_exchange.publish(
            aio_pika.Message(body=json.dumps({"workflow_id": wid, "failing_step": failing_step}).encode()),
            routing_key="compensation_requests",
        )
