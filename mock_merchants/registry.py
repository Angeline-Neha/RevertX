# -*- coding: utf-8 -*-
"""
mock_merchants/registry.py
Single source of truth for every mock merchant: which module implements it,
what port it runs on, its payee display name, whether it exposes a
/policy endpoint, and whether its /refund endpoint requires the caller to
supply the computed refund amount (server-side validated, to catch LLM
math errors — see merchant_c_domain.py's /refund).

Before this file existed, the same information was hand-duplicated in:
  - proxy/mcp_proxy.py        (MERCHANT_URLS, MERCHANT_PAYEES)
  - compensating_agent/graph.py (MERCHANT_URLS, MERCHANTS_WITH_POLICY,
                                  plus a literal `if merchant_id == "merchant_c"`
                                  special case inside attempt_refund_node)
  - run_demo.py / run_bg.py   (hardcoded uvicorn launch lines per merchant)

Adding a merchant (Phase 6) now means: add one MerchantSpec entry below,
plus one mock_merchants/*.py file implementing it. Every consumer listed
above derives its view from MERCHANTS instead of keeping its own copy, so
there is exactly one place that can be wrong.

Note: start_servers.ps1 (Windows launcher) is PowerShell, not Python, so it
cannot import this module directly. It still needs its own Start-Job line
added per merchant — see the comment at the top of that file.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MerchantSpec:
    payee: str
    port: int
    module: str  # dotted path to the FastAPI app, e.g. "mock_merchants.merchant_a_crm"
    has_policy: bool = False          # exposes GET /policy
    refund_needs_amount: bool = False  # /refund requires the caller to supply the computed amount

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    @property
    def app_target(self) -> str:
        """uvicorn's `module:app` target string."""
        return f"{self.module}:app"


MERCHANTS: dict[str, MerchantSpec] = {
    "merchant_a": MerchantSpec(
        payee="CRM Corp",
        port=8001,
        module="mock_merchants.merchant_a_crm",
    ),
    "merchant_b": MerchantSpec(
        payee="Grand Hotel",
        port=8002,
        module="mock_merchants.merchant_b_hotel",
        has_policy=True,
    ),
    "merchant_c": MerchantSpec(
        payee="Domain Registrar",
        port=8003,
        module="mock_merchants.merchant_c_domain",
        has_policy=True,
        refund_needs_amount=True,
    ),
    # Phase 6.1 — partial-penalty merchant: 30% within 5 days, otherwise
    # full refund. Exercises compute_refund()'s arbitrary-percentage math
    # with a different window/percentage pair than merchant_c's 10%/48h.
    "merchant_d": MerchantSpec(
        payee="Flex-Stay Hotels",
        port=8006,
        module="mock_merchants.merchant_d_flexstay",
        has_policy=True,
        refund_needs_amount=True,
    ),
    # Phase 6.2 — flaky merchant: /policy intermittently 500s or times out.
    # Exercises fetch_policy's fail-safe path in compensating_agent/graph.py
    # (an errored /policy call now defaults to non-refundable, not a silent
    # full refund — see that function's docstring/comment).
    "merchant_e": MerchantSpec(
        payee="Apex Print & Signage",
        port=8007,
        module="mock_merchants.merchant_e_flaky",
        has_policy=True,
    ),
    # Phase 6.3 — second, unrelated vendor set ("event launch" scenario):
    # proves nothing is hardcoded to the original CRM/Hotel/Domain names.
    "merchant_f": MerchantSpec(
        payee="Riverside Events Hall",
        port=8008,
        module="mock_merchants.merchant_f_venue",
        has_policy=True,
    ),
    "merchant_g": MerchantSpec(
        payee="Spice Route Catering",
        port=8009,
        module="mock_merchants.merchant_g_catering",
        has_policy=True,
        refund_needs_amount=True,
    ),
}

# Convenience derived views — kept so existing call sites that only ever
# needed a plain dict of urls/payees don't need to touch MerchantSpec
# directly.
MERCHANT_URLS: dict[str, str] = {mid: m.url for mid, m in MERCHANTS.items()}
MERCHANT_PAYEES: dict[str, str] = {mid: m.payee for mid, m in MERCHANTS.items()}
MERCHANTS_WITH_POLICY: frozenset[str] = frozenset(
    mid for mid, m in MERCHANTS.items() if m.has_policy
)
MERCHANTS_NEEDING_REFUND_AMOUNT: frozenset[str] = frozenset(
    mid for mid, m in MERCHANTS.items() if m.refund_needs_amount
)
