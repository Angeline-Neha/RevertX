"""
test_level1.py
Level 1 regression tests.
"""
from __future__ import annotations

import asyncio
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from proxy.mcp_proxy import app
import db.client as db

@pytest.mark.asyncio
async def test_budget_race_is_closed():
    """
    Simulates two concurrent /pay requests for the same workflow where the
    combined total exceeds the budget limit.
    """
    await db.run_migrations()
    await db.init_pool()
    try:
        WID = f"race-test-{uuid.uuid4().hex[:8]}"
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init_workflow", headers={"X-API-Key": "test-key-123"}, json={"workflow_id": WID, "budget_limit": 10000.0})
            
            async def attempt_payment():
                return await client.post("/pay", headers={"X-API-Key": "test-key-123"}, json={
                    "workflow_id": WID,
                    "merchant_id": "merchant_a",
                    "idempotency_key": str(uuid.uuid4()),
                    "expected": {"amount": 6000.0, "currency": "INR", "payee": "CRM Corp", "item": "test"}
                })

            res1, res2 = await asyncio.gather(
                attempt_payment(),
                attempt_payment(),
            )

            statuses = [res1.status_code, res2.status_code]
            assert statuses.count(403) == 1, f"Expected exactly one 403 Mandate Exceeded, got {statuses}"
    finally:
        await db.close_pool()

@pytest.mark.asyncio
async def test_idempotency_key_deduplication():
    await db.run_migrations()
    await db.init_pool()
    try:
        WID = f"idem-test-{uuid.uuid4().hex[:8]}"
        idem_key = str(uuid.uuid4())
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init_workflow", headers={"X-API-Key": "test-key-123"}, json={"workflow_id": WID, "budget_limit": 5000.0})
            
            payload = {
                "workflow_id": WID,
                "merchant_id": "merchant_a",
                "idempotency_key": idem_key,
                "expected": {"amount": 1000.0, "currency": "INR", "payee": "CRM", "item": "test"}
            }
            
            res1 = await client.post("/pay", headers={"X-API-Key": "test-key-123"}, json=payload)
            
            res2 = await client.post("/pay", headers={"X-API-Key": "test-key-123"}, json=payload)
            assert res2.status_code == res1.status_code
            assert res2.json() == res1.json()
            
            w_steps = db.get_workflow_steps_sync(WID)
            assert len(w_steps) == 1, f"Expected 1 step, found {len(w_steps)}"
    finally:
        await db.close_pool()


def test_workflow_steps_from_postgres():
    import inspect
    from state_log import redis_client
    source = inspect.getsource(redis_client.get_workflow_steps)
    assert "r.keys(" not in source and ".keys(" not in source, (
        "get_workflow_steps() still calls redis.keys()"
    )
