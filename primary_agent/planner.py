# -*- coding: utf-8 -*-
"""
primary_agent/planner.py
Phase 7.2 — the planning LLM call that replaces procurement_agent.py's
fixed 3-payment script with something that takes a goal in plain English
and decides the payment sequence itself.

Config-driven (model, temperature) and uses the same 2-attempt
retry/backoff shape as engine/policy_extractor.py — see that file's
docstrings for why: a chunk of Gemini failures are transient, and
distinguishing "the API call itself failed" from "it returned something
we couldn't parse" makes the failure reason visible instead of a generic
500 with no detail.

Guardrails (Phase 7.4), enforced in code after the LLM responds, not just
requested in the prompt:
  - every merchant_id must be a real entry in primary_agent/catalog.py —
    any hallucinated id is dropped, not passed through to /pay
  - amount must fall within PLAN_AMOUNT_BAND of that merchant's
    typical_cost — bounds how far a bad LLM guess can drift from something
    plausible
  - line items are capped at MAX_LINE_ITEMS
  - if the LLM call fails outright (both attempts) or every returned line
    item fails validation, plan_procurement() falls back to a deterministic
    greedy plan built straight from the catalog (see _fallback_plan) rather
    than raising in the middle of a live demo — same fail-safe philosophy
    as policy_extractor.py's _FAIL_SAFE, applied to planning instead of
    refund-eligibility extraction.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, asdict

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types
import structlog

from primary_agent.catalog import MERCHANT_CATALOG, CATALOG_BY_ID, catalog_prompt_block

logger = structlog.get_logger()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
PLANNER_MODEL = os.environ.get("PLANNER_MODEL", "gemini-3.5-flash-lite")
# Some temperature (rather than 0.0, which anomaly_detector.py uses for a
# pure classification task) so the planner can pick a sensibly different
# subset of the catalog for different goals instead of always converging
# on the same items — but low enough that the demo doesn't get a wildly
# different plan every run.
PLANNER_TEMPERATURE = float(os.environ.get("PLANNER_TEMPERATURE", "0.4"))

MAX_LINE_ITEMS = 4
# Allowed drift from a catalog entry's typical_cost, e.g. 0.5 = 50%-150%
# of typical_cost.
PLAN_AMOUNT_BAND = 0.5

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


_PLANNER_PROMPT = """\
You are a procurement planning assistant. Given a goal and a budget limit,
choose which items to purchase from the catalog below to accomplish the
goal. You may go over budget if that's a natural reading of the goal —
the caller's own budget-enforcement layer (not you) decides what happens
in that case.

Catalog (merchant_id, what it sells, typical price):
{catalog}

Rules:
- Only use merchant_id values that appear in the catalog above verbatim.
- Choose at most {max_items} line items.
- Keep each amount reasonably close to that merchant's typical price
  (roughly half to one and a half times it) — don't invent arbitrary
  figures unrelated to the catalog.
- Order the list in the sequence payments should be attempted.

Goal: {goal}
Budget limit: ₹{budget_limit:,.0f}

Output schema — return ONLY a JSON array, nothing else:
[
  {{"merchant_id": "...", "amount": number, "item": "short item description"}}
]
"""


@dataclass
class PlannedPayment:
    merchant_id: str
    amount: float
    item: str

    def to_dict(self) -> dict:
        return asdict(self)


def _sync_plan_call(prompt: str) -> str:
    client = _get_client()
    resp = client.models.generate_content(
        model=PLANNER_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=PLANNER_TEMPERATURE,
        ),
    )
    return resp.text or ""


def _validate_and_clamp(raw_items: list[dict]) -> list[PlannedPayment]:
    """Applies the Phase 7.4 guardrails to whatever the LLM returned.
    Silently drops individual line items that fail validation rather than
    failing the whole plan for one bad entry — a demo showing 2 good items
    beats a demo showing an error because item 3 of 3 was malformed."""
    valid: list[PlannedPayment] = []
    for raw in raw_items[:MAX_LINE_ITEMS]:
        try:
            merchant_id = str(raw["merchant_id"])
            amount = float(raw["amount"])
            item = str(raw.get("item") or CATALOG_BY_ID[merchant_id].sample_item)
        except (KeyError, TypeError, ValueError):
            continue

        catalog_entry = CATALOG_BY_ID.get(merchant_id)
        if catalog_entry is None:
            logger.warning("planner.dropped_unknown_merchant", merchant_id=merchant_id)
            continue

        low = catalog_entry.typical_cost * (1 - PLAN_AMOUNT_BAND)
        high = catalog_entry.typical_cost * (1 + PLAN_AMOUNT_BAND)
        if not (low <= amount <= high):
            logger.warning(
                "planner.dropped_out_of_band_amount",
                merchant_id=merchant_id, amount=amount, low=low, high=high,
            )
            continue

        valid.append(PlannedPayment(merchant_id=merchant_id, amount=round(amount, 2), item=item))

    return valid


def _fallback_plan(budget_limit: float) -> list[PlannedPayment]:
    """
    Deterministic plan used when the LLM call fails outright, or every
    returned line item fails validation. Greedily adds catalog items
    (in the order they're declared in catalog.py) while the running total
    stays under budget_limit, then adds exactly one more on top — same
    shape as the original scripted demo (two items comfortably in budget,
    a third that deliberately pushes over), so a live demo still gets a
    real MANDATE EXCEEDED / compensation moment instead of a silently
    empty or trivially-in-budget plan.
    """
    plan: list[PlannedPayment] = []
    total = 0.0
    for entry in MERCHANT_CATALOG:
        if len(plan) >= MAX_LINE_ITEMS:
            break
        plan.append(PlannedPayment(entry.merchant_id, entry.typical_cost, entry.sample_item))
        total += entry.typical_cost
        if total > budget_limit:
            break
    return plan


async def plan_procurement(goal: str, budget_limit: float) -> list[PlannedPayment]:
    """
    Input: a plain-English goal + a budget limit.
    Output: an ordered list of PlannedPayment, chosen from the catalog and
    validated against it — never invented merchants or wildly off amounts.
    Falls back to a deterministic plan (see _fallback_plan) rather than
    raising, matching this codebase's existing fail-safe-over-crash
    philosophy for LLM call sites.
    """
    prompt = _PLANNER_PROMPT.format(
        catalog=catalog_prompt_block(),
        max_items=MAX_LINE_ITEMS,
        goal=goal,
        budget_limit=budget_limit,
    )

    last_api_exc: Exception | None = None

    for attempt in range(2):
        try:
            raw_text = await asyncio.to_thread(_sync_plan_call, prompt)
            data = json.loads(raw_text)
            if not isinstance(data, list):
                raise ValueError("Expected a JSON array of line items")

            plan = _validate_and_clamp(data)
            if plan:
                logger.info("planner.plan_generated", goal=goal, line_items=len(plan))
                return plan

            # Every item was dropped by guardrails — treat like a parse
            # failure and retry once before falling back.
            raise ValueError("No valid line items survived guardrail validation")

        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("planner.parse_or_validation_failure", attempt=attempt, error=str(exc))
            if attempt == 1:
                break
            continue

        except Exception as exc:
            last_api_exc = exc
            logger.error(
                "planner.api_call_failed",
                attempt=attempt, error_type=type(exc).__name__, error=str(exc),
            )
            if attempt == 1:
                break
            await asyncio.sleep(1.5)
            continue

    logger.warning(
        "planner.falling_back_to_deterministic_plan",
        goal=goal, last_api_error=str(last_api_exc) if last_api_exc else None,
    )
    return _fallback_plan(budget_limit)
