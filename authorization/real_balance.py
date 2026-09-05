"""
authorization/real_balance.py
Third, distinct pre-payment control — separate from both the Agent Wallet
(authorization/wallet.py, the agent's own configured authority) and Policy
(authorization/policy.py, rule compliance). This one asks a different
question entirely: does the money actually exist in the real RazorpayX
account right now? Wallet and Policy can both pass while this still blocks
— e.g. an agent well within its ₹25,000/txn authority can still exceed
what's genuinely sitting in the account.

Only meaningful for the real-payout merchant ids (authorization/trace.py's
REAL_PAYOUT_MERCHANT_IDS): mock merchants never touch RazorpayX, so
checking a real balance against a mock charge would be a category error,
not a safety check. Callers should skip this entirely for any other
merchant_id.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from razorpayx.client import get_available_balance

# Insufficient Funds demo preset — merchant_rzp_insufficient CAN be forced
# to block here regardless of the account's actual balance (same
# reproducibility reasoning as DOWNSTREAM_FORCE_FAIL in
# mock_merchants/downstream_service.py and RZP_PENDING_DEMO_FORCE), but
# defaults to OFF: out of the box this check reflects your real RazorpayX
# balance, not a hardcoded "always fails" result. Still fetches the real
# balance for an honest event payload either way (available_balance shown
# to the dashboard is real); this flag only controls whether the pass/fail
# classification also honors that real number for this one merchant_id.
# Set to true only if you specifically want a guaranteed-reproducible
# insufficient-funds demo regardless of what's actually in the account.
REAL_BALANCE_DEMO_FORCE_INSUFFICIENT = os.getenv(
    "REAL_BALANCE_DEMO_FORCE_INSUFFICIENT", "false"
).lower() == "true"
FORCE_INSUFFICIENT_MERCHANT_ID = "merchant_rzp_insufficient"


@dataclass
class RealBalanceCheckResult:
    ok: bool
    available: float
    detail: str


async def check_real_balance(amount: float, merchant_id: str | None = None) -> RealBalanceCheckResult:
    available = await get_available_balance()
    forced = (
        merchant_id == FORCE_INSUFFICIENT_MERCHANT_ID
        and REAL_BALANCE_DEMO_FORCE_INSUFFICIENT
    )
    if forced or amount > available:
        detail = (
            f"amount ₹{amount:,.2f} exceeds real RazorpayX available balance ₹{available:,.2f}"
            if not forced else
            f"amount ₹{amount:,.2f} treated as exceeding real RazorpayX available balance "
            f"₹{available:,.2f} (demo-forced for {merchant_id})"
        )
        return RealBalanceCheckResult(ok=False, available=available, detail=detail)
    return RealBalanceCheckResult(
        ok=True, available=available,
        detail=f"within real available balance ₹{available:,.2f}",
    )
