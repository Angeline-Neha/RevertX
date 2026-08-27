"""
test_level1_migration_safety.py

Regression guard for the Phase 1 data-integrity fix: run_migrations() must
be idempotent and must NEVER destroy existing rows in transaction_steps.

Context: the original run_migrations() unconditionally ran
`DROP TABLE IF EXISTS transaction_steps CASCADE` on every call, and it is
called by both mcp_proxy's startup lifespan AND compensating_agent/worker.py's
main(). Since these are separate processes, any restart of either one after
payments had already been logged silently erased the entire transaction
ledger. This test proves that can't happen again.
"""
from __future__ import annotations

import uuid

import pytest

import db.client as db


@pytest.mark.asyncio
async def test_run_migrations_does_not_drop_existing_data():
    """
    Simulates the real failure scenario: a service (e.g. the worker) calls
    run_migrations() on startup AFTER other steps have already been written
    to the ledger by another process (e.g. the proxy). The previously
    written rows must still be present afterward.
    """
    await db.run_migrations()
    await db.init_pool()
    try:
        wid = f"migration-safety-{uuid.uuid4().hex[:8]}"
        await db.create_workflow(wid, 50000.0)

        entry = {
            "step_id": str(uuid.uuid4()),
            "workflow_id": wid,
            "merchant_id": "merchant_a",
            "expected": {"amount": 1000.0, "currency": "INR", "payee": "CRM Corp", "item": "test"},
            "actual": {
                "amount": 1000.0,
                "currency": "INR",
                "payee": "CRM Corp",
                "settlement_ref": f"crm_{uuid.uuid4().hex[:8]}",
                "status": "settled",
            },
            "raw_gateway_response": {"status_code": 200},
        }
        await db.write_transaction_step(entry, idem_key=str(uuid.uuid4()))

        before = db.get_workflow_steps_sync(wid)
        assert len(before) == 1, "Setup failed: step was not written"

        # This is the exact call every service makes on startup. It must be
        # safe to call again at any time without losing prior data.
        await db.run_migrations()

        after = db.get_workflow_steps_sync(wid)
        assert len(after) == 1, (
            "run_migrations() destroyed existing transaction_steps rows. "
            "This is the data-loss regression Phase 1 fixed — see "
            "db/client.py::run_migrations() vs reset_database_for_testing()."
        )
        assert after[0].step_id == entry["step_id"]
    finally:
        await db.close_pool()


@pytest.mark.asyncio
async def test_reset_database_for_testing_is_the_only_destructive_path():
    """
    Confirms the destructive drop still exists (tests may legitimately need
    a clean slate) but lives under an explicit, differently-named function
    that nothing in the application startup path calls.
    """
    assert hasattr(db, "reset_database_for_testing"), (
        "reset_database_for_testing() is missing — if a destructive reset "
        "is needed for test fixtures, it must be explicit and separate from "
        "run_migrations(), never folded back into it."
    )

    import inspect

    run_migrations_source = inspect.getsource(db.run_migrations)
    assert "DROP TABLE" not in run_migrations_source, (
        "run_migrations() must never contain a destructive DROP TABLE — "
        "that belongs only in reset_database_for_testing()."
    )

    for module_name in ("proxy.mcp_proxy", "compensating_agent.worker"):
        import importlib

        module = importlib.import_module(module_name)
        source = inspect.getsource(module)
        assert "reset_database_for_testing" not in source, (
            f"{module_name} must never call the destructive test-only reset function."
        )
