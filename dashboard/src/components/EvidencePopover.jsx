// EvidencePopover.jsx
// Feature E — click-to-expand evidence trail. Every field rendered here
// already exists in an event the backend publishes today (payment_attempt /
// reconciliation_result / mandate_exceeded on the payment side,
// compensation_trace / math_computation / refund_success / refund_failed /
// refund_halted on the compensation side) — App.jsx just retains it in
// paymentEvidence / compensationEvidence instead of only logging it. This
// component's only job is turning that retained data into a plain-English
// "what was called, what came back, why Aegis decided what it decided"
// readout, not fetching or inventing anything new.

const NODE_LABELS = {
  fetch_policy: "Fetch Policy",
  extract_policy: "Extract Terms (LLM)",
  compute_refund_amount: "Compute Refund",
  attempt_refund: "Attempt Refund",
  classify_and_route: "Classify & Route",
  generate_udir_payload: "UDIR Payload",
  generate_liability_report: "Liability Report",
};

function fmtInr(n) {
  return n == null ? "—" : `₹${Number(n).toLocaleString("en-IN")}`;
}

function PaymentEvidence({ evidence }) {
  const { payee, amount, item, requestStatus, match, mismatchType, budgetUsed, budgetLimit } = evidence;

  let whatCalled = `POST /pay → ${payee || "merchant"} for ${fmtInr(amount)} (${item || "item"})`;
  let whatCameBack;
  let why;

  if (requestStatus === "mandate_exceeded") {
    whatCameBack = "403 — proxy rejected this before it ever reached the merchant.";
    why = `Paying this would have pushed spend to ${fmtInr(budgetUsed)} against a ${fmtInr(budgetLimit)} mandate — the proxy refused to authorize it, so nothing was actually charged.`;
  } else if (requestStatus === "settled") {
    whatCameBack = "200 — merchant confirmed the charge, and it reconciled cleanly against what was expected.";
    why = "Amount and settlement details matched what the primary agent expected. No follow-up needed.";
  } else if (requestStatus === "mismatch") {
    whatCameBack = `200 — merchant confirmed a charge, but it didn't reconcile (${mismatchType || "mismatch"}).`;
    why = "The actual charge didn't match what was expected — worth a closer look even though the merchant says it went through.";
  } else {
    whatCameBack = "Waiting on the merchant...";
    why = "Call is in flight.";
  }

  return (
    <>
      <Row label="What was called">{whatCalled}</Row>
      <Row label="What came back">{whatCameBack}</Row>
      <Row label="Why" emphasize>{why}</Row>
    </>
  );
}

