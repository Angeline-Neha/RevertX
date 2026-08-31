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
import queue
from dataclasses import dataclass, asdict
from typing import Callable, Awaitable

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types
from prometheus_client import Counter
import structlog

logger = structlog.get_logger()

POLICY_PARSE_FAILURES = Counter(
    "aegis_policy_parse_failures_total",
    "Number of times the LLM output could not be parsed as valid JSON or failed schema validation"
)
POLICY_API_FAILURES = Counter(
    "aegis_policy_api_failures_total",
    "Number of times the underlying Gemini API call itself failed (auth, quota, network, "
    "server error) as opposed to returning a response that failed to parse"
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
    conditions="Failed to parse policy after 2 attempts — defaulting to non-refundable (fail safe).",
)


def _fail_safe_for(exc: Exception) -> PolicyTerms:
    """Fail-safe result that carries the real reason, instead of the
    generic parse-failure message, when the failure was the Gemini API
    call itself (auth, quota, invalid model, network) rather than a
    malformed response. Downstream (compute_refund_amount_node) only
    reads refundable/penalty_percentage, so this is purely for
    diagnosability in the trace log and dashboard — it changes nothing
    about the fail-safe behavior itself."""
    return PolicyTerms(
        refundable=False,
        penalty_percentage=None,
        conditions=f"LLM API call failed after 2 attempts — defaulting to non-refundable "
                   f"(fail safe). Reason: {type(exc).__name__}: {exc}",
    )


def _sync_stream(prompt: str) -> tuple[str, list[str]]:
    """Sync Gemini streaming — runs in a thread via asyncio.to_thread().
    Used only when there's no stream_callback to forward chunks to live;
    see _sync_stream_to_queue below for the genuinely live-paced path."""
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


def _sync_stream_to_queue(prompt: str, q: "queue.Queue[str | None]") -> str:
    """
    Same Gemini call as _sync_stream, but pushes each chunk onto q the
    moment it arrives instead of collecting them all before returning.

    Runs in a background thread (via asyncio.to_thread). The async caller
    concurrently drains q and awaits stream_callback(chunk) for each one —
    that's what makes the dashboard's "LLM Reasoning" panel show genuine
    token-by-token output as Gemini generates it, rather than the entire
    response appearing in one burst once the call is already done (which
    is what the previous version of extract_policy_terms() did: it fully
    collected every chunk inside this same kind of thread call, and only
    replayed them through stream_callback afterward — functionally
    equivalent to the "typing-effect animation faked over a static
    string" the spec explicitly says not to build, even though the chunks
    themselves were honestly sourced from a real streaming API call).

    Puts a final None sentinel once the stream ends (success or error) so
    the consumer knows to stop waiting.
    """
    client = _get_client()
    full_text = ""
    try:
        for chunk in client.models.generate_content_stream(
            model="gemini-3.6-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        ):
            if chunk.text:
                full_text += chunk.text
                q.put(chunk.text)
    finally:
        q.put(None)
    return full_text


async def extract_policy_terms(
    policy_text: str,
    stream_callback: Callable[[str], Awaitable[None]] | None = None,
) -> PolicyTerms:
    """
    Async wrapper around the sync Gemini call.
    Uses asyncio.to_thread() to avoid any async httpx client init/cleanup issues.
    When stream_callback is given, forwards each chunk to it AS Gemini
    generates it (see _sync_stream_to_queue) rather than buffering the
    full response before replaying anything.
    """
    prompt = _EXTRACTION_PROMPT.format(policy_text=policy_text)

    last_api_exc: Exception | None = None

    for attempt in range(2):
        try:
            if stream_callback is not None:
                q: "queue.Queue[str | None]" = queue.Queue()
                producer = asyncio.create_task(
                    asyncio.to_thread(_sync_stream_to_queue, prompt, q)
                )
                while True:
                    chunk = await asyncio.to_thread(q.get)
                    if chunk is None:
                        break
                    await stream_callback(chunk)
                full_text = await producer
            else:
                full_text, _chunks = await asyncio.to_thread(_sync_stream, prompt)

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

        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            # The API call succeeded but returned something we couldn't use.
            POLICY_PARSE_FAILURES.inc()
            logger.warning(
                "policy_extractor.parse_failure", attempt=attempt, error=str(exc)
            )
            if attempt == 1:
                POLICY_FAILSAFE_TRIGGERS.inc()
                return _FAIL_SAFE
            continue

        except Exception as exc:
            # The API call itself failed — auth, quota, invalid model, a
            # transient network/server error, or anything else the SDK
            # can raise (e.g. google.genai.errors.APIError and its
            # ClientError/ServerError subclasses, or a connection error
            # below the SDK). Previously only (JSONDecodeError, ValueError,
            # KeyError) were caught here, so any real API failure bypassed
            # the retry/fail-safe path entirely and propagated all the way
            # up through policy_service's /extract endpoint as an
            # unhandled 500 — which is what a caller (graph.py) sees as
            # just "Server error '500 Internal Server Error'", with the
            # actual reason (bad key, wrong model name, quota exceeded,
            # no network route to Gemini, etc.) never logged or surfaced
            # anywhere. Catching it here means: (a) it gets the same
            # 2-attempt retry as a parse failure, since a chunk of these
            # are transient, (b) it's logged with the real exception type
            # and message where the service's own terminal can show it,
            # and (c) the fail-safe result itself carries the reason via
            # _fail_safe_for(), so it's visible in the compensation trace
            # / dashboard log instead of just "error" with no detail.
            last_api_exc = exc
            POLICY_API_FAILURES.inc()
            logger.error(
                "policy_extractor.api_call_failed",
                attempt=attempt,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            if attempt == 1:
                POLICY_FAILSAFE_TRIGGERS.inc()
                return _fail_safe_for(exc)
            continue

    if last_api_exc is not None:
        POLICY_FAILSAFE_TRIGGERS.inc()
        return _fail_safe_for(last_api_exc)

    POLICY_FAILSAFE_TRIGGERS.inc()
    return _FAIL_SAFE

