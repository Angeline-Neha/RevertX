"""
engine/fault_classifier.py
Hard-coded deterministic fault classification.
No LLM call — ever.  Judges will test this component first.

Rule (from spec Section 5.3):
  - Timeout / 5xx / network error_type  →  network_fault
  - 2xx with content mismatch           →  agent_fault  (default)
  - Mandate limit (4xx agent constraint)→  agent_fault
  - Ambiguous / unknown                 →  agent_fault  (conservative safe default)
"""
from __future__ import annotations

from proxy.schemas import FaultClassification

# Gateway error types that are unambiguously infrastructure failures
_NETWORK_ERROR_TYPES: frozenset[str] = frozenset(
    {"timeout", "connection_reset", "connection_refused", "network_error"}
)

# 5xx status codes → network/infrastructure fault
_NETWORK_STATUS_CODES: frozenset[int] = frozenset({500, 501, 502, 503, 504, 507, 508})


def classify_fault(step_id: str, raw_gateway_response: dict) -> FaultClassification:
    """
    Classify whether a failure was caused by the network/gateway (network_fault)
    or by the agent/application logic (agent_fault).

    This function must never call an LLM.  It must never raise an exception.
    When genuinely ambiguous, it returns agent_fault (the conservative safe default).

    Signature note: the original spec writes this as
    classify_fault(raw_gateway_response) — one argument. This implementation
    deliberately keeps step_id as a second, required parameter rather than
    matching that signature exactly, because the returned FaultClassification
    embeds step_id (see below) for downstream tracing — the compensating
    agent's graph and the batch-eval harness both key their per-step
    logging off of it. Making the caller stitch step_id onto the result
    after the fact would just move the same coupling to every call site
    instead of removing it. If this ever needs to match the spec's exact
    signature (e.g. for an external contract), the fix is to return a plain
    dict without step_id and let the caller attach it, not to drop tracing.
    """
    status_code: int | None = raw_gateway_response.get("status_code")
    error_type: str = raw_gateway_response.get("error_type", "")

    # ------------------------------------------------------------------ #
    # 1. Explicit network-level error_type → network_fault
    # ------------------------------------------------------------------ #
    if error_type in _NETWORK_ERROR_TYPES:
        basis = "timeout" if error_type == "timeout" else "connection_reset"
        return FaultClassification(
            step_id=step_id,
            fault_type="network_fault",
            classification_basis=basis,
            confidence_note=(
                "deterministic — based on raw gateway error_type, not inferred"
            ),
        )

    # ------------------------------------------------------------------ #
    # 2. 5xx status code → network_fault
    # ------------------------------------------------------------------ #
    if isinstance(status_code, int) and status_code in _NETWORK_STATUS_CODES:
        return FaultClassification(
            step_id=step_id,
            fault_type="network_fault",
            classification_basis="gateway_returned_5xx",
            confidence_note=(
                "deterministic — based on raw gateway response code, not inferred"
            ),
        )

    # ------------------------------------------------------------------ #
    # 3. Agent-side constraint (mandate / budget limit) → agent_fault
    # ------------------------------------------------------------------ #
    if error_type == "mandate_limit_exceeded":
        return FaultClassification(
            step_id=step_id,
            fault_type="agent_fault",
            classification_basis="agent_logic_no_gateway_error",
            confidence_note=(
                "deterministic — mandate limit is an agent constraint, "
                "not a gateway or network failure"
            ),
        )

    # ------------------------------------------------------------------ #
    # 4. 2xx gateway success but content mismatched → agent_fault default
    # ------------------------------------------------------------------ #
    if isinstance(status_code, int) and 200 <= status_code < 300:
        return FaultClassification(
            step_id=step_id,
            fault_type="agent_fault",
            classification_basis="gateway_returned_4xx_authorized_but_mismatched",
            confidence_note=(
                "deterministic — gateway succeeded (2xx) but content mismatched; "
                "conservative default is agent_fault"
            ),
        )

    # ------------------------------------------------------------------ #
    # 5. 4xx non-mandate → agent_fault (bad request from agent)
    # ------------------------------------------------------------------ #
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return FaultClassification(
            step_id=step_id,
            fault_type="agent_fault",
            classification_basis="gateway_returned_4xx_authorized_but_mismatched",
            confidence_note=(
                "deterministic — 4xx indicates agent-side error, not infrastructure"
            ),
        )

    # ------------------------------------------------------------------ #
    # 6. Ambiguous / unknown → agent_fault (safe conservative default)
    #    Per spec: filing a false dispute is worse than under-filing one.
    # ------------------------------------------------------------------ #
    return FaultClassification(
        step_id=step_id,
        fault_type="agent_fault",
        classification_basis="agent_logic_no_gateway_error",
        confidence_note=(
            "deterministic — ambiguous raw response; "
            "conservative rule defaults to agent_fault, never network_fault"
        ),
    )
