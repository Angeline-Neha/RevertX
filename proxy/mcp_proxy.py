"""
proxy/mcp_proxy.py
The Aegis MCP Proxy — sits between the Primary Agent and the mock merchant APIs.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
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
from mock_merchants.registry import MERCHANT_URLS, MERCHANT_PAYEES
from authorization.trace import authorize
from authorization.wallet import get_wallet
from authorization.policy import DEFAULT_POLICY_CONFIG
from razorpayx.client import create_payout, poll_payout, reverse_payout
from mock_merchants.downstream_service import confirm_fulfillment

RZP_DEMO_FUND_ACCOUNT_ID = os.getenv("RZP_DEMO_FUND_ACCOUNT_ID")
RZP_SELF_FUND_ACCOUNT_ID = os.getenv("RZP_SELF_FUND_ACCOUNT_ID")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6380"))
PROXY_API_KEY = os.getenv("PROXY_API_KEY", "test-key-123")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

# MERCHANT_URLS / MERCHANT_PAYEES now come from mock_merchants/registry.py —
# the single source of truth shared with compensating_agent/graph.py and the
# process-launch scripts. Do not redeclare them here.

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

# Phase 9.1 — before/after toggle. When False, mandate_exceeded still rejects
# the payment (budget enforcement is always on) but does NOT trigger the
# compensation worker, so the money stays stranded — which is exactly the
# "without Aegis" state the demo toggle shows before flipping it back on.
# Lives in process memory (not DB) because it's a demo-time mode switch, not
# production config — it resets on every proxy restart, which is the right
# default for a live demo where you want a clean slate each time.
_aegis_enabled: bool = True


class AegisModeRequest(BaseModel):
    enabled: bool


@app.post("/aegis_mode", dependencies=[Depends(verify_api_key)])
def set_aegis_mode(req: AegisModeRequest):
    """Phase 9.1 — flips the compensation trigger on/off without restarting."""
    global _aegis_enabled
    _aegis_enabled = req.enabled
    return {"aegis_enabled": _aegis_enabled}

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

    # 1c. Agent Wallet + policy authorization — runs BEFORE the workflow
    # budget check below. Distinct concern: reserve_budget() enforces this
    # *workflow's* goal-scoped mandate; authorize() enforces the agent's
    # own standing financial authority (daily/per-txn limits, recipient/
    # category policy) independent of any single workflow. Single-agent
    # system today, so agent_id/category are fixed defaults — parameterize
    # PayRequest if/when multiple agents exist.
    auth_result = await authorize(
        workflow_id=wid, agent_id="primary_agent", amount=amount,
        category=expected_data.get("category", "vendor_payment"),
        recipient_id=payee, merchant_id=merchant_id,
    )
    if auth_result["decision"] == "BLOCK":
        await async_redis.aclose()
        return JSONResponse(status_code=403, content={
            "status_code": 403,
            "error_type": "authorization_blocked",
            "trace_id": auth_result["trace_id"],
            "detail": auth_result["steps"][-1]["detail"],
        })

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
            "merchant_id": merchant_id, "payee": payee, "item": item,
            "budget_used": current_used, "budget_limit": current_limit,
        })
        
        await publish_compensation_request(wid, failing_entry.model_dump()) if _aegis_enabled else None
        # Publish an event so the dashboard toggle can show the "Aegis OFF — money stranded"
        # state visually, rather than just having nothing happen after the 403.
        if not _aegis_enabled:
            publish_event(wid, "aegis_disabled_no_compensation", {
                "workflow_id": wid,
                "message": "Aegis is OFF — compensation not triggered. Money is stranded.",
            })
        await async_redis.aclose()
        
        return JSONResponse(status_code=403, content={
            "error_type": "mandate_limit_exceeded",
            "step_id": failing_entry.step_id,
            **raw_response,
        })

    # 3. Forward to Merchant (real RazorpayX payout for merchant_rzp, mock HTTP otherwise)
    publish_event(wid, "payment_attempt", {
        "merchant_id": merchant_id, "amount": amount, "payee": payee, "item": item
    })

    if merchant_id == "merchant_rzp":
        # Real RazorpayX Test Mode payout — same response shape (status/
        # amount/payee/settlement_ref) as a mock merchant's /charge, so
        # every downstream step (budget commit, reconciliation, dashboard
        # events) works unchanged regardless of which path ran.
        try:
            payout = await create_payout(
                fund_account_id=RZP_DEMO_FUND_ACCOUNT_ID,
                amount_paise=int(amount * 100),
                purpose="payout",
                reference_id=idem_key,
                narration=item[:30] if item else "Aegis payout",
            )
            payout_id = payout.get("id", "")
            # Phase 2 — don't trust create_payout()'s own initial response;
            # queued/processing are non-terminal (queued specifically means
            # "not processed, nothing sent yet"), not evidence of a
            # completed payout. Re-poll until processed/reversed/rejected/
            # cancelled/failed, or until the poll window (RZP_POLL_* env
            # vars, default 2s x 3 = 6s) is exhausted.
            poll_result = await poll_payout(payout_id) if payout_id else None
            rzp_status = poll_result.razorpay_status if poll_result else payout.get("status", "")

            if poll_result and poll_result.classification == "processed":
                merchant_status = "settled"
            elif poll_result and poll_result.classification == "failed":
                merchant_status = "failed"
            elif poll_result and poll_result.classification == "non_terminal":
                # Genuinely unknown, not a failure — do NOT mark settled or
                # failed. See Preset 2's design: this should route to
                # human_escalation_required (held for later resolution),
                # not the compensation saga, which assumes a known failure.
                # That routing is Phase 5 — for now this is surfaced as its
                # own status so nothing downstream silently treats it as
                # either a success or a known failure.
                merchant_status = "pending"
            else:
                # payout_id missing from RazorpayX's own create response —
                # can't poll at all, treat as failed rather than guess.
                merchant_status = "failed"

            merchant_data = {
                "status": merchant_status,
                "amount": payout.get("amount", 0) / 100.0,
                "payee": payee,
                "settlement_ref": payout_id,
                "razorpay_status": rzp_status,
                "utr": payout.get("utr"),
                "poll_attempts": poll_result.attempts_made if poll_result else 0,
            }
            status_code = 200

            if merchant_status == "pending":
                publish_event(wid, "payout_unconfirmed", {
                    "merchant_id": merchant_id, "payee": payee, "amount": amount,
                    "settlement_ref": payout_id, "razorpay_status": rzp_status,
                    "poll_attempts": poll_result.attempts_made,
                    "reason": (
                        f"Payout {payout_id} still '{rzp_status}' after "
                        f"{poll_result.attempts_made} poll(s) — financial "
                        "outcome unconfirmed, holding before any recovery action."
                    ),
                })
            elif merchant_status == "settled":
                # Phase 4 (Preset 3 flagship) — the payout is CONFIRMED
                # processed (not uncertain), so this is a known failure if
                # the downstream fulfillment/booking never happens, not an
                # ambiguous one. Auto-reversal is safe here specifically
                # because there's no double-spend risk: we know for
                # certain the money moved and for certain the thing it
                # paid for didn't.
                fulfillment = await confirm_fulfillment(merchant_id, payout_id, amount)
                if not fulfillment["confirmed"]:
                    publish_event(wid, "downstream_fulfillment_failed", {
                        "merchant_id": merchant_id, "payee": payee, "amount": amount,
                        "settlement_ref": payout_id, "utr": payout.get("utr"),
                        "reason": fulfillment["reason"],
                    })
                    if _aegis_enabled:
                        if not RZP_SELF_FUND_ACCOUNT_ID:
                            publish_event(wid, "compensation_error", {
                                "workflow_id": wid,
                                "error": (
                                    "RZP_SELF_FUND_ACCOUNT_ID not set — cannot fire a real "
                                    "reversal payout. Run `python -m razorpayx.provision --self` "
                                    "and add it to .env."
                                ),
                            })
                        else:
                            try:
                                reversal = await reverse_payout(
                                    original_payout_id=payout_id,
                                    self_fund_account_id=RZP_SELF_FUND_ACCOUNT_ID,
                                    amount_paise=int(amount * 100),
                                )
                                publish_event(wid, "final_output", {
                                    "type": "real_payout_reversed",
                                    "label": "Downstream failure — real reversal payout sent",
                                    "payload": {
                                        "merchant_id": merchant_id, "payee": payee, "amount": amount,
                                        "original_settlement_ref": payout_id,
                                        "original_utr": payout.get("utr"),
                                        "downstream_failure_reason": fulfillment["reason"],
                                        "reversal_settlement_ref": reversal.get("id", ""),
                                        "reversal_utr": reversal.get("utr"),
                                        "reversal_status": reversal.get("status", ""),
                                    },
                                })
                            except Exception as exc:
                                publish_event(wid, "compensation_error", {
                                    "workflow_id": wid, "error": f"Reversal payout failed: {exc}",
                                })
                    else:
                        # Phase 9.1's existing OFF-state signal, reused —
                        # same "money stranded, nothing recovers it"
                        # visual as the mandate_exceeded path, now also
                        # covering a real-money downstream failure.
                        publish_event(wid, "aegis_disabled_no_compensation", {
                            "workflow_id": wid,
                            "message": (
                                f"Aegis is OFF — real payout {payout_id} (₹{amount:,.2f}) "
                                "succeeded but its downstream fulfillment failed. "
                                "No reversal triggered. Money is stranded."
                            ),
                        })
        except Exception as exc:
            merchant_data = {"error": str(exc)}
            status_code = 502
    else:
        base_url = MERCHANT_URLS.get(merchant_id)
        if not base_url:
            await db.rollback_budget(wid, amount)
            await async_redis.aclose()
            return JSONResponse(status_code=404, content={"error": f"Unknown merchant: {merchant_id}"})

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

    final_used, final_limit = await db.get_budget_state(wid)
    response_data = {
        "step_id": entry.step_id,
        "merchant_response": merchant_data,
        "reconciliation": recon_result.model_dump(),
        "budget": {"used": final_used, "limit": final_limit},
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


@app.get("/workflows", dependencies=[Depends(verify_api_key)])
async def list_workflows(limit: int = 20):
    """Recent workflows for the dashboard's workflow picker (Phase 5.2) —
    lets the dashboard offer a click-to-connect list instead of requiring a
    UUID pasted from the demo client's terminal output."""
    limit = max(1, min(limit, 100))
    workflows = await db.list_recent_workflows(limit=limit)
    return {"workflows": workflows}


