"""
test_policy_extractor_streaming.py
Proves engine/policy_extractor.py's stream_callback path forwards chunks
AS Gemini generates them, not after the full response is already
buffered and then replayed in a burst.

Context: the original implementation ran the entire Gemini streaming
loop to completion inside a background thread, collected every chunk
into a list, and only replayed them through stream_callback afterward.
The chunks themselves were honestly sourced from a real streaming API
call, but the *pacing* the dashboard actually saw was indistinguishable
from a fake typing-effect animation played over a static string —
exactly what spec §9.7 region 5 says not to build, and calls "the
single moment worth the most build time" in the whole project. This
test locks in the fix: chunks must arrive with real spacing between
them, not all within a few milliseconds of each other at the very end.
"""
import asyncio
import time

import pytest

import engine.policy_extractor as pe


class _FakeChunk:
    def __init__(self, text: str):
        self.text = text


class _FakeClientWithDelay:
    """Stands in for the real Gemini client. Yields chunks with a real
    delay between them, simulating genuine token-generation pacing."""

    class models:
        @staticmethod
        def generate_content_stream(**kwargs):
            parts = [
                '{"refundable": true, ',
                '"penalty_percentage": 5.0, ',
                '"conditions": "ok"}',
            ]
            for p in parts:
                time.sleep(0.08)
                yield _FakeChunk(p)


@pytest.mark.asyncio
async def test_extract_policy_terms_streams_chunks_live(monkeypatch):
    monkeypatch.setattr(pe, "_get_client", lambda: _FakeClientWithDelay())

    arrival_times: list[float] = []
    start = time.monotonic()

    async def stream_callback(chunk: str) -> None:
        arrival_times.append(time.monotonic() - start)

    result = await pe.extract_policy_terms("some policy text", stream_callback=stream_callback)

    assert result.refundable is True
    assert result.penalty_percentage == 5.0
    assert len(arrival_times) == 3, "expected one callback invocation per chunk"

    gaps = [arrival_times[i + 1] - arrival_times[i] for i in range(len(arrival_times) - 1)]
    assert all(gap > 0.04 for gap in gaps), (
        f"chunks arrived in a burst (gaps={gaps}) instead of being forwarded live as "
        f"Gemini generated them. This is the exact regression this test exists to catch: "
        f"buffering the whole response before replaying any of it looks correct in every "
        f"way except the one that matters for the dashboard's live 'LLM Reasoning' panel."
    )


@pytest.mark.asyncio
async def test_client_construction_failure_does_not_hang_the_streaming_path(monkeypatch):
    """
    Regression test for a real, reproduced bug: _sync_stream_to_queue used
    to call _get_client() OUTSIDE its try/finally, so a client-construction
    failure (e.g. an eager API key validation error) meant the queue's
    None sentinel never got pushed — the consumer loop in
    extract_policy_terms() blocked forever on q.get() waiting for a
    sentinel that would never arrive, hanging the compensating agent's
    node indefinitely instead of failing cleanly. Confirmed hanging for 5+
    seconds before the fix and completing near-instantly after.
    """

    def raising_get_client():
        raise PermissionError("API key not valid. Please pass a valid API key.")

    monkeypatch.setattr(pe, "_get_client", raising_get_client)

    async def stream_callback(chunk: str) -> None:
        pytest.fail(f"no chunk should ever be produced when the client fails to construct, got: {chunk!r}")

    result = await asyncio.wait_for(
        pe.extract_policy_terms("policy text", stream_callback=stream_callback),
        timeout=2.0,  # generous; the bug this guards against would hang indefinitely, not just slowly
    )

    assert result.refundable is False
    assert "PermissionError" in result.conditions
    assert "API key not valid" in result.conditions


@pytest.mark.asyncio
async def test_extract_policy_terms_without_callback_still_works(monkeypatch):
    """The no-callback path (used wherever nothing needs live chunks,
    e.g. direct/offline calls) must still return the correct parsed
    result — this test protects that path from any regression introduced
    by adding the live-streaming branch alongside it."""

    class _FakeClientNoDelay:
        class models:
            @staticmethod
            def generate_content_stream(**kwargs):
                for p in ['{"refundable": false, ', '"penalty_percentage": null, "conditions": "none"}']:
                    yield _FakeChunk(p)

    monkeypatch.setattr(pe, "_get_client", lambda: _FakeClientNoDelay())

    result = await pe.extract_policy_terms("policy text")
    assert result.refundable is False
    assert result.penalty_percentage is None
    assert result.conditions == "none"
