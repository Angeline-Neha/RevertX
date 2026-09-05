"""
authorization/wallet.py
The AI agent's financial authority — distinct from the underlying RazorpayX
account balance. The RazorpayX balance is "how much money exists"; the
Agent Wallet is "how much of it this agent is allowed to move, and how much
it already has."

Backed by Postgres (agent_wallet table) so limits/spend survive restarts.
Call reset_daily_if_needed() before each check — cheap, no cron required.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import db.client as db


@dataclass
class WalletState:
    agent_id: str
    per_txn_limit: float
    daily_limit: float
    spent_today: float
    last_reset: date

    @property
    def remaining_today(self) -> float:
        return max(self.daily_limit - self.spent_today, 0.0)

    def can_spend(self, amount: float) -> tuple[bool, str | None]:
        if amount > self.per_txn_limit:
            return False, f"amount ₹{amount} exceeds per-transaction limit ₹{self.per_txn_limit}"
        if amount > self.remaining_today:
            return False, f"amount ₹{amount} exceeds remaining daily authority ₹{self.remaining_today}"
        return True, None


async def get_wallet(agent_id: str) -> WalletState:
    row = await db.fetch_wallet(agent_id)
    ws = WalletState(
        agent_id=agent_id,
        per_txn_limit=row["per_txn_limit"],
        daily_limit=row["daily_limit"],
        spent_today=row["spent_today"],
        last_reset=row["last_reset"],
    )
    if ws.last_reset != date.today():
        ws.spent_today = 0.0
        ws.last_reset = date.today()
        await db.reset_wallet_spend(agent_id, ws.last_reset)
    return ws


async def record_spend(agent_id: str, amount: float) -> None:
    await db.increment_wallet_spend(agent_id, amount)