@app.get("/session_metrics")
async def session_metrics():
    """Phase 9.2 — live running counter: total ₹ auto-recovered + disputes resolved.
    No auth required so the dashboard can poll it without the API key header from
    a plain fetch() — the numbers are non-sensitive aggregate metrics."""
    return await db.get_session_metrics()


@app.get("/aegis_status")
def aegis_status():
    """Phase 9.1 — lets the dashboard read the current Aegis on/off state on connect."""
    return {"aegis_enabled": _aegis_enabled}


@app.get("/wallet/{agent_id}")
async def get_wallet_state(agent_id: str):
    """Agent Wallet panel — live per-txn/daily limits + spend. No auth
    required (matches /session_metrics, /aegis_status): plain fetch() from
    the dashboard, non-sensitive to this single-agent demo. Same
    get_wallet() authorize() itself calls, so the panel can never show a
    number that differs from what's actually enforced."""
    wallet = await get_wallet(agent_id)
    return {
        "agent_id": wallet.agent_id,
        "per_txn_limit": wallet.per_txn_limit,
        "daily_limit": wallet.daily_limit,
        "spent_today": wallet.spent_today,
        "remaining_today": wallet.remaining_today,
    }


@app.get("/policy")
def get_policy():
    """Policy panel — categories/denylist/allowlist/approval threshold.
    Reads DEFAULT_POLICY_CONFIG, the same shared instance authorize() checks
    against, so this can never show a rule that isn't actually enforced.
    Static today (policy has no live-changing state), unlike /wallet above."""
    config = DEFAULT_POLICY_CONFIG
    return {
        "allowed_categories": sorted(config.allowed_categories),
        "recipient_allowlist": sorted(config.recipient_allowlist) if config.recipient_allowlist is not None else None,
        "recipient_denylist": sorted(config.recipient_denylist),
        "human_approval_threshold": config.human_approval_threshold,
    }


