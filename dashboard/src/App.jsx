import { useState, useEffect, useRef, useCallback } from "react";
import TopBar from "./components/TopBar.jsx";
import AgentTerminal from "./components/AgentTerminal.jsx";
import WorkflowGraph from "./components/WorkflowGraph.jsx";
import ReasoningStream from "./components/ReasoningStream.jsx";
import MetricsBar from "./components/MetricsBar.jsx";
import EndStatePanel from "./components/EndStatePanel.jsx";
import TriggerPanel from "./components/TriggerPanel.jsx";

// Batch eval metrics — from last run of test_harness/run_batch_eval.py.
// These numbers must match results.json committed to the repo exactly —
// spec §9.8 step 3 explicitly says paste the literal output numbers, not
// rounded or qualitative ones. Re-run run_batch_eval.py and update BOTH
// this object and results.json together before any demo/submission —
// there's no dynamic loader wiring this to results.json automatically.
const BATCH_METRICS = {
  matchRate: 1.0,
  mismatchDetectionRate: 1.0,
  falseDisputeRate: 0.0,
  totalRecords: 51,
};

// Derive workflow ID from URL hash, e.g. /#wf-uuid
function getWorkflowId() {
  const hash = window.location.hash.replace("#", "").trim();
  return hash || null;
}

