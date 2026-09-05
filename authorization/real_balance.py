"""
authorization/real_balance.py
Third, distinct pre-payment control — separate from both the Agent Wallet
(authorization/wallet.py, the agent's own configured authority) and Policy
(authorization/policy.py, rule compliance). This one asks a different
question entirely: does the money actually exist in the real RazorpayX
account right now? Wallet and Policy can both pass while this still blocks
— e.g. an agent well within its ₹25,000/txn authority can still exceed
what's genuinely sitting in the account.

Only meaningful for merchant_id == "merchant_rzp": mock merchants never
touch RazorpayX, so checking a real balance against a mock charge would be
a category error, not a safety check. Callers should skip this entirely
for any other merchant_id.
"""
from __future__ import annotations

from dataclasses import dataclass

from razorpayx.client import get_available_balance


@dataclass
class RealBalanceCheckResult:
    ok: bool
    available: float
    detail: str


async def check_real_balance(amount: float) -> RealBalanceCheckResult:
    available = await get_available_balance()
    if amount > available:
        return RealBalanceCheckResult(
            ok=False, available=available,
            detail=f"amount ₹{amount:,.2f} exceeds real RazorpayX available balance ₹{available:,.2f}",
        )
    return RealBalanceCheckResult(
        ok=True, available=available,
        detail=f"within real available balance ₹{available:,.2f}",
    )