function CompensationEvidence({ nodeId, evidence }) {
  const label = NODE_LABELS[nodeId] || nodeId?.replace(/_/g, " ");
  const merchant = evidence.merchantId || evidence.merchant_id;

  if (nodeId === "fetch_policy") {
    const whatCalled = `GET ${merchant || "merchant"}/policy`;
    if (evidence.status === "skip") {
      return (
        <>
          <Row label="What was called">{whatCalled}</Row>
          <Row label="What came back">Skipped — {evidence.reason || "this merchant has no /policy endpoint"}.</Row>
          <Row label="Why" emphasize>No stated policy to read, so the next step handles this merchant without one (typically a full, unconditional refund).</Row>
        </>
      );
    }
    if (evidence.status === "error") {
      return (
        <>
          <Row label="What was called">{whatCalled}</Row>
          <Row label="What came back">The call itself failed: {evidence.error || "unknown error"}.</Row>
          <Row label="Why" emphasize>Fail-safe rule: an unreachable policy is treated as non-refundable rather than assumed to be a full refund — safer to under-recover than to hand back money that wasn't actually owed.</Row>
        </>
      );
    }
    if (evidence.policy_text) {
      return (
        <>
          <Row label="What was called">{whatCalled}</Row>
          <Row label="What came back">"{evidence.policy_text}"</Row>
          {evidence.sanitization_truncated || (evidence.sanitization_injection_flags && evidence.sanitization_injection_flags.length > 0) ? (
            <Row label="Note">This text was sanitized before being trusted (truncated: {String(!!evidence.sanitization_truncated)}, flags: {(evidence.sanitization_injection_flags || []).join(", ") || "none"}).</Row>
          ) : null}
          <Row label="Why" emphasize>This is the merchant's own stated refund policy, read live — the next step extracts the actual terms (refundable? penalty?) from this exact text.</Row>
        </>
      );
    }
    return <Row label="Status">Waiting on the merchant's /policy endpoint...</Row>;
  }

  if (nodeId === "extract_policy") {
    if (evidence.status === "error") {
      return (
        <>
          <Row label="What was called">Policy-extraction LLM service</Row>
          <Row label="What came back">Failed: {evidence.error || "unknown error"}.</Row>
          <Row label="Why" emphasize>Same fail-safe rule as above — defaulted to non-refundable rather than guessing.</Row>
        </>
      );
    }
    if (evidence.status === "end") {
      return (
        <>
          <Row label="What was called">Policy-extraction LLM service, given the raw policy text above</Row>
          <Row label="What came back">
            refundable: {String(!!evidence.refundable)}
            {evidence.penalty_percentage != null ? `, penalty: ${evidence.penalty_percentage}%` : ""}
          </Row>
          {evidence.conditions && <Row label="Conditions">{evidence.conditions}</Row>}
          <Row label="Why" emphasize>{evidence.is_fail_safe ? "This is a fail-safe default, not a confident read of the policy text." : "The LLM turned the merchant's plain-English policy into structured terms the deterministic math step below can actually compute with."}</Row>
        </>
      );
    }
    return <Row label="Status">Extracting structured terms from the policy text...</Row>;
  }

  if (nodeId === "compute_refund_amount") {
    return (
      <>
        <Row label="What was computed">{evidence.formula || "Waiting on inputs..."}</Row>
        <Row label="Why" emphasize>
          {evidence.isFailSafe
            ? "This is arithmetic run on a fail-safe default (policy couldn't be confidently read), not a genuine policy-driven number."
            : "Pure deterministic code — no LLM involved in the arithmetic itself, only in reading the policy terms that feed it."}
        </Row>
      </>
    );
  }

  if (nodeId === "attempt_refund") {
    const outcome = evidence.outcome;
    let whatCameBack = "Waiting on the merchant's /refund endpoint...";
    let why = "";
    if (outcome === "refunded") { whatCameBack = `Refund accepted — ${fmtInr(evidence.amountRecovered)} recovered.`; why = "Merchant's gateway confirmed the reversal."; }
    else if (outcome === "failed") { whatCameBack = `Refund rejected by the gateway: ${evidence.message || ""}`; why = "The merchant's own gateway declined the refund call."; }
    else if (outcome === "non_refundable") { whatCameBack = "No refund attempted."; why = evidence.message || "Policy explicitly prevents a refund here — money is recorded as a liability instead of being chased."; }
    else if (outcome === "dlq") { whatCameBack = "Sent to the dead-letter queue."; why = evidence.message || "This merchant's refund channel is currently unreliable (circuit breaker open) — retried later instead of failing silently."; }
    return (
      <>
        <Row label="What was called">POST {merchant || "merchant"}/refund</Row>
        <Row label="What came back">{whatCameBack}</Row>
        {why && <Row label="Why" emphasize>{why}</Row>}
      </>
    );
  }

  return <Row label="Status">{evidence.status || "pending"}</Row>;
}

function Row({ label, children, emphasize }) {
  return (
    <div className="mb-2 last:mb-0">
      <div className="text-[9px] uppercase tracking-wider text-[var(--text-muted)] font-semibold mb-0.5">{label}</div>
      <div className={`text-xs ${emphasize ? "text-[var(--text-primary)] font-medium" : "text-[var(--text-muted)]"} break-words`}>
        {children}
      </div>
    </div>
  );
}

export default function EvidencePopover({ kind, nodeId, title, evidence, onClose }) {
  if (!evidence) return null;
  return (
    <div
      className="absolute z-30 w-80 rounded-lg shadow-2xl p-3"
      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", top: 44, right: 12 }}
    >
      <div className="flex items-start justify-between mb-2">
        <div className="text-xs font-semibold text-[var(--text-primary)]">{title}</div>
        <button className="text-[var(--text-muted)] hover:text-white text-sm leading-none" onClick={onClose}>✕</button>
      </div>
      {kind === "payment" ? (
        <PaymentEvidence evidence={evidence} />
      ) : (
        <CompensationEvidence nodeId={nodeId} evidence={evidence} />
      )}
    </div>
  );
}