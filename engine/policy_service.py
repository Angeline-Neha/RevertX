"""
engine/policy_service.py
Isolated microservice for running LLM policy extraction.
"""
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from engine.policy_extractor import extract_policy_terms

app = FastAPI(title="Aegis Policy Extractor Service")

class ExtractRequest(BaseModel):
    policy_text: str

@app.post("/extract")
async def extract(req: ExtractRequest):
    # Call the async function from policy_extractor
    terms = await extract_policy_terms(req.policy_text)
    # Convert pydantic model to dict
    if hasattr(terms, "model_dump"):
        return terms.model_dump()
    return terms.dict()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8004)
