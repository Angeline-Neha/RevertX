"""
authorization/trace.py
Structured pre-payment decision trace — sibling to compensation_trace, but
for the authorization side. Every step is a named, deterministic check
(no LLM, no hidden reasoning) so the whole thing is auditable: input, rule,
result, per step, plus a final ALLOW/BLOCK.

Published as event_type="authorization_trace" on the same
workflow:{workflow_id}:events channel the dashboard already listens on.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from authorization.wallet import get_wallet, record_spend
from authorization.policy import PolicyConfig, evaluate, all_passed
from state_log.redis_client import publish_event


async def authorize(
    *, workflow_id: str, agent_id: str, amount: float, category: str,
    recipient_id: str, policy_config: PolicyConfig | None = None,
) -> dict:
    """Runs the full authorization pipeline and returns
    {"decision": "ALLOW"|"BLOCK", "trace_id": ..., "steps": [...]}.
    Publishes each step as it happens; caller (proxy) still owns actually
    calling RazorpayX — this function only decides yes/no."""
    trace_id = str(uuid.uuid4())
    config = policy_config or PolicyConfig()
    steps = []

    def emit(step: str, status: str, detail: str, extra: dict | None = None):
        entry = {
            "trace_id": trace_id, "step": step, "status": status,
            "detail": detail, "timestamp": datetime.now(timezone.utc).isoformat(),
            **(extra or {}),
        }
        steps.append(entry)
        publish_event(workflow_id, "authorization_trace", entry)

    emit("parse_intent", "pass", f"amount=₹{amount}, category={category}, recipient={recipient_id}")

    wallet = await get_wallet(agent_id)
    can_spend, reason = wallet.can_spend(amount)
    emit(
        "check_wallet_authority", "pass" if can_spend else "fail",
        reason or f"within limits (per-txn ≤₹{wallet.per_txn_limit}, remaining today ₹{wallet.remaining_today})",
        {"per_txn_limit": wallet.per_txn_limit, "daily_limit": wallet.daily_limit,
         "spent_today": wallet.spent_today, "remaining_today": wallet.remaining_today},
    )
    if not can_spend:
        # Structured, not just a prose reason — dashboard renders these as
        # a clean "requested vs authority" banner rather than parsing text.
        emit("final_decision", "block", reason, {
            "block_source": "wallet",
            "requested_amount": amount,
            "per_txn_limit": wallet.per_txn_limit,
            "daily_limit": wallet.daily_limit,
            "remaining_today": wallet.remaining_today,
            "financial_action": "payout not attempted",
        })
        return {"decision": "BLOCK", "trace_id": trace_id, "steps": steps}

    policy_results = evaluate(amount=amount, category=category, recipient_id=recipient_id, config=config)
    for r in policy_results:
        emit(r.check, "pass" if r.passed else "fail", r.detail)

    if not all_passed(policy_results):
        failed_check = next(r for r in policy_results if not r.passed)
        emit("final_decision", "block", failed_check.detail, {
            "block_source": "policy",
            "failed_check": failed_check.check,
            "requested_amount": amount,
            "financial_action": "payout not attempted",
        })
        return {"decision": "BLOCK", "trace_id": trace_id, "steps": steps}

    emit("final_decision", "allow", "all checks passed")
    await record_spend(agent_id, amount)
    return {"decision": "ALLOW", "trace_id": trace_id, "steps": steps}
