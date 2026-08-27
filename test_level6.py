"""
test_level6.py
Level 6 regression tests (Observability).
"""
import pytest
from prometheus_client import REGISTRY
import json
import io
import structlog
from proxy.mcp_proxy import app
from httpx import AsyncClient, ASGITransport

@pytest.mark.asyncio
async def test_structured_logs():
    """
    Test that the logger outputs JSON with a workflow_id.
    """
    out = io.StringIO()
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(file=out)
    )
    log = structlog.get_logger()
    
    log.info("test_message", workflow_id="123")
    output = out.getvalue()
    
    assert "{" in output
    parsed = json.loads(output)
    assert parsed["workflow_id"] == "123"
    assert parsed["event"] == "test_message"

@pytest.mark.asyncio
async def test_false_dispute_alert():
    """
    Ensure the false dispute metric increments when a 4xx error is routed to network_fault.
    """
    from compensating_agent.graph import classify_and_route_node
    
    state = {
        "workflow_id": "fd-test-1",
        "failing_step": {
            "step_id": "test-step",
            "actual": {
                "status_code": 403,
                "error": "mandate_limit_exceeded"
            }
        },
        "compensation_results": []
    }
    
    from compensating_agent import graph
    original = graph.classify_fault
    try:
        from proxy.schemas import FaultClassification
        graph.classify_fault = lambda x, y: FaultClassification(
            step_id="test-step",
            fault_type="network_fault",
            classification_basis="mock",
            confidence_note="mock"
        )

        # Get baseline metric value
        before = REGISTRY.get_sample_value('aegis_false_disputes_total') or 0.0

        # Run node
        classify_and_route_node(state)

        after = REGISTRY.get_sample_value('aegis_false_disputes_total') or 0.0

        assert after > before, "False dispute metric did not increment"

    finally:
        graph.classify_fault = original

@pytest.mark.asyncio
async def test_metrics_endpoint():
    """
    Check if /metrics endpoint exists and exposes Prometheus data.
    """
    from compensating_agent import graph # ensure metric is registered
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/metrics/")
        assert resp.status_code == 200
    assert "aegis_false_disputes_total" in resp.text
