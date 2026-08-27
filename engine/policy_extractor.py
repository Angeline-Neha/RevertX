"""
engine/policy_extractor.py
THE ONLY LLM CALL IN THE ENTIRE SYSTEM.

Uses Google Gemini (gemini-3.6-flash) via asyncio.to_thread() so it runs
cleanly inside LangGraph ainvoke() without triggering google-genai SDK
async-cleanup bugs (missing _async_httpx_client attribute on Python 3.14).
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, asdict
from typing import Callable, Awaitable

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types
from prometheus_client import Counter

POLICY_PARSE_FAILURES = Counter(
    "aegis_policy_parse_failures_total",
    "Number of times the LLM output could not be parsed as valid JSON or failed schema validation"
)
POLICY_FAILSAFE_TRIGGERS = Counter(
    "aegis_policy_failsafe_triggers_total",
    "Number of times the policy extractor fell back to the non-refundable default"
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


_EXTRACTION_PROMPT = """\
You are a strict information extractor. You will be given a merchant's
cancellation/refund policy text. Extract ONLY the following fields as JSON.
Do NOT compute any refund amount. Do NOT reason about what should happen.
Only extract what is stated.

Output schema:
{{
  "refundable": true | false,
  "penalty_percentage": number | null,
  "conditions": "short quote or paraphrase of the relevant condition"
}}

Policy text:
\"\"\"
{policy_text}
\"\"\"

Return ONLY the JSON object, nothing else.\
"""


@dataclass
class PolicyTerms:
    refundable: bool
    penalty_percentage: float | None
    conditions: str

    def to_dict(self) -> dict:
        return asdict(self)


_FAIL_SAFE = PolicyTerms(
    refundable=False,
    penalty_percentage=None,
    conditions="Failed to parse policy after 2 attempts â€” defaulting to non-refundable (fail safe).",
)


def _sync_stream(prompt: str) -> tuple[str, list[str]]:
    """Sync Gemini streaming â€” runs in a thread via asyncio.to_thread()."""
    client = _get_client()
    full_text = ""
    chunks: list[str] = []
    for chunk in client.models.generate_content_stream(
        model="gemini-3.6-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    ):
        if chunk.text:
            full_text += chunk.text
            chunks.append(chunk.text)
    return full_text, chunks


async def extract_policy_terms(
    policy_text: str,
    stream_callback: Callable[[str], Awaitable[None]] | None = None,
) -> PolicyTerms:
    """
    Async wrapper around the sync Gemini call.
    Uses asyncio.to_thread() to avoid any async httpx client init/cleanup issues.
    Replays buffered chunks through stream_callback after the call completes.
    """
    prompt = _EXTRACTION_PROMPT.format(policy_text=policy_text)

    for attempt in range(2):
        try:
            full_text, chunks = await asyncio.to_thread(_sync_stream, prompt)

            # Replay chunks to the dashboard stream panel
            if stream_callback and chunks:
                for chunk in chunks:
                    await stream_callback(chunk)

            data = json.loads(full_text)
            if "refundable" not in data:
                raise ValueError("Missing 'refundable' field")

            return PolicyTerms(
                refundable=bool(data["refundable"]),
                penalty_percentage=(
                    float(data["penalty_percentage"])
                    if data.get("penalty_percentage") is not None
                    else None
                ),
                conditions=str(data.get("conditions", "")),
            )

        except (json.JSONDecodeError, ValueError, KeyError):
            POLICY_PARSE_FAILURES.inc()
            if attempt == 1:
                POLICY_FAILSAFE_TRIGGERS.inc()
                return _FAIL_SAFE
            continue

    POLICY_FAILSAFE_TRIGGERS.inc()
    return _FAIL_SAFE

