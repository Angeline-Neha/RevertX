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

# When true (the default), the Downstream Failure demo preset's fulfillment
# check always fails — this is the intended demo behavior for that preset.
# Scoped to FORCE_FAIL_MERCHANT_ID only (previously applied to every
# merchant_rzp payout, which meant a true Happy Path — real payout AND
# downstream success — didn't exist without flipping this off globally and
# breaking the failure demo in the same breath). Set to "false" to rehearse
# that preset's exact payout path without triggering the failure.
DOWNSTREAM_FORCE_FAIL = os.getenv("DOWNSTREAM_FORCE_FAIL", "true").lower() == "true"
FORCE_FAIL_MERCHANT_ID = "merchant_rzp_downstream_fail"


async def confirm_fulfillment(merchant_id: str, settlement_ref: str, amount: float) -> dict:
    """Returns {"confirmed": bool, "reason": str}.

    A real integration would call the merchant's own booking/fulfillment
    API here. The real-payout merchants have none — these aren't bookable
    merchants, they're a stand-in for "a real payout went out for some
    real-world thing" — so this is a local, deterministic stand-in for that
    missing confirmation call. Only FORCE_FAIL_MERCHANT_ID ever fails here;
    every other merchant_id (e.g. the Happy Path's merchant_rzp) always
    confirms, since there is no real downstream to actually check.
    """
    if merchant_id == FORCE_FAIL_MERCHANT_ID and DOWNSTREAM_FORCE_FAIL:
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
