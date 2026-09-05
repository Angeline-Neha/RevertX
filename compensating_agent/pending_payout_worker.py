"""
compensating_agent/pending_payout_worker.py
Phase 5 — resolves merchant_rzp payouts that polled to "non_terminal" in
mcp_proxy.py (Phase 2's poll_payout() timed out still queued/processing).

Design (per the Preset 2 discussion carried over from the transcript this
phase started from): a non_terminal payout is genuinely ambiguous, not a
known failure — it must not be silently treated as either. mcp_proxy.py's
/pay handler already does the right thing at request time: it holds the
budget reservation open (Phase 5's fix — see mcp_proxy.py) and persists a
`pending_payouts` row instead of guessing. This worker is what actually
revisits that row later:

  - single-shot recheck (poll_payout(payout_id, max_attempts=0) — no
    internal sleep, just one fresh GET) every
    PENDING_PAYOUT_RECHECK_INTERVAL_SECONDS
  - "processed"  -> quietly resolved: commit the held budget, write a
                    payout_resolution ledger entry, tell the dashboard so
                    the escalation banner clears itself
  - "failed"     -> known failure now: release the held budget (rollback),
                    write the ledger entry, tell the dashboard. Nothing to
                    reverse — reversed/rejected/cancelled/failed all mean
                    the money never actually left (or Razorpay already
                    auto-reversed it), so this is the compensation outcome
                    for this case, not a hand-off to reverse_payout().
  - still "non_terminal" after PENDING_PAYOUT_MAX_CHECKS rechecks -> marked
    'exhausted': stops auto-retrying and fires a distinct, non-auto-
    clearing escalation — auto-retry has done what it can; only a human
    checking the RazorpayX dashboard directly can resolve it from here.

Runs standalone (`python -m compensating_agent.pending_payout_worker`), no
RabbitMQ involved — this is a simple poll loop over Postgres, not a queue
consumer.
"""
from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

import db.client as db
from proxy.schemas import ActualPayment, ExpectedPayment, TransactionLogEntry
from razorpayx.client import poll_payout
from state_log.redis_client import publish_event

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RECHECK_INTERVAL_SECONDS = float(os.getenv("PENDING_PAYOUT_RECHECK_INTERVAL_SECONDS", "15"))
MAX_CHECKS = int(os.getenv("PENDING_PAYOUT_MAX_CHECKS", "8"))


async def _write_resolution_step(row: dict, status: str, razorpay_status: str) -> None:
    """A follow-up ledger entry for the same payout — mirrors how refunds
    get their own transaction_steps row (action_type='refund') rather than
    mutating the original payment entry in place, so the original 'pending'
    entry stays an honest record of what the /pay response actually said
    at the time."""
    expected = ExpectedPayment(amount=row["amount"], currency=row["currency"], payee=row["payee"], item="")
    actual = ActualPayment(
        amount=row["amount"], currency=row["currency"], payee=row["payee"],
        settlement_ref=row["payout_id"], status=status,
    )
    entry = TransactionLogEntry(
        workflow_id=row["workflow_id"], action_type="payout_resolution",
        merchant_id=row["merchant_id"], expected=expected, actual=actual,
        raw_gateway_response={"razorpay_status": razorpay_status, "resolved_from": "pending_payout_worker"},
    )
    await db.write_transaction_step(entry.model_dump())
    publish_event(row["workflow_id"], "step_written", entry.model_dump())


async def _recheck_one(row: dict) -> None:
    wid = row["workflow_id"]
    result = await poll_payout(row["payout_id"], max_attempts=0, interval_seconds=0)

    if result.classification == "processed":
        # commit_budget only adjusts the delta between expected and actual
        # amount — the reservation itself was never rolled back (Phase 5's
        # mcp_proxy.py fix), so this just closes out the hold cleanly.
        await db.commit_budget(wid, row["amount"], row["amount"])
        await db.resolve_pending_payout(row["id"], "resolved_settled", result.razorpay_status)
        await _write_resolution_step(row, "settled", result.razorpay_status)
        publish_event(wid, "payout_resolved", {
            "workflow_id": wid, "merchant_id": row["merchant_id"],
            "settlement_ref": row["payout_id"], "pending_payout_id": row["id"],
            "resolution": "settled", "razorpay_status": result.razorpay_status,
            "message": f"Payout {row['payout_id']} confirmed processed — resolved automatically, no intervention needed.",
        })
        logger.info(f"pending_payout {row['id']} ({row['payout_id']}) resolved: settled")

    elif result.classification == "failed":
        await db.rollback_budget(wid, row["amount"])
        await db.resolve_pending_payout(row["id"], "resolved_failed", result.razorpay_status)
        await _write_resolution_step(row, "failed", result.razorpay_status)
        publish_event(wid, "payout_resolved", {
            "workflow_id": wid, "merchant_id": row["merchant_id"],
            "settlement_ref": row["payout_id"], "pending_payout_id": row["id"],
            "resolution": "failed", "razorpay_status": result.razorpay_status,
            "message": (
                f"Payout {row['payout_id']} came back '{result.razorpay_status}' — money never actually "
                "moved (or Razorpay auto-reversed it). Budget released, nothing to reverse."
            ),
        })
        logger.info(f"pending_payout {row['id']} ({row['payout_id']}) resolved: failed ({result.razorpay_status})")

    else:
        checks_done = await db.touch_pending_payout(row["id"], result.razorpay_status)
        if checks_done >= MAX_CHECKS:
            await db.resolve_pending_payout(row["id"], "exhausted", result.razorpay_status)
            publish_event(wid, "payout_resolution_exhausted", {
                "workflow_id": wid, "merchant_id": row["merchant_id"],
                "settlement_ref": row["payout_id"], "pending_payout_id": row["id"],
                "razorpay_status": result.razorpay_status, "checks_done": checks_done,
                "message": (
                    f"Payout {row['payout_id']} still '{result.razorpay_status}' after {checks_done} "
                    "auto-rechecks — giving up on auto-resolution. Check the RazorpayX dashboard directly."
                ),
            })
            logger.warning(f"pending_payout {row['id']} ({row['payout_id']}) exhausted after {checks_done} checks")
        else:
            logger.info(f"pending_payout {row['id']} ({row['payout_id']}) still {result.razorpay_status} (check {checks_done}/{MAX_CHECKS})")


async def run_once() -> None:
    rows = await db.get_open_pending_payouts()
    for row in rows:
        try:
            await _recheck_one(row)
        except Exception as exc:
            # One payout's transient error (e.g. a RazorpayX API hiccup)
            # must not crash the loop or block every other row — log and
            # let the next cycle try this row again, same as it would any
            # other cycle where the status simply hadn't changed yet.
            logger.error(f"pending_payout {row['id']} recheck failed: {exc}")


async def main() -> None:
    await db.run_migrations()
    await db.init_pool()
    logger.info(
        f"pending_payout_worker started — rechecking every {RECHECK_INTERVAL_SECONDS}s, "
        f"max {MAX_CHECKS} checks before marking exhausted"
    )
    try:
        while True:
            await run_once()
            await asyncio.sleep(RECHECK_INTERVAL_SECONDS)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
