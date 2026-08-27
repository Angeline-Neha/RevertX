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

class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"fake HTTP {self.status_code}")


@pytest.mark.asyncio
async def test_policy_service_decoupled(monkeypatch):
    """
    Behavioral proof that extract_policy_terms_node runs the policy
    extraction over HTTP against the isolated service, not via an in-process
    import of engine.policy_extractor (which would run LLM inference inline).

    Actually calls the node, captures the outgoing request, and asserts on
    the returned state — not on source text.
    """
    from compensating_agent import graph as graph_mod

    calls = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            calls.append((url, json))
            return _FakeResponse(
                200, {"refundable": True, "penalty_percentage": 5, "conditions": "mock-policy"}
            )

    monkeypatch.setattr(graph_mod.httpx, "AsyncClient", _Client)
    # extract_policy_terms_node also calls _trace() -> write_compensation_trace,
    # which is a real Redis call bound into graph.py's own namespace at import
    # time (`from state_log.redis_client import write_compensation_trace`).
    # Mock it here too so this test genuinely needs no live Redis, matching
    # what it already does for the HTTP call.
    monkeypatch.setattr(graph_mod, "write_compensation_trace", lambda *a, **k: None)

    state = {
        "workflow_id": "wf-policy-test",
        "policy_text": "Refunds allowed within 7 days, 5% penalty.",
    }
    result = await graph_mod.extract_policy_terms_node(state)

    assert calls, "extract_policy_terms_node never made an HTTP call — it may be calling the extractor in-process instead"
    called_url, called_body = calls[0]
    assert "/extract" in called_url, f"expected a call to the policy service's /extract endpoint, got {called_url}"
    assert called_body == {"policy_text": state["policy_text"]}

    assert result["policy_terms"]["refundable"] is True
    assert result["policy_terms"]["penalty_percentage"] == 5


@pytest.mark.asyncio
async def test_policy_service_failure_falls_back_safely(monkeypatch):
    """If the isolated policy service is unreachable, the node must fail
    safe (non-refundable, no penalty) rather than crash or hang."""
    from compensating_agent import graph as graph_mod

    class _DeadClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(graph_mod.httpx, "AsyncClient", _DeadClient)
    monkeypatch.setattr(graph_mod, "write_compensation_trace", lambda *a, **k: None)

    state = {"workflow_id": "wf-policy-fail", "policy_text": "some policy"}
    result = await graph_mod.extract_policy_terms_node(state)

    assert result["policy_terms"]["refundable"] is False
    assert result["policy_terms"]["penalty_percentage"] is None


@pytest.mark.asyncio
async def test_anomaly_service_decoupled(monkeypatch):
    """
    Behavioral proof that the anomaly check is (a) made over HTTP to the
    isolated service, (b) fire-and-forget so it can never delay/block
    liability report generation, and (c) publishes an anomaly_detected event
    when the service flags one.

    Phase 1 finding: a second LLM call (the anomaly/triage check) was
    previously inlined via `from engine.anomaly_detector import
    flag_anomalies`, running LLM inference in-process. This test exercises
    the real call path instead of grepping source text for "asyncio" /
    "8005" substrings.
    """
    from compensating_agent import graph as graph_mod

    calls = []
    published = []
    release_event = asyncio.Event()

    class _SlowAnomalyClient:
        """Simulates a slow anomaly service to prove the call is genuinely
        backgrounded rather than awaited inline."""

        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            calls.append((url, json))
            await release_event.wait()  # would hang forever if awaited inline
            return _FakeResponse(200, {"is_anomalous": True, "reason": "test-flagged"})

    monkeypatch.setattr(graph_mod.httpx, "AsyncClient", _SlowAnomalyClient)
    monkeypatch.setattr(graph_mod, "publish_event", lambda wid, kind, payload: published.append((wid, kind, payload)))
    monkeypatch.setattr(graph_mod, "write_compensation_trace", lambda *a, **k: None)

    import state_log.redis_client as redis_client_mod
    monkeypatch.setattr(redis_client_mod, "get_workflow_steps", lambda _wid: [])

    state = {
        "workflow_id": "wf-anomaly-test",
        "failing_step": {"step_id": "s1", "expected": {}, "actual": {}},
        "compensation_results": [{"step_id": "s1", "outcome": "refunded"}],
    }

    import time
    start = time.monotonic()
    result = await graph_mod.generate_liability_report_node(state)
    elapsed = time.monotonic() - start

    # The liability report must return immediately — the anomaly service
    # call is still blocked on release_event at this point.
    assert elapsed < 2.0, "generate_liability_report_node blocked waiting on the anomaly service call"
    assert result["liability_report"]["workflow_id"] == "wf-anomaly-test"
    assert not any(k == "anomaly_detected" for _, k, _ in published), (
        "anomaly_detected was published before the (slow) anomaly service even responded — "
        "the call is not actually backgrounded"
    )

    # Now let the background call resolve and confirm the flag surfaces.
    release_event.set()
    await asyncio.sleep(0.05)

    assert calls, "no HTTP call was made to the anomaly service"
    called_url, called_body = calls[0]
    assert "flag_anomalies" in called_url
    assert called_body["workflow_id"] == "wf-anomaly-test"

    assert any(k == "anomaly_detected" for _, k, _ in published), (
        "anomaly service flagged is_anomalous=True but no anomaly_detected event was published"
    )


@pytest.mark.asyncio
async def test_anomaly_service_failure_does_not_affect_liability_report(monkeypatch):
    """If the anomaly service errors or is unreachable, the liability report
    must still have already been generated and published untouched."""
    from compensating_agent import graph as graph_mod

    class _DeadAnomalyClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("connection refused")

    published = []
    monkeypatch.setattr(graph_mod.httpx, "AsyncClient", _DeadAnomalyClient)
    monkeypatch.setattr(graph_mod, "publish_event", lambda wid, kind, payload: published.append((wid, kind, payload)))
    monkeypatch.setattr(graph_mod, "write_compensation_trace", lambda *a, **k: None)

    import state_log.redis_client as redis_client_mod
    monkeypatch.setattr(redis_client_mod, "get_workflow_steps", lambda _wid: [])

    state = {
        "workflow_id": "wf-anomaly-down",
        "failing_step": {"step_id": "s1", "expected": {}, "actual": {}},
        "compensation_results": [{"step_id": "s1", "outcome": "refunded"}],
    }

    result = await graph_mod.generate_liability_report_node(state)
    assert result["liability_report"]["workflow_id"] == "wf-anomaly-down"

    liability_events = [p for _, k, p in published if k == "final_output"]
    assert liability_events, "liability report was never published"

    await asyncio.sleep(0.05)
    assert not any(k == "anomaly_detected" for _, k, _ in published), (
        "anomaly_detected should never fire when the anomaly service call failed"
    )
