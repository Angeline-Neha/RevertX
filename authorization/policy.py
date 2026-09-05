"""
authorization/policy.py
Pre-transaction rules the agent itself must obey — recipient allowlist,
category restrictions, human-approval threshold. Deterministic, no LLM.

Distinct from engine/policy_extractor.py, which parses a MERCHANT's refund
policy after the fact. This module answers "is the agent allowed to do
this at all", not "what does the merchant owe us back."
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PolicyConfig:
    allowed_categories: set[str] = field(default_factory=lambda: {"vendor_payment", "conference_registration", "subscription"})
    recipient_allowlist: set[str] | None = None  # None = no allowlist restriction
    recipient_denylist: set[str] = field(default_factory=set)
    # Was 15000.0 — lower than several legitimate single-item catalog
    # amounts (merchant_b/merchant_d hotels ₹20k, merchant_f venue ₹25k),
    # so this gate was hard-blocking those items ("exceeds the ₹15000.0
    # auto-approve threshold") before the payment ever reached the
    # budget-mandate check the demo scenarios are actually designed
    # around — e.g. Happy path's CRM+hotel could never both succeed, since
    # the hotel alone always tripped this. Raised to match the Agent
    # Wallet's own per-txn limit (authorization/wallet.py's WalletState,
    # currently ₹25,000 in the seeded agent_wallet row) — the wallet is
    # already the authority on "is a single payment too large", so this
    # threshold should sit at or above it, not below it.
    human_approval_threshold: float = 25000.0


# Shared singleton — authorize() defaults to this instead of constructing a
# fresh PolicyConfig() per call, so the Policy panel's GET /policy reads the
# exact same object the enforcement path checks against. Two independently
# default-constructed instances would happen to match today, but would
# silently drift the moment either one is edited without the other.
DEFAULT_POLICY_CONFIG = PolicyConfig()


@dataclass
class PolicyCheckResult:
    check: str
    passed: bool
    detail: str


def evaluate(
    *, amount: float, category: str, recipient_id: str, config: PolicyConfig,
) -> list[PolicyCheckResult]:
    results = []

    results.append(PolicyCheckResult(
        check="check_category",
        passed=category in config.allowed_categories,
        detail=f"category '{category}' {'is' if category in config.allowed_categories else 'is not'} in allowed set {sorted(config.allowed_categories)}",
    ))

    if recipient_id in config.recipient_denylist:
        results.append(PolicyCheckResult("check_recipient_denylist", False, f"recipient '{recipient_id}' is explicitly denied"))
    else:
        results.append(PolicyCheckResult("check_recipient_denylist", True, "recipient not on denylist"))

    if config.recipient_allowlist is not None:
        ok = recipient_id in config.recipient_allowlist
        results.append(PolicyCheckResult("check_recipient_allowlist", ok, f"recipient '{recipient_id}' {'is' if ok else 'is not'} on the allowlist"))

    needs_approval = amount > config.human_approval_threshold
    results.append(PolicyCheckResult(
        check="check_approval_threshold",
        passed=not needs_approval,
        detail=f"₹{amount} {'exceeds' if needs_approval else 'is within'} the ₹{config.human_approval_threshold} auto-approve threshold",
    ))

    return results


def all_passed(results: list[PolicyCheckResult]) -> bool:
    return all(r.passed for r in results)
