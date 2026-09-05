# -*- coding: utf-8 -*-
"""
mock_merchants/downstream_service.py
The "downstream fulfillment/booking" step for the Preset 3 (flagship)
scenario: "payout succeeds, then the thing it paid for doesn't happen."

This is deliberately NOT another payment gateway call — merchant_rzp has
no mock HTTP merchant to call (it's a real RazorpayX payout, not a mock
/charge endpoint), so there's nothing to simulate a booking-confirmation
failure against except a local, controlled check. And it's deliberately
NOT randomly flaky like merchant_e's /policy endpoint — Preset 3 is meant
to be 100% reproducible on demand for a live demo (per the design
conversation: a judge watching once needs a guaranteed story, not a coin
flip).

Wired into proxy/mcp_proxy.py's /pay, merchant_rzp branch, only after a
payout has polled to "processed" — this checks what happens *after* the
money definitely moved, which is the whole point of the scenario.
"""
from __future__ import annotations

import os

# When true (the default), every merchant_rzp fulfillment check fails —
# this is the intended demo behavior for Preset 3. Set to "false" in .env
# to rehearse Preset 1 (clean happy path) through the exact same
# merchant_rzp payout path without triggering the downstream failure.
DOWNSTREAM_FORCE_FAIL = os.getenv("DOWNSTREAM_FORCE_FAIL", "true").lower() == "true"


async def confirm_fulfillment(merchant_id: str, settlement_ref: str, amount: float) -> dict:
    """Returns {"confirmed": bool, "reason": str}.

    A real integration would call the merchant's own booking/fulfillment
    API here. merchant_rzp has none — "RazorpayX Live Payout" isn't a
    bookable merchant, it's a stand-in for "a real payout went out for
    some real-world thing" — so this is a local, deterministic stand-in
    for that missing confirmation call.
    """
    if DOWNSTREAM_FORCE_FAIL:
        return {
            "confirmed": False,
            "reason": (
                f"Fulfillment/booking confirmation for {merchant_id} "
                f"(settlement_ref={settlement_ref}, ₹{amount:,.2f}) was "
                "never received — the payout succeeded but the thing it "
                "paid for was not confirmed on the other end."
            ),
        }
    return {"confirmed": True, "reason": "Fulfillment confirmed."}
