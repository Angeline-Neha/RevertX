"""
test_level5.py
Level 5 regression tests.
"""
import pytest
from httpx import AsyncClient, ASGITransport
import uuid
import asyncio

import db.client as db
from proxy.mcp_proxy import app

@pytest.mark.asyncio
async def test_ledger_is_partitioned():
    """
    Ensure the transaction_steps table uses declarative partitioning.
    """
    await db.run_migrations()
    await db.init_pool()
    try:
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT relkind FROM pg_class WHERE relname = 'transaction_steps'")
            assert row is not None, "transaction_steps table does not exist"
            assert row["relkind"] in (b'p', 'p'), f"transaction_steps is not partitioned (relkind={row['relkind']}, expected 'p')"
    finally:
        await db.close_pool()

@pytest.mark.asyncio
async def test_rate_limits_enforced():
    """
    Fire 60 requests quickly to /init_workflow (or /pay) to trigger the rate limiter.
    """
    await db.run_migrations()
    await db.init_pool()
    try:
        WID = f"rate-limit-test-{uuid.uuid4().hex[:8]}"
        headers = {"X-API-Key": "test-key-123"}
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/init_workflow", json={"workflow_id": WID, "budget_limit": 100000.0}, headers=headers)
            
            async def make_request(i):
                return await client.post("/pay", json={
                    "workflow_id": WID,
                    "merchant_id": "merchant_a",
                    "expected": {"amount": 10.0},
                    "idempotency_key": f"{WID}-step-{i}"
                }, headers=headers)
                
            tasks = [make_request(i) for i in range(60)]
            results = await asyncio.gather(*tasks)
            
            status_codes = [r.status_code for r in results]
            
            assert 429 in status_codes, "Rate limiter did not return 429 Too Many Requests after 60 concurrent calls"
    finally:
        await db.close_pool()

def test_policy_service_decoupled():
    """
    Ensure graph.py no longer directly imports engine.policy_extractor.
    """
    import inspect
    from compensating_agent import graph
    
    source = inspect.getsource(graph)
    
    assert "from engine.policy_extractor" not in source and "import engine.policy_extractor" not in source, (
        "graph.py is still directly importing the policy extractor, meaning it is running "
        "LLM inference in the same process."
    )
    
    assert "httpx" in source and "8004" in source or "policy-extractor" in source, (
        "graph.py does not appear to be making an HTTP request to the isolated policy service."
    )
