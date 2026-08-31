"""
engine/policy_service.py
Isolated microservice for running LLM policy extraction.
"""
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from engine.policy_extractor import extract_policy_terms
from state_log.redis_client import publish_event

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

    # Call the async function from policy_extractor
    terms = await extract_policy_terms(req.policy_text, stream_callback=stream_callback)
    # Convert pydantic model to dict
    if hasattr(terms, "to_dict"):
        return terms.to_dict()
    return vars(terms)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8004)