# Repo root — same resolution primary_agent/procurement_agent.py uses for
# its own sys.path fix, needed here so the subprocess below (launched with
# cwd=_REPO_ROOT) can resolve `-m primary_agent.procurement_agent` and
# `import db` regardless of what directory uvicorn itself was started from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TriggerRunRequest(BaseModel):
    # None → the fixed 3-step demo script (run_procurement, unchanged from
    # before Phase 7). Set → primary_agent/planner.py's goal-driven path
    # (run_procurement_with_plan).
    goal: Optional[str] = None
    budget_limit: float = 35000.0


@app.post("/trigger_run", dependencies=[Depends(verify_api_key)])
def trigger_run(req: TriggerRunRequest):
    """
    Phase 8.1 — launches a fresh procurement run without the demo operator
    touching a terminal. Generates the workflow_id here (rather than
    letting procurement_agent.py generate its own) specifically so it can
    be handed back to the caller immediately, before the run has actually
    finished — the dashboard uses this to auto-connect via Phase 5.2's
    existing connect-by-id flow while the run is still in progress, which
    is what makes the live event stream visible from the very first
    payment instead of only after the whole script completes.

    Runs procurement_agent.py as a genuinely separate OS process (the same
    way run_demo.py and start_and_test.py already launch it), not as an
    in-process function call — that script does process-global things at
    import time (reconfiguring sys.stdout's encoding, mutating sys.path)
    that are fine for a standalone script but not something this proxy
    process's own stdout/path should inherit just because an HTTP request
    came in.
    """
    wid = str(uuid.uuid4())
    args = [sys.executable, "-X", "utf8", "-m", "primary_agent.procurement_agent", wid]
    if req.goal:
        args += ["--goal", req.goal, "--budget", str(req.budget_limit)]
    logs_dir = os.path.join(_REPO_ROOT, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f"agent_{wid[:8]}.log")
    with open(log_path, "w", encoding="utf-8") as lf:
        subprocess.Popen(args, cwd=_REPO_ROOT, stdout=lf, stderr=lf)
    return {"workflow_id": wid, "log": f"logs/agent_{wid[:8]}.log"}


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
