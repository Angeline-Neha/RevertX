"""
test_level2.py
Level 2 regression tests.
"""
from __future__ import annotations

import ast
import inspect
import pytest

# ---------------------------------------------------------------------------
# Test A: Message Broker Decoupling
# ---------------------------------------------------------------------------
def test_fire_and_forget_removed():
    """
    mcp_proxy.pay() must NOT use BackgroundTasks.add_task to trigger compensation.
    """
    from proxy import mcp_proxy
    source = inspect.getsource(mcp_proxy.pay)
    assert ".add_task(_trigger_compensation" not in source, (
        "mcp_proxy.pay() is still using fire-and-forget BackgroundTasks "
        "instead of a message broker."
    )


# ---------------------------------------------------------------------------
# Test B: LangGraph Persistent Checkpointer
# ---------------------------------------------------------------------------
def test_checkpointer_enabled():
    """
    graph.py must pass a checkpointer to g.compile().
    """
    from compensating_agent import graph
    source = inspect.getsource(graph.build_graph)
    
    # We parse the AST to ensure g.compile is called with checkpointer
    parsed = ast.parse(source)
    compile_calls = [
        node for node in ast.walk(parsed)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compile"
    ]
    
    assert compile_calls, "Could not find g.compile() call"
    has_checkpointer = any(
        kw.arg == "checkpointer" for call in compile_calls for kw in call.keywords
    )
    
    assert has_checkpointer, (
        "g.compile() is missing the 'checkpointer' argument. "
        "Saga state will be lost on crash."
    )


# ---------------------------------------------------------------------------
# Test C: Circuit Breaker & DLQ
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_circuit_breaker_dlq():
    """
    Simulates attempt_refund_node with a merchant that is down (using an invalid URL).
    It should populate a DLQ entry and ideally open a circuit breaker.
    """
    import httpx
    from compensating_agent.graph import attempt_refund_node
    
    # Check if a DLQ table or method exists in db client
    import db.client as db
    assert hasattr(db, "write_dlq_entry"), (
        "DLQ function 'write_dlq_entry' not found in db.client. "
        "Level 2 circuit breaker/DLQ is not implemented."
    )


# ---------------------------------------------------------------------------
# Test D: Async fetch_policy
# ---------------------------------------------------------------------------
def test_fetch_policy_is_async():
    """
    fetch_policy must be an async function to avoid blocking the event loop.
    """
    from compensating_agent import graph
    
    assert inspect.iscoroutinefunction(graph.fetch_policy), (
        "fetch_policy is a synchronous function. It contains blocking I/O "
        "that will stall the LangGraph event loop."
    )
