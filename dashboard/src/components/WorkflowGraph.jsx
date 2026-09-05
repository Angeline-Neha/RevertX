import { useMemo, useState, useEffect } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { motion } from "framer-motion";
import EvidencePopover from "./EvidencePopover.jsx";

const STATUS_STYLES = {
  pending:     { bg: "#1c2436", border: "#2a3349", text: "#7c8399", icon: "○" },
  in_progress: { bg: "#1c2436", border: "#dba955", text: "#dba955", icon: "◌", pulse: true },
  success:     { bg: "#1c2436", border: "#5fb489", text: "#5fb489", icon: "✓" },
  failed:      { bg: "#1c2436", border: "#e3654c", text: "#e3654c", icon: "✗" },
  skipped:     { bg: "#1c2436", border: "#2a3349", text: "#565e74", icon: "—" },
  // Phase 2/5/6 — a payout held at "pending" (non_terminal poll result, or
  // Preset 2's forced demo hold) is genuinely unconfirmed, not a failure —
  // same ⏳ language as the human_escalation_required banner. Distinct from
  // both `pending` above (hasn't run yet) and `failed` (confirmed dead).
  held:        { bg: "#1c2436", border: "#dba955", text: "#dba955", icon: "⏳" },
};

// Feature D — confidence/"evaluating" flicker. While a node is in_progress
// (i.e. between a compensation_trace "start" and its matching "end"/"error"/
// "skip"), cycle through a small set of tentative-sounding phrases instead
// of a static "loading" label, so the eventual verdict reads as the product
// of real evaluation rather than an instant, pre-baked answer. Purely a
// rendering choice — the underlying latency (a real HTTP call to a mock
// merchant's /policy endpoint) already exists today.
const FLICKER_PHRASES = [
  "Evaluating...",
  "Leaning refundable...",
  "Leaning non-refundable...",
  "Checking the fine print...",
];

function useFlicker(active) {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (!active) { setI(0); return; }
    const id = setInterval(() => setI((n) => (n + 1) % FLICKER_PHRASES.length), 650);
    return () => clearInterval(id);
  }, [active]);
  return FLICKER_PHRASES[i];
}

