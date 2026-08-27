"""
test_level4.py
Level 4 regression tests (AI Instrumentation and Triage).
"""
import json

import pytest
from prometheus_client import REGISTRY


@pytest.mark.asyncio
async def test_extractor_metrics(monkeypatch):
    """
    Real behavioral proof, not a presence check. The previous version of
    this test only confirmed the metric *names* were registered — it never
    actually triggered a parse failure, despite its own docstring claiming
    to "verify the counters increment properly". A metric that's declared
    but never wired to the failure path it's meant to observe would have
    passed this test forever.

    This forces two consecutive parse failures (matching the real retry
    behavior in extract_policy_terms: retry once, then fail safe) by
    monkeypatching the underlying sync Gemini call to return malformed
    JSON, with no live network/API-key needed, and asserts both counters
    actually move.
    """
    from engine import policy_extractor

    before_failures = REGISTRY.get_sample_value("aegis_policy_parse_failures_total") or 0.0
    before_failsafe = REGISTRY.get_sample_value("aegis_policy_failsafe_triggers_total") or 0.0

    def _broken_stream(prompt: str) -> tuple[str, list[str]]:
        return "not valid json at all", []

    monkeypatch.setattr(policy_extractor, "_sync_stream", _broken_stream)

    result = await policy_extractor.extract_policy_terms("Some policy text.")

    after_failures = REGISTRY.get_sample_value("aegis_policy_parse_failures_total") or 0.0
    after_failsafe = REGISTRY.get_sample_value("aegis_policy_failsafe_triggers_total") or 0.0

    assert after_failures >= before_failures + 2, (
        "aegis_policy_parse_failures_total did not increment for both retry "
        "attempts on genuinely unparseable LLM output."
    )
    assert after_failsafe >= before_failsafe + 1, (
        "aegis_policy_failsafe_triggers_total did not increment when both "
        "parse attempts failed and the fail-safe default was returned."
    )
    assert result.refundable is False, (
        "Fail-safe path must default to non-refundable, per the spec's "
        "conservative-default principle."
    )


@pytest.mark.asyncio
async def test_anomaly_detector_fails_safe_without_a_client(monkeypatch):
    """
    Real behavioral proof of the "read-only, advisory-only, fails safe"
    invariant that engine/anomaly_service.py's docstring claims. The
    previous version of this test only checked that flag_anomalies exists
    and returns something dict-shaped by inspecting its type annotation —
    it never actually called the function, so it couldn't have caught a
    regression where the fail-safe path stopped defaulting to
    "not anomalous" (which is the property that makes it safe to call this
    an advisory-only signal in the first place: a broken or unavailable
    model must never silently default to "anomalous" and flag innocent
    workflows, and must never raise and take down liability-report
    generation with it).
    """
    from engine import anomaly_detector

    # Simulate "no Gemini client available" (e.g. missing API key), which is
    # the real fail-safe path get_client() already has, with no network call.
    monkeypatch.setattr(anomaly_detector, "get_client", lambda: None)

    result = await anomaly_detector.flag_anomalies(
        "wf-test", [{"step_id": "s1", "merchant_id": "merchant_a"}]
    )

    assert result["is_anomalous"] is False, (
        "flag_anomalies must fail safe to is_anomalous=False when no model "
        "client is available — defaulting to True would incorrectly flag "
        "every workflow the moment the LLM provider is unreachable."
    )
    assert isinstance(result.get("reason"), str)


@pytest.mark.asyncio
async def test_anomaly_detector_fails_safe_on_malformed_model_output(monkeypatch):
    """
    Same invariant, different failure mode: the model responds, but with
    something that isn't valid JSON (a real possibility for any LLM call,
    same class of failure the policy extractor already guards against).
    Must fail safe, not raise.
    """
    from engine import anomaly_detector

    monkeypatch.setattr(anomaly_detector, "_sync_anomaly_check", lambda steps_data: "not json")

    result = await anomaly_detector.flag_anomalies(
        "wf-test", [{"step_id": "s1", "merchant_id": "merchant_a"}]
    )

    assert result["is_anomalous"] is False, (
        "flag_anomalies must fail safe to is_anomalous=False on malformed "
        "model output rather than raising or defaulting to True."
    )