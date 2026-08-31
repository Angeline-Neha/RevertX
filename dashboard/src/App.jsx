import { useState, useEffect, useRef, useCallback } from "react";
import TopBar from "./components/TopBar.jsx";
import AgentTerminal from "./components/AgentTerminal.jsx";
import WorkflowGraph from "./components/WorkflowGraph.jsx";
import ReasoningStream from "./components/ReasoningStream.jsx";
import MetricsBar from "./components/MetricsBar.jsx";
import EndStatePanel from "./components/EndStatePanel.jsx";

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
  const [nodeStates, setNodeStates] = useState({});
  const [compensationNodes, setCompensationNodes] = useState([]);
  const [llmStream, setLlmStream] = useState("");
  const [mathLine, setMathLine] = useState(null);
  const [endState, setEndState] = useState(null);
  const [budget, setBudget] = useState({ used: 0, limit: 0 });
  const wsRef = useRef(null);

  const log = useCallback((msg) => {
    const ts = new Date().toLocaleTimeString("en-IN", { hour12: false });
    setTerminalLines((prev) => [...prev.slice(-200), `[${ts}] ${msg}`]);
  }, []);

  // Clears every piece of per-workflow state. Must run before connecting to
  // ANY workflow_id (including the very first one on mount) — otherwise
  // switching workflows (typing a new UUID, or reloading with a different
  // #hash) leaves the previous run's log lines, node states, and reasoning
  // stream sitting in place, and the new run's events just get appended on
  // top of them instead of starting clean.
  const resetWorkflowState = useCallback(() => {
    setTerminalLines([]);
    setNodeStates({});
    setCompensationNodes([]);
    setLlmStream("");
    setMathLine(null);
    setEndState(null);
    setBudget({ used: 0, limit: 0 });
  }, []);

  const connectWS = useCallback((wid) => {
    if (wsRef.current) wsRef.current.close();
    resetWorkflowState();
    const proxyApiKey = import.meta.env.VITE_PROXY_API_KEY || "test-key-123";
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

    switch (event_type) {
      case "workflow_init":
        setBudget({ used: 0, limit: data.budget_limit });
        log(`Workflow initialised — budget ₹${data.budget_limit.toLocaleString("en-IN")}`);
        break;

      case "payment_attempt":
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
        log(`✗ MANDATE EXCEEDED — ₹${data.amount} rejected. Budget: ₹${data.budget_used} / ₹${data.budget_limit}`);
        setNodeStates((prev) => ({ ...prev, merchant_c: "failed" }));
        break;

      case "compensation_started":
        log("🛡 Aegis compensation agent STARTED");
        setCompensationNodes([]);
        setLlmStream("");
        setMathLine(null);
        break;

      case "compensation_trace": {
        const { node, status } = data;
        log(`  [Aegis:${node}] ${status}`);
        setCompensationNodes((prev) => {
          const existing = prev.find((n) => n.id === node);
          const newState =
            status === "start" ? "in_progress" : status === "end" ? "success" : status === "error" ? "failed" : status === "skip" ? "skipped" : "pending";
          if (existing) return prev.map((n) => n.id === node ? { ...n, status: newState } : n);
          return [...prev, { id: node, label: node.replace(/_/g, " "), status: newState }];
        });
        break;
      }

      case "llm_stream_chunk":
        setLlmStream((prev) => prev + data.chunk);
        break;

      case "math_computation":
        setMathLine(data.formula);
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

      default:
        break;
    }
  }

  function connect() {
    const wid = inputId.trim();
    if (!wid) return;
    setWorkflowId(wid);
    window.location.hash = wid;
    connectWS(wid);
  }

  useEffect(() => {
    if (workflowId) connectWS(workflowId);
    return () => wsRef.current?.close();
  }, []);

  if (!workflowId) {
    return (
      <div className="flex flex-col items-center justify-center h-screen gap-4">
        <div className="text-3xl">🛡️ Aegis</div>
        <p className="text-[var(--text-muted)]">Enter a workflow ID to watch live events</p>
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
            onClick={connect}
          >
            Connect
          </button>
        </div>
        <p className="text-xs text-[var(--text-muted)]">
          Run <code className="bg-[var(--bg-secondary)] px-1 py-0.5 rounded">python primary_agent/procurement_agent.py</code> to start a demo workflow
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <TopBar workflowId={workflowId} budget={budget} connected={connected} />

      <div className="flex flex-1 overflow-hidden">
        {/* Left — Agent Terminal */}
        <AgentTerminal lines={terminalLines} />

        {/* Center — Workflow Graph */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <WorkflowGraph nodeStates={nodeStates} compensationNodes={compensationNodes} />
        </div>

        {/* Right — Reasoning Stream */}
        <ReasoningStream llmStream={llmStream} mathLine={mathLine} />
      </div>

      <MetricsBar metrics={BATCH_METRICS} />

      {endState && <EndStatePanel data={endState} onClose={() => setEndState(null)} />}
    </div>
  );
}