function AegisNode({ data }) {
  const s = STATUS_STYLES[data.status] || STATUS_STYLES.pending;
  const flickering = data.status === "in_progress";
  const flickerText = useFlicker(flickering);
  const clickable = data.hasEvidence && data.status !== "pending";

  return (
    <motion.div
      className={`rounded-lg px-4 py-3 min-w-[140px] text-center relative ${s.pulse ? "node-pulse" : ""} ${clickable ? "cursor-pointer" : ""}`}
      title={
        data.status === "failed" && data.error
          ? data.error
          : clickable
            ? "Click for details"
            : undefined
      }
      style={{
        background: s.bg,
        border: `2px solid ${s.border}`,
        color: s.text,
        fontSize: 13,
        fontWeight: 600,
        opacity: clickable ? 1 : data.status === "pending" ? 0.75 : 1,
      }}
      initial={{ scale: 0.8, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      whileHover={clickable ? { scale: 1.04 } : undefined}
      transition={{ duration: 0.3 }}
    >
      <Handle type="target" position={Position.Left} style={{ background: s.border }} />
      <div className="text-lg mb-1">{s.icon}</div>
      <div>{data.label}</div>
      {flickering ? (
        <div className="text-[10px] italic opacity-80 mt-0.5">{flickerText}</div>
      ) : (
        data.subLabel && <div className="text-xs opacity-70 mt-0.5">{data.subLabel}</div>
      )}
      {clickable && <div className="text-[9px] opacity-50 mt-1">click for details</div>}
      <Handle type="source" position={Position.Right} style={{ background: s.border }} />
    </motion.div>
  );
}

const nodeTypes = { aegis: AegisNode };

const NODE_X_SPACING = 220;
const NODE_Y = 120;

const COMP_NODE_LABELS = {
  load_workflow_log:       "Load Log",
  select_next_step_to_undo:"Select Step",
  fetch_policy:            "Fetch Policy",
  // graph.py's _trace() calls fire under the key "extract_policy" (see
  // extract_policy_terms_node in compensating_agent/graph.py), not
  // "extract_policy_terms" — this label previously never matched, so this
  // sub-node (the one the spec calls "the single moment worth the most
  // build time", §9.7 region 4) silently fell back to an auto-generated
  // "extract policy" label instead of the intended one below.
  extract_policy:          "Extract Terms (LLM)",
  compute_refund_amount:   "Compute Refund",
  attempt_refund:          "Attempt Refund",
  classify_and_route:      "Classify & Route",
  generate_udir_payload:   "UDIR Payload",
  generate_liability_report:"Liability Report",
  anomaly_check:           "Anomaly Check (LLM, advisory)",
};

export default function WorkflowGraph({
  merchants, nodeStates, compensationNodes,
  paymentEvidence = {}, compensationEvidence = {},
}) {
  const showCompensation = compensationNodes.length > 0;
  const [selectedNodeId, setSelectedNodeId] = useState(null);

  // Clicking a node that has nothing to show yet (still pending) is a
  // no-op; clicking the currently-open node again closes it.
  function handleNodeClick(_event, node) {
    const evidence = node.id.startsWith("comp_")
      ? compensationEvidence[node.id.slice(5)]
      : paymentEvidence[node.id];
    if (!evidence) return;
    setSelectedNodeId((prev) => (prev === node.id ? null : node.id));
  }

  const nodes = useMemo(() => {
    const result = merchants.map((m, i) => ({
      id: m.id,
      type: "aegis",
      position: { x: 100 + i * NODE_X_SPACING, y: NODE_Y },
      data: {
        // Fall back to a generic "Merchant X ₹Y" label when a merchant's
        // real payee name hasn't arrived yet (e.g. a payment_attempt event
        // was missed) rather than rendering a blank box — see Phase 5.1
        // acceptance check: any workflow_id's merchants should render
        // correctly, not just the ones the original demo used.
        label: m.label || `Merchant ${m.id.replace(/^merchant_/, "").toUpperCase()}`,
        subLabel: m.subLabel || (m.amount != null ? `₹${m.amount.toLocaleString("en-IN")}` : undefined),
        status: nodeStates[m.id] || "pending",
        hasEvidence: !!paymentEvidence[m.id],
      },
    }));

    if (showCompensation) {
      compensationNodes.forEach((cn, i) => {
        result.push({
          id: `comp_${cn.id}`,
          type: "aegis",
          position: { x: 50 + i * 170, y: 280 },
          data: {
            label: COMP_NODE_LABELS[cn.id] || cn.label,
            status: cn.status || "pending",
            // Shown as a hover tooltip on failed nodes (see AegisNode's
            // title attribute below) so the reason a node failed — e.g.
            // the actual Gemini API error — doesn't only live in the
            // scrollback log.
            error: cn.error,
            hasEvidence: !!compensationEvidence[cn.id],
          },
        });
      });
    }

    return result;
  }, [merchants, nodeStates, compensationNodes, showCompensation, paymentEvidence, compensationEvidence]);

  const edges = useMemo(() => {
    // Chain each merchant to the next in the order they were first seen
    // (the order payment_attempt events arrived), instead of a hardcoded
    // a→b→c chain — this is what actually lets a workflow with a
    // different merchant set/count render a sensible flow.
    const e = [];
    for (let i = 0; i < merchants.length - 1; i++) {
      e.push({
        id: `${merchants[i].id}-${merchants[i + 1].id}`,
        source: merchants[i].id,
        target: merchants[i + 1].id,
        animated: true,
        style: { stroke: "#58a6ff" },
      });
    }

    if (showCompensation && merchants.length > 0) {
      e.push({
        id: "last-comp",
        source: merchants[merchants.length - 1].id,
        target: `comp_${compensationNodes[0]?.id}`,
        animated: true,
        style: { stroke: "#f85149", strokeDasharray: "5 5" },
        label: "Compensation",
        labelStyle: { fill: "#f85149", fontSize: 11 },
      });
      compensationNodes.forEach((cn, i) => {
        if (i < compensationNodes.length - 1) {
          e.push({
            id: `comp_${i}_${i + 1}`,
            source: `comp_${cn.id}`,
            target: `comp_${compensationNodes[i + 1].id}`,
            animated: cn.status === "in_progress",
            style: { stroke: "#58a6ff" },
          });
        }
      });
    }

    return e;
  }, [merchants, compensationNodes, showCompensation]);

  // Resolve what the currently-open popover (if any) should show.
  let popover = null;
  if (selectedNodeId) {
    const isComp = selectedNodeId.startsWith("comp_");
    const nodeId = isComp ? selectedNodeId.slice(5) : selectedNodeId;
    const evidence = isComp ? compensationEvidence[nodeId] : paymentEvidence[nodeId];
    if (evidence) {
      const title = isComp
        ? COMP_NODE_LABELS[nodeId] || nodeId.replace(/_/g, " ")
        : (merchants.find((m) => m.id === nodeId)?.label || nodeId);
      popover = (
        <EvidencePopover
          kind={isComp ? "compensation" : "payment"}
          nodeId={nodeId}
          title={title}
          evidence={evidence}
          onClose={() => setSelectedNodeId(null)}
        />
      );
    }
  }

  return (
    <div className="flex-1 relative" style={{ background: "var(--bg-primary)" }}>
      <div className="panel-head" style={{ background: "var(--bg-secondary)" }}>
        <span className="dot" style={{ background: "var(--violet)" }} />
        <span className="title">Workflow</span>
        <span className="dim">{merchants.length + compensationNodes.length} nodes</span>
      </div>
      <div style={{ height: "calc(100% - 33px)" }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={handleNodeClick}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#2a3349" gap={20} />
          <Controls style={{ background: "#1c2436", border: "1px solid #2a3349" }} />
        </ReactFlow>
      </div>
      {popover}
    </div>
  );
}