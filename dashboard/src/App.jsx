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
  const [escalation, setEscalation] = useState(null);  // Phase 10.2
  const [budget, setBudget] = useState({ used: 0, limit: 0 });
  const [recentWorkflows, setRecentWorkflows] = useState([]);
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
    setBudget({ used: 0, limit: 0 });
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
        break;

      case "reconciliation_result":
        if (data.match) {
          setNodeStates((prev) => ({ ...prev, [data.merchant_id]: "success" }));
          log(`✓ ${data.merchant_id} reconciled — ₹${data.amount.toLocaleString("en-IN")} settled`);
        } else {
          setNodeStates((prev) => ({ ...prev, [data.merchant_id]: "failed" }));
          log(`✗ ${data.merchant_id} mismatch: ${data.mismatch_type}`);
        }
        setBudget((prev) => ({ ...prev, used: data.status === "settled" ? prev.used + (data.amount || 0) : prev.used }));
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
        break;

      case "compensation_started":
        log("🛡 Aegis compensation agent STARTED");
        setCompensationNodes([]);
        setLlmStream("");
        setMathLine(null);
        break;

      case "compensation_trace": {
        const { node, status, error } = data;
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
        break;

      case "refund_halted":
        log(`⚠ Refund halted for ${data.merchant_id}: ${data.message}`);
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

      // Phase 10.2 — policy extraction fell back to the fail-safe non-refundable
      // default (LLM failed, /policy endpoint unreachable, etc.). This is
      // visually distinct from a merchant whose policy genuinely says non-refundable.
      case "human_escalation_required":
        setEscalation(data);
        log(`⚠ HUMAN REVIEW REQUIRED — ${data.merchant_id}: ${data.reason}`);
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
      <TopBar workflowId={workflowId} budget={budget} connected={connected} onNewRun={disconnect} />

      <div className="flex flex-1 overflow-hidden">
        {/* Left — Agent Terminal */}
        <AgentTerminal lines={terminalLines} />

        {/* Center — Workflow Graph */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <WorkflowGraph merchants={merchants} nodeStates={nodeStates} compensationNodes={compensationNodes} />
        </div>

        {/* Right — Reasoning Stream */}
        <ReasoningStream llmStream={llmStream} mathLine={mathLine} />
      </div>

      <MetricsBar metrics={BATCH_METRICS} />

      {/* Phase 10.2 — human escalation amber banner (policy fail-safe triggered) */}
      {escalation && (
        <div
          className="absolute bottom-16 left-1/2 -translate-x-1/2 z-40 rounded-lg px-5 py-3 text-sm shadow-2xl flex items-start gap-3 max-w-lg"
          style={{ background: "#2a1f00", border: "1px solid #d29922", color: "#d29922" }}
        >
          <span className="text-xl shrink-0">⚠️</span>
          <div>
            <div className="font-semibold mb-0.5">Flagged for Human Review</div>
            <div className="text-xs opacity-80">
              Policy extraction failed for <strong>{escalation.merchant_id}</strong> — defaulted to non-refundable.
              A human should verify whether a refund applies.
            </div>
            <div className="text-xs opacity-60 mt-1 font-mono break-all">{escalation.reason}</div>
          </div>
          <button
            className="ml-auto shrink-0 opacity-60 hover:opacity-100"
            onClick={() => setEscalation(null)}
          >✕</button>
        </div>
      )}

      {endState && <EndStatePanel data={endState} onClose={() => setEndState(null)} />}
    </div>
  );
}