export default function App() {
  const [workflowId, setWorkflowId] = useState(getWorkflowId);
  const [inputId, setInputId] = useState("");
  const [connected, setConnected] = useState(false);
  const [terminalLines, setTerminalLines] = useState([]);
  const [merchants, setMerchants] = useState([]);
  const [nodeStates, setNodeStates] = useState({});
  const [compensationNodes, setCompensationNodes] = useState([]);
  const [llmStream, setLlmStream] = useState("");
  const [mathLine, setMathLine] = useState(null);
  const [endState, setEndState] = useState(null);
  const [escalation, setEscalation] = useState(null);  // Phase 10.2 (also carries Phase 5's payout_unconfirmed kind)
  // Phase 5 — payouts pending_payout_worker.py gave up auto-resolving
  // (still non_terminal after PENDING_PAYOUT_MAX_CHECKS rechecks). Distinct
  // from `escalation`: this never auto-clears on a payout_resolved event
  // (there isn't one — it's stuck), only on manual dismiss, since it
  // genuinely needs a human to check the RazorpayX dashboard directly.
  const [stuckPayouts, setStuckPayouts] = useState([]);
  const [budget, setBudget] = useState({ used: 0, limit: 0 });
  // Populated from authorization_trace's check_wallet_authority step —
  // null until the first /pay call runs authorize(), so WalletPanel falls
  // back to its own one-time fetch until then.
  const [liveWalletState, setLiveWalletState] = useState(null);
  const [recentWorkflows, setRecentWorkflows] = useState([]);
  // Evidence trail (Feature E) — keyed by merchant_id for payments, by
  // compensation node name for the compensating agent's stages. Populated
  // from events the backend already publishes; nothing new is fetched here,
  // this just retains what handleEvent previously only ever logged and
  // discarded.
  const [paymentEvidence, setPaymentEvidence] = useState({});
  const [compensationEvidence, setCompensationEvidence] = useState({});
  // Several compensation_trace events (extract_policy, compute_refund_amount)
  // don't carry merchant_id themselves — the undo loop processes one
  // merchant at a time, so we track whichever merchant_id was last seen on
  // a trace that DID include one (fetch_policy start, attempt_refund) and
  // attribute merchant-less events to it. A ref (not state) because this
  // must be readable synchronously between rapid-fire events, not after a
  // render.
  const activeCompMerchantRef = useRef(null);
  const wsRef = useRef(null);

  const log = useCallback((msg) => {
    const ts = new Date().toLocaleTimeString("en-IN", { hour12: false });
    setTerminalLines((prev) => [...prev.slice(-200), `[${ts}] ${msg}`]);
  }, []);

  const proxyApiKey = import.meta.env.VITE_PROXY_API_KEY || "test-key-123";

  // Fetches the recent-workflows list for the picker panel shown on the
  // "no workflow connected" screen (Phase 5.2) — click-to-connect instead
  // of pasting a UUID copied from the demo client's terminal output.
  const fetchRecentWorkflows = useCallback(async () => {
    try {
      const resp = await fetch("http://localhost:8000/workflows?limit=20", {
        headers: { "X-API-Key": proxyApiKey },
      });
      if (!resp.ok) return;
      const data = await resp.json();
      setRecentWorkflows(data.workflows || []);
    } catch {
      // Proxy not reachable yet — the picker just stays empty; the manual
      // paste field below it still works as a fallback.
    }
  }, [proxyApiKey]);

  // Clears every piece of per-workflow state. Must run before connecting to
  // ANY workflow_id (including the very first one on mount) — otherwise
  // switching workflows (typing a new UUID, or reloading with a different
  // #hash) leaves the previous run's log lines, node states, and reasoning
  // stream sitting in place, and the new run's events just get appended on
  // top of them instead of starting clean.
  const resetWorkflowState = useCallback(() => {
    setTerminalLines([]);
    setMerchants([]);
    setNodeStates({});
    setCompensationNodes([]);
    setLlmStream("");
    setMathLine(null);
    setEndState(null);
    setEscalation(null);
    setStuckPayouts([]);
    setBudget({ used: 0, limit: 0 });
    setPaymentEvidence({});
    setCompensationEvidence({});
    activeCompMerchantRef.current = null;
  }, []);

  const connectWS = useCallback((wid) => {
    if (wsRef.current) wsRef.current.close();
    resetWorkflowState();
    const ws = new WebSocket(`ws://localhost:8000/ws/${wid}?token=${proxyApiKey}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      log(`✓ Connected to workflow ${wid}`);
    };
    ws.onclose = () => {
      setConnected(false);
      log("⚠ WebSocket disconnected");
    };
    ws.onerror = () => log("✗ WebSocket error");

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        handleEvent(msg);
      } catch {}
    };
  }, [log, resetWorkflowState]);

  function handleEvent(msg) {
    const { event_type, data } = msg;

    // Adds a merchant box to the graph the first time this workflow_id
    // mentions it, in the order merchants are first seen — replaces the
    // old hardcoded 3-box MERCHANT_NODES array (Phase 5.1). Fine for the
    // final label/amount to arrive slightly after the box first appears
    // (WorkflowGraph.jsx falls back to a generic label until then).
    function upsertMerchant(id, payee, amount) {
      if (!id) return;
      setMerchants((prev) =>
        prev.some((m) => m.id === id)
          ? prev
          : [...prev, { id, label: payee, amount }]
      );
    }

    switch (event_type) {
      case "workflow_init":
        setBudget({ used: 0, limit: data.budget_limit });
        log(`Workflow initialised — budget ₹${data.budget_limit.toLocaleString("en-IN")}`);
        break;

      case "payment_attempt":
        upsertMerchant(data.merchant_id, data.payee, data.amount);
        log(`→ Paying ${data.merchant_id} — ₹${data.amount.toLocaleString("en-IN")} (${data.item})`);
        setNodeStates((prev) => ({ ...prev, [data.merchant_id]: "in_progress" }));
        setPaymentEvidence((prev) => ({
          ...prev,
          [data.merchant_id]: {
            merchantId: data.merchant_id, payee: data.payee, amount: data.amount,
            item: data.item, requestStatus: "in_progress",
          },
        }));
        break;

      case "reconciliation_result":
        if (data.match) {
          setNodeStates((prev) => ({ ...prev, [data.merchant_id]: "success" }));
          log(`✓ ${data.merchant_id} reconciled — ₹${data.amount.toLocaleString("en-IN")} settled`);
        } else if (data.mismatch_type === "pending_unconfirmed") {
          // Held, not dead — same distinction the payout_unconfirmed /
          // human_escalation_required banner already makes. Was previously
          // falling into the "failed" bucket below, making every reconciliation
          // test / genuinely-uncertain payout look like a hard crash.
          setNodeStates((prev) => ({ ...prev, [data.merchant_id]: "held" }));
          log(`⏳ ${data.merchant_id} unconfirmed — holding, not a failure`);
        } else {
          setNodeStates((prev) => ({ ...prev, [data.merchant_id]: "failed" }));
          log(`✗ ${data.merchant_id} mismatch: ${data.mismatch_type}`);
        }
        setBudget((prev) => ({ ...prev, used: data.status === "settled" ? prev.used + (data.amount || 0) : prev.used }));
        setPaymentEvidence((prev) => ({
          ...prev,
          [data.merchant_id]: {
            ...(prev[data.merchant_id] || { merchantId: data.merchant_id }),
            match: data.match, mismatchType: data.mismatch_type,
            requestStatus: data.match ? "settled" : "mismatch",
          },
        }));
        break;

      case "mandate_exceeded":
        // A mandate-rejected payment never reaches the "3. Forward to
        // Merchant" step in mcp_proxy.py, so no payment_attempt event ever
        // fires for it — this is the only place this merchant's box gets
        // created. merchant_id/payee were previously missing from this
        // event entirely (the dashboard just hardcoded "merchant_c" here),
        // which silently broke for any workflow whose budget-busting step
        // wasn't literally merchant_c.
        upsertMerchant(data.merchant_id, data.payee, data.amount);
        log(`✗ MANDATE EXCEEDED — ₹${data.amount} rejected. Budget: ₹${data.budget_used} / ₹${data.budget_limit}`);
        setNodeStates((prev) => ({ ...prev, [data.merchant_id]: "failed" }));
        setPaymentEvidence((prev) => ({
          ...prev,
          [data.merchant_id]: {
            merchantId: data.merchant_id, payee: data.payee, amount: data.amount,
            item: data.item, requestStatus: "mandate_exceeded",
            budgetUsed: data.budget_used, budgetLimit: data.budget_limit,
          },
        }));
        break;

      case "compensation_started":
        log("🛡 Aegis compensation agent STARTED");
        setCompensationNodes([]);
        setLlmStream("");
        setMathLine(null);
        setCompensationEvidence({});
        activeCompMerchantRef.current = null;
        break;

      case "compensation_trace": {
        const { node, status, error } = data;
        if (data.merchant_id) activeCompMerchantRef.current = data.merchant_id;
        setCompensationEvidence((prev) => ({
          ...prev,
          [node]: {
            ...(prev[node] || {}),
            node, status, error,
            merchantId: data.merchant_id || activeCompMerchantRef.current || undefined,
            ...data,
          },
        }));
        // _trace() in graph.py attaches {"error": str(exc)} on status
        // "error" events (e.g. extract_policy_terms_node's except
        // clause), but that detail was previously dropped here — the log
        // only ever showed "[Aegis:extract_policy] error" with no
        // indication of *why* (bad/missing GEMINI_API_KEY, wrong model
        // name, the policy-extractor service being unreachable, etc.).
        // Surface it whenever it's present so a real failure is
        // diagnosable from the dashboard instead of only from the
        // service's own terminal output.
        const bgNote = node === "anomaly_check" ? " (background, advisory — not part of the completed result)" : "";
        log(`  [Aegis:${node}] ${status}${bgNote}${error ? ` — ${error}` : ""}`);
        setCompensationNodes((prev) => {
          const existing = prev.find((n) => n.id === node);
          const newState =
            status === "start" ? "in_progress" : status === "end" ? "success" : status === "error" ? "failed" : status === "skip" ? "skipped" : "pending";
          // This box is shared across every merchant the undo loop processes
          // for this pipeline stage (e.g. fetch_policy runs once per
          // merchant-being-undone, not once per whole workflow). A later
          // merchant's legitimate "skip" (no /policy endpoint for THAT
          // merchant) used to unconditionally overwrite an earlier
          // merchant's legitimate "success" here, making a stage that had
          // actually worked look skipped/broken. Rank terminal states so a
          // confirmed failure or success from any merchant stays visible
          // instead of being silently replaced by a less informative later
          // status for a different merchant.
          const RANK = { failed: 3, success: 2, skipped: 1, in_progress: 0, pending: -1 };
          if (existing) {
            const keepOld = RANK[existing.status] > RANK[newState];
            return prev.map((n) =>
              n.id === node
                ? keepOld
                  ? n // don't downgrade a more informative earlier status
                  : { ...n, status: newState, error: error || n.error }
                : n
            );
          }
          return [...prev, { id: node, label: node.replace(/_/g, " "), status: newState, error }];
        });
        break;
      }

      case "llm_stream_chunk":
        setLlmStream((prev) => prev + data.chunk);
        break;

      case "math_computation":
        setMathLine({ formula: data.formula, isFailSafe: !!data.is_fail_safe });
        log(`  [Math] ${data.formula}`);
        setCompensationEvidence((prev) => ({
          ...prev,
          compute_refund_amount: {
            ...(prev.compute_refund_amount || {}),
            formula: data.formula, isFailSafe: !!data.is_fail_safe,
            merchantId: activeCompMerchantRef.current,
          },
        }));
        break;

      case "refund_halted":
      case "refund_success":
      case "refund_failed":
        if (event_type === "refund_halted") log(`⚠ Refund halted for ${data.merchant_id}: ${data.message}`);
        if (event_type === "refund_success") log(`✓ Refund recovered for ${data.merchant_id}: ₹${(data.amount_recovered || 0).toLocaleString("en-IN")}`);
        if (event_type === "refund_failed") log(`✗ Refund failed for ${data.merchant_id}: ${data.message}`);
        setCompensationEvidence((prev) => ({
          ...prev,
          attempt_refund: {
            ...(prev.attempt_refund || {}),
            outcome: data.outcome, amountRecovered: data.amount_recovered,
            message: data.message, merchantId: data.merchant_id,
          },
        }));
        break;

      case "final_output":
        setEndState(data);
        log(`★ Final output: ${data.label}`);
        break;

      case "compensation_complete":
        log(
          `✓ Aegis compensation complete — udir: ${data.has_udir ? "yes" : "no"}, ` +
          `liability_report: ${data.has_liability_report ? "yes" : "no"}`
        );
        break;

      case "compensation_error":
        log(`✗ Aegis compensation ERROR: ${data.error}`);
        break;

      // No mandate was ever exceeded — every planned payment settled, so
      // there is nothing for Aegis to compensate. Previously there was no
      // signal for this at all; the dashboard just went quiet.
      case "workflow_complete":
        log(`✅ Workflow completed cleanly — ₹${(data.total_paid || 0).toLocaleString("en-IN")} across ${data.merchant_count || 0} merchant(s), no intervention needed`);
        setEndState({
          type: "clean_success",
          label: "Workflow completed — no intervention needed",
          payload: data,
        });
        break;

      // Phase 10.2 — policy extraction fell back to the fail-safe non-refundable
      // default (LLM failed, /policy endpoint unreachable, etc.). This is
      // visually distinct from a merchant whose policy genuinely says non-refundable.
      case "human_escalation_required":
        setEscalation(data);
        log(`⚠ HUMAN REVIEW REQUIRED — ${data.merchant_id}: ${data.reason}`);
        break;

      // Phase 5 — pending_payout_worker.py resolved a payout that was
      // stuck non_terminal. "settled" and "failed" are both legitimate,
      // known outcomes at this point (Phase 2's poll_payout classified
      // them for certain), so the escalation banner clears itself instead
      // of waiting for a human — that's the "quietly clear" half of
      // Phase 5's routing design.
      case "payout_resolved":
        log(
          data.resolution === "settled"
            ? `✅ PAYOUT RESOLVED — ${data.merchant_id} ${data.settlement_ref} confirmed processed, auto-cleared`
            : `↩ PAYOUT RESOLVED — ${data.merchant_id} ${data.settlement_ref} came back '${data.razorpay_status}', budget released`
        );
        setNodeStates((prev) => ({
          ...prev,
          [data.merchant_id]: data.resolution === "settled" ? "settled" : "failed",
        }));
        setEscalation((prev) =>
          prev && prev.kind === "payout_unconfirmed" && prev.pending_payout_id === data.pending_payout_id
            ? null
            : prev
        );
        break;

      // Phase 5 — auto-retry exhausted PENDING_PAYOUT_MAX_CHECKS rechecks
      // still non_terminal. This is the "hand off" half of Phase 5's
      // routing design: auto-resolution has done what it can, so this
      // gets its own persistent banner (not the dismissible escalation
      // one) that only a human checking RazorpayX directly can clear.
      case "payout_resolution_exhausted":
        log(`🛑 PAYOUT NEEDS MANUAL REVIEW — ${data.merchant_id} ${data.settlement_ref} still '${data.razorpay_status}' after ${data.checks_done} auto-rechecks`);
        setEscalation((prev) =>
          prev && prev.kind === "payout_unconfirmed" && prev.pending_payout_id === data.pending_payout_id
            ? null
            : prev
        );
        setStuckPayouts((prev) => [...prev, data]);
        break;

      // Phase 9.1 — Aegis was OFF when the budget limit fired. Money is
      // stranded with no compensation triggered — this is exactly what a
      // world without Aegis looks like.
      case "aegis_disabled_no_compensation":
        log(`🔴 AEGIS OFF — ${data.message}`);
        setEndState({
          type: "aegis_disabled",
          label: "Aegis OFF — Money Stranded, No Compensation",
          payload: { message: data.message, workflow_id: data.workflow_id },
        });
        break;

      // Phase 4 (Preset 3 flagship) — payout was CONFIRMED processed, but
      // the thing it paid for wasn't. Distinct from payout_unconfirmed
      // (which is genuinely ambiguous) — this is a known failure.
      case "downstream_fulfillment_failed":
        log(`✗ DOWNSTREAM FULFILLMENT FAILED — ${data.merchant_id} ₹${data.amount.toLocaleString("en-IN")}: ${data.reason}`);
        setNodeStates((prev) => ({ ...prev, [data.merchant_id]: "failed" }));
        break;

      // Phase 2 — payout polled to timeout still queued/processing.
      // Genuinely unknown, not a known failure, so this does NOT reuse
      // mandate_exceeded's shape or trigger compensation. Full routing to
      // the human_escalation_required banner + later resolution check is
      // Phase 5 — for now this just logs so the event isn't silently lost.
      case "payout_unconfirmed":
        log(`⏳ PAYOUT UNCONFIRMED — ${data.merchant_id} ₹${data.amount.toLocaleString("en-IN")} still '${data.razorpay_status}' after ${data.poll_attempts} poll(s)`);
        setNodeStates((prev) => ({ ...prev, [data.merchant_id]: "in_progress" }));
        break;

      // Agent Wallet / policy authorization ran BEFORE any payment attempt
      // (mcp_proxy.py, before reserve_budget). Distinct from
      // mandate_exceeded: no merchant/payment_attempt event ever fires
      // here, so there's no per-merchant box to update — this is a clean,
      // global dead-end. No compensation: nothing was paid, so there's
      // nothing to recover.
      case "authorization_trace": {
        const { step, status, detail } = data;
        log(`  [Auth:${step}] ${status}${detail ? ` — ${detail}` : ""}`);
        // check_wallet_authority's emit() carries the numbers WalletPanel
        // needs directly — no extra fetch, just read them off the step.
        if (step === "check_wallet_authority") {
          setLiveWalletState({
            agent_id: "primary_agent",
            per_txn_limit: data.per_txn_limit,
            daily_limit: data.daily_limit,
            spent_today: data.spent_today,
            remaining_today: data.remaining_today,
          });
        }
        if (step === "final_decision" && status === "block") {
          setEndState({
            type: "authorization_blocked",
            label: "Authorization BLOCKED — payout not attempted",
            payload: data,
          });
        }
        break;
      }

      default:
        break;
    }
  }

  function connect(wid) {
    const target = (wid || inputId).trim();
    if (!target) return;
    setWorkflowId(target);
    window.location.hash = target;
    connectWS(target);
  }

  useEffect(() => {
    if (workflowId) connectWS(workflowId);
    return () => wsRef.current?.close();
  }, []);

  // Populate the workflow picker whenever the "no workflow connected"
  // screen is showing — on first load, and again after a workflow
  // disconnects — so a demo operator always sees an up-to-date list
  // instead of a stale one from whenever the page first mounted.
  useEffect(() => {
    if (!workflowId) fetchRecentWorkflows();
  }, [workflowId, fetchRecentWorkflows]);

  if (!workflowId) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-4 py-8">
        <div className="text-3xl">🛡️ Aegis</div>

        <TriggerPanel onLaunched={(wid) => connect(wid)} />

        <div className="text-xs text-[var(--text-muted)]">— or connect to an existing run —</div>

        <div className="flex gap-2">
          <input
            className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-2 text-sm w-80 focus:outline-none focus:border-[var(--blue)]"
            placeholder="Paste workflow UUID here"
            value={inputId}
            onChange={(e) => setInputId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && connect()}
          />
          <button
            className="bg-[var(--blue)] text-black font-semibold px-4 py-2 rounded text-sm hover:opacity-90"
            onClick={() => connect()}
          >
            Connect
          </button>
        </div>

        {recentWorkflows.length > 0 && (
          <div className="w-[420px] bg-[var(--bg-secondary)] border border-[var(--border)] rounded overflow-hidden">
            <div className="px-3 py-2 text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border)] flex items-center justify-between">
              Recent workflows
              <button
                className="text-[var(--blue)] normal-case font-normal hover:underline"
                onClick={fetchRecentWorkflows}
              >
                refresh
              </button>
            </div>
            <div className="max-h-64 overflow-y-auto">
              {recentWorkflows.map((w) => (
                <button
                  key={w.workflow_id}
                  className="w-full text-left px-3 py-2 text-xs hover:bg-[var(--bg-primary)] border-b border-[var(--border)] last:border-b-0 flex items-center justify-between"
                  onClick={() => connect(w.workflow_id)}
                >
                  <span className="font-mono truncate">{w.workflow_id}</span>
                  <span className="text-[var(--text-muted)] ml-2 shrink-0">
                    ₹{Number(w.budget_used).toLocaleString("en-IN")} / ₹{Number(w.budget_limit).toLocaleString("en-IN")}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  function disconnect() {
    wsRef.current?.close();
    setWorkflowId(null);
    window.location.hash = "";
  }

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <TopBar
        workflowId={workflowId}
        budget={budget}
        connected={connected}
        onNewRun={disconnect}
        liveWalletState={liveWalletState}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* Left — Agent Terminal */}
        <AgentTerminal lines={terminalLines} />

        {/* Center — Workflow Graph */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <WorkflowGraph
            merchants={merchants}
            nodeStates={nodeStates}
            compensationNodes={compensationNodes}
            paymentEvidence={paymentEvidence}
            compensationEvidence={compensationEvidence}
          />
        </div>

        {/* Right — Reasoning Stream */}
        <ReasoningStream llmStream={llmStream} mathLine={mathLine} />
      </div>

      <MetricsBar metrics={BATCH_METRICS} />

      {/* Phase 10.2 — human escalation amber banner (policy fail-safe), and
          Phase 5's payout_unconfirmed kind reusing the same dismissible
          slot while pending_payout_worker.py's auto-retry is in progress. */}
      {escalation && (
        <div
          className="absolute bottom-16 left-1/2 -translate-x-1/2 z-40 rounded-lg px-5 py-3 text-sm shadow-2xl flex items-start gap-3 max-w-lg"
          style={{ background: "#2a1f00", border: "1px solid #d29922", color: "#d29922" }}
        >
          <span className="text-xl shrink-0">{escalation.kind === "payout_unconfirmed" ? "⏳" : "⚠️"}</span>
          <div>
            <div className="font-semibold mb-0.5">
              {escalation.kind === "payout_unconfirmed" ? "Payout Unconfirmed — Auto-Resolving" : "Flagged for Human Review"}
            </div>
            <div className="text-xs opacity-80">
              {escalation.kind === "payout_unconfirmed" ? (
                <>Real payout for <strong>{escalation.merchant_id}</strong> is still unconfirmed — retrying automatically in the background.</>
              ) : (
                <>Policy extraction failed for <strong>{escalation.merchant_id}</strong> — defaulted to non-refundable.
                A human should verify whether a refund applies.</>
              )}
            </div>
            <div className="text-xs opacity-60 mt-1 font-mono break-all">{escalation.reason}</div>
          </div>
          <button
            className="ml-auto shrink-0 opacity-60 hover:opacity-100"
            onClick={() => setEscalation(null)}
          >✕</button>
        </div>
      )}

      {/* Phase 5 — persistent red banner for payouts auto-retry gave up on.
          Unlike `escalation`, this never clears itself on a resolution
          event (there won't be one); only manual dismiss removes it. */}
      {stuckPayouts.length > 0 && (
        <div className="absolute bottom-16 right-4 z-40 flex flex-col gap-2 max-w-sm">
          {stuckPayouts.map((sp, i) => (
            <div
              key={`${sp.pending_payout_id}-${i}`}
              className="rounded-lg px-5 py-3 text-sm shadow-2xl flex items-start gap-3"
              style={{ background: "#2a0700", border: "1px solid #f85149", color: "#f85149" }}
            >
              <span className="text-xl shrink-0">🛑</span>
              <div>
                <div className="font-semibold mb-0.5">Payout Needs Manual Review</div>
                <div className="text-xs opacity-80">
                  <strong>{sp.merchant_id}</strong> ({sp.settlement_ref}) still '{sp.razorpay_status}' after {sp.checks_done} auto-rechecks.
                  Check the RazorpayX dashboard directly.
                </div>
              </div>
              <button
                className="ml-auto shrink-0 opacity-60 hover:opacity-100"
                onClick={() => setStuckPayouts((prev) => prev.filter((_, idx) => idx !== i))}
              >✕</button>
            </div>
          ))}
        </div>
      )}

      {endState && <EndStatePanel data={endState} onClose={() => setEndState(null)} />}
    </div>
  );
}