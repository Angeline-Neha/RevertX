"""
engine/anomaly_detector.py
Level 4: Anomaly and Triage model for human review.
"""
import asyncio
import json
import os
from google import genai
from google.genai import types

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
_client: genai.Client | None = None

def get_client() -> genai.Client:
    global _client
    if _client is None:
        key = GEMINI_API_KEY
        if not key:
            print("[Warn] GEMINI_API_KEY not set. Using dummy client.")
            return None
        _client = genai.Client(api_key=key)
    return _client

_ANOMALY_PROMPT = """
You are a fraud and anomaly detection triage model.
You will be provided with a sequence of transaction steps for a workflow.
Your goal is to determine if this pattern of activity is anomalous or suspicious, requiring human review.

Look for:
- Very rapid successive identical payments that were rejected.
- Large spikes in amounts.
- Unusual merchant routing logic that doesn't make business sense.

Output valid JSON only matching this schema:
{"is_anomalous": bool, "reason": "A brief 1-2 sentence explanation if anomalous, or empty string"}

Workflow Steps Data:
{steps_data}
"""

def _sync_anomaly_check(steps_data: str) -> str:
    client = get_client()
    if client is None:
        return '{"is_anomalous": false, "reason": "No API key"}'

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=_ANOMALY_PROMPT.format(steps_data=steps_data),
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema={
                "type": "OBJECT",
                "properties": {
                    "is_anomalous": {"type": "BOOLEAN"},
                    "reason": {"type": "STRING"}
                },
                "required": ["is_anomalous", "reason"]
            }
        ),
    )
    return response.text or '{"is_anomalous": false, "reason": "Empty response"}'

async def flag_anomalies(workflow_id: str, steps: list[dict]) -> dict:
    """
    Evaluates the workflow steps for anomalies.
    Returns a read-only dict: {"is_anomalous": bool, "reason": str}
    This is for human review ONLY and does not affect the actual transaction logic.
    """
    steps_json = json.dumps(steps, indent=2)
    try:
        raw_output = await asyncio.to_thread(_sync_anomaly_check, steps_json)
        data = json.loads(raw_output)
        return {
            "is_anomalous": bool(data.get("is_anomalous", False)),
            "reason": str(data.get("reason", ""))
        }
    except Exception as exc:
        print(f"[Warn] Anomaly detector failed: {exc}")
        return {"is_anomalous": False, "reason": f"Detector error: {exc}"}
