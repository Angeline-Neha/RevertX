"""
razorpayx/client.py
Thin async wrapper over RazorpayX's Payouts API (test mode).
Auth: HTTP Basic (Key ID / Key Secret) — same as Razorpay Payments.
Docs: https://razorpay.com/docs/x/
"""
from __future__ import annotations

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

RZP_KEY_ID = os.getenv("RZP_KEY_ID")
RZP_KEY_SECRET = os.getenv("RZP_KEY_SECRET")
RZP_ACCOUNT_NUMBER = os.getenv("RZP_ACCOUNT_NUMBER")  # RazorpayX virtual account no.
BASE_URL = "https://api.razorpay.com/v1"

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