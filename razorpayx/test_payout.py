"""
razorpayx/test_payout.py
Fires ONE real RazorpayX test-mode payout, isolated from mcp_proxy/pay.
Purpose: prove create_payout works (auth, fund account, payload shape)
before wiring it into the /pay pipeline where wallet/budget/reconciliation
could otherwise mask where a failure is coming from.

Usage:
    python -m razorpayx.test_payout [amount_in_rupees]
    (defaults to ₹10 — small, cheap against the ₹10k test balance)

Requires RZP_DEMO_FUND_ACCOUNT_ID in .env (see razorpayx/provision.py).
"""
import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv

load_dotenv()

from razorpayx.client import create_payout, get_payout, get_balance

FUND_ACCOUNT_ID = os.getenv("RZP_DEMO_FUND_ACCOUNT_ID")


async def main():
    if not FUND_ACCOUNT_ID:
        raise RuntimeError(
            "RZP_DEMO_FUND_ACCOUNT_ID not set in .env — run "
            "`python -m razorpayx.provision` first and paste its output in."
        )

    amount_rupees = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    amount_paise = int(round(amount_rupees * 100))
    reference_id = f"test-{uuid.uuid4().hex[:12]}"

    balance_before = await get_balance()
    print("Balance before:", balance_before)

    print(f"\nCreating payout: ₹{amount_rupees} -> {FUND_ACCOUNT_ID} (ref={reference_id})")
    payout = await create_payout(
        fund_account_id=FUND_ACCOUNT_ID,
        amount_paise=amount_paise,
        purpose="payout",
        mode="UPI",
        reference_id=reference_id,
        narration="RevertX test payout",
    )
    print("\nPayout response:")
    print(payout)

    payout_id = payout.get("id")
    status = payout.get("status")
    print(f"\npayout_id={payout_id} status={status}")

    # Test-mode payouts commonly land in queued/processing and never reach
    # 'processed' — that's still a committed payout, not a failure. Only
    # 'rejected'/'cancelled'/'failed' mean the money didn't move.
    if status in ("queued", "processing", "processed"):
        print(f"✓ Payout accepted (status={status}) — money committed against test balance.")
    else:
        print(f"✗ Unexpected status={status} — inspect the raw response above.")

    if payout_id:
        print("\nRe-fetching payout by id to confirm it's queryable...")
        fetched = await get_payout(payout_id)
        print(fetched)


if __name__ == "__main__":
    asyncio.run(main())
