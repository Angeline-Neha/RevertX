"""
engine/anomaly_service.py
Isolated microservice for the anomaly/triage LLM call.

This exists for the same reason engine/policy_service.py exists: the
compensating agent's graph process must never make an inline LLM call
directly. Every model call in this system lives behind its own HTTP
service boundary, with its own timeout and its own fail-safe default,
so a slow or failing model provider can degrade one advisory signal
without ever blocking the payment-critical compensation path.

This is read-only, advisory-only. Nothing in compensating_agent/graph.py
gates a refund, a UDIR filing, or a liability report on this service's
output — it only attaches an extra "is_anomalous" flag to the liability
report for a human reviewer, and it fails safe to "not anomalous" on any
error, exactly like policy extraction fails safe to "not refundable".
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

from engine.anomaly_detector import flag_anomalies

app = FastAPI(title="Aegis Anomaly/Triage Service")


class FlagAnomaliesRequest(BaseModel):
    workflow_id: str
    steps: list[dict]


@app.post("/flag_anomalies")
async def flag_anomalies_endpoint(req: FlagAnomaliesRequest) -> dict:
    return await flag_anomalies(req.workflow_id, req.steps)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8005)
