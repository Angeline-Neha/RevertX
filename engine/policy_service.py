"""
engine/policy_service.py
Isolated microservice for running LLM policy extraction.
"""
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
import structlog
from engine.policy_extractor import extract_policy_terms, PolicyTerms
from state_log.redis_client import publish_event

logger = structlog.get_logger()

app = FastAPI(title="Aegis Policy Extractor Service")

class ExtractRequest(BaseModel):
    policy_text: str
    # Optional: when set, real Gemini stream chunks are published to
    # workflow_id's Redis channel as "llm_stream_chunk" events, the same
    # way every other node in compensating_agent/graph.py publishes its
    # own events — this service being an isolated HTTP microservice
    # (Phase 1) doesn't change that pattern, it just means the publish
    # happens from here instead of from graph.py. Without a workflow_id,
    # this endpoint still works (e.g. for direct/manual testing) but the
    # dashboard's "LLM Reasoning" panel has nothing to show, since no
    # caller told this service which channel to publish on — this was
    # previously always the case, since graph.py's HTTP call never sent
    # one and stream_callback was consequently never wired to anything.
    workflow_id: str = ""

@app.post("/extract")
async def extract(req: ExtractRequest):
    stream_callback = None
    if req.workflow_id:
        async def stream_callback(chunk: str) -> None:
            publish_event(req.workflow_id, "llm_stream_chunk", {"chunk": chunk})

    # extract_policy_terms() already catches everything it knows about
    # (parse failures and API/network failures alike) and returns a
    # fail-safe PolicyTerms rather than raising. This try/except is
    # defense-in-depth only, for anything genuinely unanticipated (e.g. a
    # bug in this service's own glue code), so this endpoint never returns
    # a bare 500 that would (a) surface to graph.py as an opaque "Server
    # error '500 Internal Server Error'" with the real cause discarded,
    # and (b) leave the dashboard's "LLM Reasoning" panel stuck on
    # "Waiting for policy extraction call..." forever with no explanation.
    try:
        terms = await extract_policy_terms(req.policy_text, stream_callback=stream_callback)
    except Exception as exc:
        logger.error("policy_service.extract_unexpected_failure", error_type=type(exc).__name__, error=str(exc))
        terms = PolicyTerms(
            refundable=False,
            penalty_percentage=None,
            conditions=f"Unexpected policy_service failure — defaulting to non-refundable "
                       f"(fail safe). Reason: {type(exc).__name__}: {exc}",
            is_fail_safe=True,
        )

    # Convert pydantic model to dict
    if hasattr(terms, "to_dict"):
        return terms.to_dict()
    return vars(terms)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8004)
