# -*- coding: utf-8 -*-
"""
primary_agent/catalog.py
Merchant catalog for the planning agent (Phase 7.1) — a small, structured
list of what's actually purchasable in this demo, built from the merchants
that exist in mock_merchants/registry.py after Phase 6.

Each entry gives the planner (primary_agent/planner.py) what it needs to
choose sensibly: what the merchant sells, a typical price so amounts stay
plausible instead of invented from nothing, and the merchant_id that maps
straight onto mock_merchants/registry.py / proxy's /pay endpoint — no
separate lookup or translation step between "what the planner picked" and
"what /pay actually calls".

typical_cost is a demo-reasonable single figure, not a range — this keeps
the planner's guardrail check in planner.py (amounts must stay within a
band of typical_cost) simple, and matches the ballpark figures already
used in the original scripted procurement_agent.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from mock_merchants.registry import MERCHANTS


@dataclass(frozen=True)
class CatalogEntry:
    merchant_id: str
    payee: str
    sells: str          # short description of what this merchant sells
    typical_cost: float  # demo-reasonable ballpark price in INR
    sample_item: str    # example item string, passed through to /pay as-is


MERCHANT_CATALOG: list[CatalogEntry] = [
    CatalogEntry("merchant_a", MERCHANTS["merchant_a"].payee, "CRM software licensing", 10000.0, "CRM license (annual)"),
    CatalogEntry("merchant_b", MERCHANTS["merchant_b"].payee, "hotel booking, non-refundable within 7 days of booking", 20000.0, "hotel booking (2 nights)"),
    CatalogEntry("merchant_c", MERCHANTS["merchant_c"].payee, "domain registration, hosting, and similar non-refundable-window digital goods", 12000.0, "domain registration + hosting"),
    CatalogEntry("merchant_d", MERCHANTS["merchant_d"].payee, "hotel booking with flexible (partial-penalty) cancellation", 20000.0, "hotel booking, flexible cancellation"),
    CatalogEntry("merchant_e", MERCHANTS["merchant_e"].payee, "print and signage services", 8000.0, "signage printing"),
    CatalogEntry("merchant_f", MERCHANTS["merchant_f"].payee, "event venue rental, always non-refundable", 25000.0, "venue booking"),
    CatalogEntry("merchant_g", MERCHANTS["merchant_g"].payee, "event catering", 15000.0, "catering"),
    # Not in mock_merchants/registry.py — mcp_proxy.py special-cases this id
    # to fire a REAL RazorpayX test-mode payout instead of forwarding to a
    # local mock server (see the merchant_id == "merchant_rzp" branch in
    # proxy/mcp_proxy.py). typical_cost=15000 keeps the LLM's amount band
    # (±50%) inside both the wallet's per-txn limit and a realistic single
    # conference-registration-style payment.
    CatalogEntry("merchant_rzp", "RazorpayX Live Payout", "conference registration or vendor payment made as a real RazorpayX test-mode payout", 15000.0, "conference registration (real payout)"),
    # Phase 6 (Preset 2) — same real RazorpayX payout path as merchant_rzp
    # above, but mcp_proxy.py deterministically forces this one's poll
    # classification to "non_terminal" (via RZP_PENDING_DEMO_FORCE) instead
    # of trusting whatever RazorpayX test-mode timing happens to do. Exists
    # so the payout_unconfirmed / pending_payout_worker.py path (Phase 5)
    # has a reliably reproducible trigger for a live demo, the same way
    # DOWNSTREAM_FORCE_FAIL makes Preset 3's reversal reproducible — sells
    # text is deliberately distinct/verbose so the planner doesn't confuse
    # it with plain merchant_rzp.
    CatalogEntry(
        "merchant_rzp_pending", "RazorpayX Live Payout (Reconciliation Test)",
        "a real RazorpayX test-mode payout for a reconciliation or dispute-resolution demo, "
        "deliberately left pending/processing/unconfirmed to test the uncertain-payout handling path — "
        "NOT a normal conference registration or vendor payment",
        15000.0, "reconciliation test payout (intentionally pending)",
    ),
    # Insufficient Funds preset — same real RazorpayX payout path as
    # merchant_rzp, but authorization/real_balance.py deterministically
    # forces check_real_balance to fail for this one (via
    # REAL_BALANCE_DEMO_FORCE_INSUFFICIENT), instead of depending on the
    # account's actual balance happening to be low enough on demo day —
    # same reproducibility reasoning as DOWNSTREAM_FORCE_FAIL and
    # RZP_PENDING_DEMO_FORCE. Never reaches create_payout() at all: the
    # block happens in authorize(), before any money moves.
    CatalogEntry(
        "merchant_rzp_insufficient", "RazorpayX Live Payout (Insufficient Funds Test)",
        "a real RazorpayX payout deliberately used to demonstrate the real-balance block — "
        "requested amount treated as exceeding the account's actual available balance — "
        "NOT a normal conference registration or vendor payment",
        15000.0, "insufficient-funds test payout (intentionally blocked)",
    ),
    # Downstream Failure preset (the flagship saga-recovery demo) — same
    # real payout + poll-to-processed path as merchant_rzp, but
    # mock_merchants/downstream_service.py deterministically forces ONLY
    # this merchant_id's fulfillment check to fail (via
    # DOWNSTREAM_FORCE_FAIL, now scoped by merchant_id instead of applying
    # to every merchant_rzp payout) — so Happy Path (merchant_rzp) can
    # always succeed end-to-end while this one always demonstrates the
    # real reversal.
    CatalogEntry(
        "merchant_rzp_downstream_fail", "RazorpayX Live Payout (Downstream Failure Test)",
        "a real RazorpayX payout deliberately used to demonstrate saga recovery — payout "
        "processed successfully but the downstream fulfillment/booking confirmation fails, "
        "triggering a real reversal payout — NOT a normal conference registration or vendor payment",
        15000.0, "downstream-failure test payout (triggers reversal)",
    ),
]

# Fast lookup by id — planner.py validates every LLM-chosen merchant_id
# against this before it's allowed anywhere near a real /pay call.
CATALOG_BY_ID: dict[str, CatalogEntry] = {c.merchant_id: c for c in MERCHANT_CATALOG}


def catalog_prompt_block() -> str:
    """Renders the catalog as plain text for the planner's prompt."""
    lines = [
        f"- {c.merchant_id} ({c.payee}): sells {c.sells}. Typical price ~₹{c.typical_cost:,.0f}."
        for c in MERCHANT_CATALOG
    ]
    return "\n".join(lines)
