"""
razorpayx/client.py
Thin async wrapper over RazorpayX's Payouts API (test mode).
Auth: HTTP Basic (Key ID / Key Secret) — same as Razorpay Payments.
Docs: https://razorpay.com/docs/x/
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

load_dotenv()

RZP_KEY_ID = os.getenv("RZP_KEY_ID")
RZP_KEY_SECRET = os.getenv("RZP_KEY_SECRET")
RZP_ACCOUNT_NUMBER = os.getenv("RZP_ACCOUNT_NUMBER")  # RazorpayX virtual account no.
BASE_URL = "https://api.razorpay.com/v1"

# Phase 2 — poll cadence for post-payout status classification. Configurable
# via env (not hardcoded) so the demo-day window (default 2s x 3 = 6s) can be
# tuned without a code change. See poll_payout() below.
RZP_POLL_INTERVAL_SECONDS = float(os.getenv("RZP_POLL_INTERVAL_SECONDS", "2"))
RZP_POLL_MAX_ATTEMPTS = int(os.getenv("RZP_POLL_MAX_ATTEMPTS", "3"))

# RazorpayX payout status buckets (per Razorpay docs, confirmed in the
# Preset-design discussion): "queued" specifically means insufficient
# balance / not yet processed — nothing has been sent. "processing" means
# in flight, outcome unknown. "processed" is the only real success.
# "reversed"/"rejected"/"cancelled"/"failed" all mean the money didn't move
# (or moved and was reversed automatically by Razorpay on failure).
_TERMINAL_SUCCESS = {"processed"}
_TERMINAL_FAILURE = {"reversed", "rejected", "cancelled", "failed"}
_NON_TERMINAL = {"queued", "processing"}

if not RZP_KEY_ID or not RZP_KEY_SECRET:
    raise RuntimeError(
        "Set RZP_KEY_ID and RZP_KEY_SECRET in .env (never hardcode/commit them)."
    )

_auth = (RZP_KEY_ID, RZP_KEY_SECRET)


async def get_balance() -> dict:
    """RazorpayX current-account balance (test mode). NOT /v1/balance —
    that endpoint returns the Payments 'primary' balance and ignores
    account_number, which is why it showed 0 even after adding test funds."""
    async with httpx.AsyncClient(auth=_auth) as client:
        resp = await client.get(f"{BASE_URL}/banking_balances")
        resp.raise_for_status()
        return resp.json()


async def get_available_balance() -> float:
    """Real, current RazorpayX available balance in rupees — distinct from
    the Agent Wallet's per-txn/daily limits, which are just numbers in our
    own Postgres. Used by authorization/real_balance.py's pre-payment check
    so a BLOCK can be based on what money actually exists, not just what
    the agent is internally allowed to move.

    Response field name varies by account type/API version (confirmed
    'available amount' when this was first tested against this account —
    see razorpayx/test_balance.py output). Checked defensively across the
    field names Razorpay's docs and this account have used, in order of
    likelihood; raises loudly rather than silently returning 0 (which
    would block every real payout) if none match — if this fires, run
    `python -m razorpayx.test_balance` and update the key list below to
    match the actual field name in its printed response.
    """
    data = await get_balance()
    items = data.get("items") or [data]
    if not items:
        raise RuntimeError(f"get_available_balance(): no balance items in response: {data}")
    item = items[0]
    for key in ("available_amount", "balance", "amount"):
        if key in item:
            return float(item[key]) / 100.0
    raise RuntimeError(
        f"get_available_balance(): none of the expected balance keys found in {item!r} — "
        "update authorization/real_balance.py's key list to match the actual field name."
    )


async def create_contact(name: str, email: str | None = None, contact_type: str = "vendor") -> dict:
    async with httpx.AsyncClient(auth=_auth) as client:
        payload = {"name": name, "type": contact_type}
        if email:
            payload["email"] = email
        resp = await client.post(f"{BASE_URL}/contacts", json=payload)
        resp.raise_for_status()
        return resp.json()


async def create_fund_account_bank(contact_id: str, account_number: str, ifsc: str, name: str) -> dict:
    async with httpx.AsyncClient(auth=_auth) as client:
        payload = {
            "contact_id": contact_id,
            "account_type": "bank_account",
            "bank_account": {"name": name, "ifsc": ifsc, "account_number": account_number},
        }
        resp = await client.post(f"{BASE_URL}/fund_accounts", json=payload)
        resp.raise_for_status()
        return resp.json()


async def create_fund_account_vpa(contact_id: str, address: str) -> dict:
    """UPI fund account — simplest for test-mode demo (no fake bank details needed)."""
    async with httpx.AsyncClient(auth=_auth) as client:
        payload = {
            "contact_id": contact_id,
            "account_type": "vpa",
            "vpa": {"address": address},
        }
        resp = await client.post(f"{BASE_URL}/fund_accounts", json=payload)
        resp.raise_for_status()
        return resp.json()


async def create_payout(
    fund_account_id: str,
    amount_paise: int,
    purpose: str = "payout",
    mode: str = "UPI",
    reference_id: str | None = None,
    narration: str | None = None,
) -> dict:
    """amount_paise: integer paise (e.g. ₹500 -> 50000). mode: UPI | IMPS | NEFT | RTGS."""
    if not RZP_ACCOUNT_NUMBER:
        raise RuntimeError("RZP_ACCOUNT_NUMBER is required to create payouts.")
    async with httpx.AsyncClient(auth=_auth) as client:
        payload = {
            "account_number": RZP_ACCOUNT_NUMBER,
            "fund_account_id": fund_account_id,
            "amount": amount_paise,
            "currency": "INR",
            "mode": mode,
            "purpose": purpose,
            "queue_if_low_balance": True,
        }
        if reference_id:
            payload["reference_id"] = reference_id
        if narration:
            payload["narration"] = narration
        resp = await client.post(f"{BASE_URL}/payouts", json=payload)
        resp.raise_for_status()
        return resp.json()


async def get_payout(payout_id: str) -> dict:
    async with httpx.AsyncClient(auth=_auth) as client:
        resp = await client.get(f"{BASE_URL}/payouts/{payout_id}")
        resp.raise_for_status()
        return resp.json()


async def reverse_payout(
    original_payout_id: str,
    self_fund_account_id: str,
    amount_paise: int,
    narration: str | None = None,
) -> dict:
    """Phase 4 (Preset 3 flagship) — RazorpayX Payouts have no undo/refund
    endpoint; per Razorpay's docs, a "reversal" only happens automatically
    when a payout FAILS, crediting the amount back on its own. There is no
    API to trigger that for a payout that already succeeded. So "sort it
    out" for a confirmed-processed payout whose downstream fulfillment
    failed has to mean firing a second, real payout of the same amount
    back to a fund account you control — a genuine, auditable reversal,
    not a database rollback pretending money moved back by itself.

    self_fund_account_id must point at a fund account belonging to the
    SAME RazorpayX account (see razorpayx/provision.py's provision_self())
    — not the original recipient's fund account.
    """
    reference_id = f"reversal-{original_payout_id}"[:40]
    return await create_payout(
        fund_account_id=self_fund_account_id,
        amount_paise=amount_paise,
        purpose="refund",
        reference_id=reference_id,
        narration=(narration or f"Aegis reversal of {original_payout_id}")[:30],
    )


@dataclass
class PayoutPollResult:
    """Phase 2 — replaces the old blanket 'queued/processing/processed all
    count as settled' read of a payout's status with a real classification,
    reached by re-fetching the payout up to RZP_POLL_MAX_ATTEMPTS times
    (RZP_POLL_INTERVAL_SECONDS apart) instead of trusting create_payout()'s
    single initial response.

    classification is one of:
      "processed"    — terminal success, money moved.
      "failed"       — terminal failure (reversed/rejected/cancelled/failed).
      "non_terminal" — still queued/processing after every poll attempt;
                        outcome genuinely unknown at this point. Callers
                        must NOT treat this as either success or failure —
                        see authorization/trace.py's real_balance check and
                        the Preset 2 (uncertain/reconciliation) design for
                        how this is meant to be routed (human_escalation_
                        required, not the compensation saga, until a later
                        resolution check settles it).
    """
    payout: dict
    classification: str  # "processed" | "failed" | "non_terminal"
    attempts_made: int
    razorpay_status: str


async def poll_payout(
    payout_id: str,
    max_attempts: int | None = None,
    interval_seconds: float | None = None,
) -> PayoutPollResult:
    """Checks a payout immediately, then re-polls via get_payout() every
    interval_seconds, up to max_attempts additional times, until it reaches
    a terminal status (processed / reversed / rejected / cancelled /
    failed) or the attempts are exhausted — whichever comes first.

    Worst-case wait is max_attempts * interval_seconds (default 3 x 2s =
    6s), matching the "poll every 2s, 3 attempts max" demo-day window from
    the Preset 2/3 design. Defaults come from RZP_POLL_INTERVAL_SECONDS /
    RZP_POLL_MAX_ATTEMPTS so that window is one env change, not a code
    change.
    """
    attempts = max_attempts if max_attempts is not None else RZP_POLL_MAX_ATTEMPTS
    interval = interval_seconds if interval_seconds is not None else RZP_POLL_INTERVAL_SECONDS

    payout = await get_payout(payout_id)
    status = payout.get("status", "")
    polls_done = 0

    while status in _NON_TERMINAL and polls_done < attempts:
        await asyncio.sleep(interval)
        payout = await get_payout(payout_id)
        status = payout.get("status", "")
        polls_done += 1

    if status in _TERMINAL_SUCCESS:
        return PayoutPollResult(payout, "processed", polls_done, status)
    if status in _TERMINAL_FAILURE:
        return PayoutPollResult(payout, "failed", polls_done, status)
    # Still queued/processing (or an unrecognized status) after every
    # attempt — genuinely unknown, not a failure. Callers must not treat
    # this as settled.
    return PayoutPollResult(payout, "non_terminal", polls_done, status)