"""
engine/anomaly_detector.py
Level 4: Anomaly and Triage model for human review.
"""
import asyncio
import json
import os

# This module runs inside its own separate process (anomaly_service.py,
# started as `python -m uvicorn engine.anomaly_service:app`) — a distinct
# process from the one running policy_extractor.py/policy_service.py, with
# its own environment. policy_extractor.py calls load_dotenv() before
# reading GEMINI_API_KEY (see its own top-of-file comment); this file never
# did, so GEMINI_API_KEY below was always "" here even when correctly set
# in .env, silently falling back to get_client() returning None ("[Warn]
# GEMINI_API_KEY not set. Using dummy client.") on every single run — the
# anomaly service was never actually calling Gemini, regardless of whether
# a real key existed.
from dotenv import load_dotenv
load_dotenv()

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
{{"is_anomalous": bool, "reason": "A brief 1-2 sentence explanation if anomalous, or empty string"}}

Workflow Steps Data:
{steps_data}
"""
# The doubled {{ }} above are deliberate, not a typo: this is a Python
# str.format() template, and .format() treats ANY unescaped {...} as a
# field to substitute — not just the intended {steps_data} placeholder.
# With single braces, the literal JSON example text itself
# ({"is_anomalous": bool, ...}) was being parsed as a replacement field
# named literally '"is_anomalous"' (quotes included — format field names
# are read as raw text), and .format(steps_data=...) had no such keyword
# argument, raising KeyError('"is_anomalous"'). This is the actual root
# cause of the anomaly-detector crash — confirmed by reproducing it
# directly against this exact template — not response_schema, which was
# an earlier, plausible-but-wrong guess. Doubling the braces here tells
# .format() to treat them as literal { and } in the output rather than a
# field to fill in; {steps_data} stays single-braced since that ARE the
# one real substitution this template needs.

def _sync_anomaly_check(steps_data: str) -> str:
    client = get_client()
    if client is None:
        return '{"is_anomalous": false, "reason": "No API key"}'

    # Previously passed response_schema (a raw dict, not a types.Schema
    # object) alongside response_mime_type="application/json" — this is the
    # only Gemini call site in the codebase that did. policy_extractor.py
    # (the only other Gemini call in this system, and the one that's
    # confirmed working end-to-end) relies purely on the prompt itself
    # asking for JSON (see _ANOMALY_PROMPT above, which already does the
    # same) plus response_mime_type, with no response_schema at all. The
    # observed failure — a bare KeyError('"is_anomalous"'), with the actual
    # API call never completing (raw_output stayed None, confirmed via the
    # diagnostic logging added just before this fix) — points at the SDK's
    # own raw-dict schema handling, not at anything wrong with the request
    # itself or the response content. Dropping response_schema here matches
    # the one Gemini call path already proven reliable in this exact repo.
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=_ANOMALY_PROMPT.format(steps_data=steps_data),
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
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
    raw_output = None
    try:
        raw_output = await asyncio.to_thread(_sync_anomaly_check, steps_json)
        data = json.loads(raw_output)
        return {
            "is_anomalous": bool(data.get("is_anomalous", False)),
            "reason": str(data.get("reason", ""))
        }
    except Exception as exc:
        # raw_output is included here because this has genuinely been seen
        # to fail with a bare, uninformative exception (e.g. a KeyError
        # whose message alone doesn't say what Gemini actually returned).
        # Since response_schema is a raw dict (not a types.Schema object),
        # the SDK's own structured-output handling is the most likely
        # source if this triggers again — seeing the actual raw_output
        # text makes that immediately diagnosable instead of guesswork.
        print(f"[Warn] Anomaly detector failed: {exc} | raw_output={raw_output!r}")
        return {"is_anomalous": False, "reason": f"Detector error: {exc}"}
