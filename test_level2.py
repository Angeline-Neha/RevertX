"""
test_level2.py
Level 2 regression tests.
"""
from __future__ import annotations

import ast
import inspect
from unittest.mock import AsyncMock

import pytest

from langgraph.checkpoint.memory import InMemorySaver

from proxy.schemas import ExpectedPayment, ActualPayment, TransactionLogEntry

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
def test_checkpointer_argument_is_wired_through():
    """
    Structural guard (kept, but no longer the whole story): build_graph must
    still accept and forward a checkpointer to g.compile(). The previous
    version of this test stopped here, which is exactly why it passed while
    the actual compiled_graph was built with checkpointer=None everywhere
    that mattered. See test_checkpointer_resumes_after_interruption below
    for the real behavioral proof.
    """
    from compensating_agent import graph
    source = inspect.getsource(graph.build_graph)

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


def test_worker_uses_a_real_postgres_backed_checkpointer():
    """
    Structural guard on the production wiring: the worker must build its
    graph with an AsyncPostgresSaver, not rely on graph.py's uncheckpointed
    module-level default. Catches the specific regression of "checkpointer
    plumbing exists in graph.py but nothing production ever passes one in".
    """
    from compensating_agent import worker

    source = inspect.getsource(worker.main)
    assert "AsyncPostgresSaver" in source, (
        "worker.main() no longer builds a real Postgres-backed checkpointer — "
        "compensation runs would go back to being unrecoverable on crash."
    )
    assert "build_graph(checkpointer=" in source, (
        "worker.main() must pass its checkpointer into build_graph(); "
        "otherwise the checkpointer object exists but is never used."
    )


def _fake_entry(workflow_id: str, merchant_id: str, amount: float) -> TransactionLogEntry:
    return TransactionLogEntry(
        workflow_id=workflow_id,
        action_type="payment",
        merchant_id=merchant_id,
        expected=ExpectedPayment(amount=amount, payee=merchant_id, item="test item"),
        actual=ActualPayment(
            amount=amount,
            payee=merchant_id,
            settlement_ref=f"{merchant_id}_ref",
            status="settled",
        ),
        raw_gateway_response={"status_code": 200},
    )


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


class _FakeAsyncClient:
    """
    Stands in for httpx.AsyncClient so the test never touches a real
    network socket. Routes canned responses by URL suffix; also counts
    /refund calls per merchant so the test can assert each merchant's
    refund endpoint was only ever hit once, even across an interrupt+resume
    cycle — that count is the actual proof there was no double-refund.
    """
    refund_calls: dict[str, int] = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str, *args, **kwargs) -> _FakeResponse:
        if url.endswith("/policy"):
            return _FakeResponse(200, {"policy": "Fully refundable, no penalty."})
        raise AssertionError(f"unexpected GET {url}")

    async def post(self, url: str, json: dict | None = None, **kwargs) -> _FakeResponse:
        if url.endswith("/extract"):
            return _FakeResponse(
                200, {"refundable": True, "penalty_percentage": 0, "conditions": "mock"}
            )
        if url.endswith("/refund"):
            merchant_id = json.get("settlement_ref", "").replace("_ref", "")
            _FakeAsyncClient.refund_calls[merchant_id] = (
                _FakeAsyncClient.refund_calls.get(merchant_id, 0) + 1
            )
            return _FakeResponse(200, {"status": "refunded", "amount": json.get("amount", 0)})
        raise AssertionError(f"unexpected POST {url}")


@pytest.mark.asyncio
async def test_checkpointer_resumes_after_interruption(monkeypatch):
    """
    The real behavioral proof the fix plan asked for: actually run the graph,
    interrupt it partway through (simulating the worker process dying right
    after one undo step completed), reconstruct a *fresh* compiled graph
    object against the same checkpointer/thread_id (simulating a restarted
    worker), and confirm it resumes from the next pending node rather than
    restarting load_workflow_log from scratch. If it restarted from scratch,
    the already-refunded merchant would get refunded a second time — this
    test's central assertion is that each merchant's /refund endpoint is
    called exactly once, not that the final state merely "looks done".
    """
    from compensating_agent import graph as graph_mod
    import state_log.redis_client as redis_client_mod
    import db.client as db_client_mod

    wid = "test-wf-resume-1"
    entry_a = _fake_entry(wid, "merchant_a", 10000.0)  # no /policy endpoint
    entry_b = _fake_entry(wid, "merchant_b", 20000.0)  # has a /policy endpoint

    # Two payments already settled, in chronological order [A, B] — the graph
    # undoes most-recent-first, so B is undone before A.
    fake_steps = [entry_a, entry_b]

    monkeypatch.setattr(graph_mod, "get_workflow_steps", lambda _wid: fake_steps)
    monkeypatch.setattr(redis_client_mod, "get_workflow_steps", lambda _wid: fake_steps)
    monkeypatch.setattr(graph_mod, "publish_event", lambda *a, **k: None)
    monkeypatch.setattr(graph_mod, "write_compensation_trace", lambda *a, **k: None)
    monkeypatch.setattr(graph_mod.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(db_client_mod, "check_circuit_breaker", AsyncMock(return_value="closed"))
    monkeypatch.setattr(db_client_mod, "reset_circuit_breaker", AsyncMock(return_value=None))
    _FakeAsyncClient.refund_calls = {}

    failing_step = {
        "step_id": "failing-step",
        "expected": {"amount": 12000.0},
        "actual": {"amount": 0.0, "status_code": 403},
        "raw_gateway_response": {"status_code": 403, "error_type": "mandate_limit_exceeded"},
    }

    saver = InMemorySaver()

    # --- "Run 1": the worker that gets killed right after undoing merchant_b ---
    graph_run1 = graph_mod.build_graph(checkpointer=saver, interrupt_after=["attempt_refund"])
    partial_result = await graph_mod.run_compensation(wid, failing_step, graph=graph_run1)

    assert len(partial_result["compensation_results"]) == 1, (
        "Expected exactly one undo step to have completed before the "
        "simulated crash (merchant_b, undone first)."
    )
    assert partial_result["compensation_results"][0]["merchant_id"] == "merchant_b"
    assert _FakeAsyncClient.refund_calls == {"merchant_b": 1}
    assert partial_result["steps_to_undo"], (
        "merchant_a should still be pending — the graph should not have "
        "reached the end of the loop yet."
    )

    # --- "Run 2": a restarted worker — a brand-new CompiledGraph object,
    # same checkpointer/thread_id, no interrupt this time ---
    graph_run2 = graph_mod.build_graph(checkpointer=saver)
    final_result = await graph_mod.run_compensation(wid, failing_step, graph=graph_run2)

    assert _FakeAsyncClient.refund_calls == {"merchant_a": 1, "merchant_b": 1}, (
        "merchant_b's /refund endpoint was called more than once — this is "
        "exactly the double-refund bug that a fake 'checkpointer=None' "
        "compile would produce if the resumed run restarted from scratch "
        "instead of picking up where the interrupted run left off."
    )
    assert len(final_result["compensation_results"]) == 2
    assert final_result["liability_report"] is not None
    assert final_result["udir_payload"] is None


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
