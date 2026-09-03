import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  BaseEdge,
  getBezierPath,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { motion, AnimatePresence } from "framer-motion";

const STATUS_STYLES = {
  pending:     { bg: "#484f58", border: "#484f58", text: "#c9d1d9", icon: "○" },
  in_progress: { bg: "#1a2332", border: "#58a6ff", text: "#58a6ff", icon: "◌", pulse: true },
  success:     { bg: "#1a3320", border: "#3fb950", text: "#3fb950", icon: "✓" },
  failed:      { bg: "#2d1a1a", border: "#f85149", text: "#f85149", icon: "✗" },
  skipped:     { bg: "#2a2a2a", border: "#484f58", text: "#8b949e", icon: "—" },
};

function AegisNode({ data }) {
  const s = STATUS_STYLES[data.status] || STATUS_STYLES.pending;
  return (
    <motion.div
      className={`rounded-lg px-4 py-3 min-w-[140px] text-center relative ${s.pulse ? "node-pulse" : ""}`}
      title={data.status === "failed" && data.error ? data.error : undefined}
      style={{
        background: s.bg,
        border: `2px solid ${s.border}`,
        color: s.text,
        fontSize: 13,
        fontWeight: 600,
      }}
      initial={{ scale: 0.8, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      <Handle type="target" position={Position.Left} style={{ background: s.border }} />
      <div className="text-lg mb-1">{s.icon}</div>
      <div>{data.label}</div>
      {data.subLabel && <div className="text-xs opacity-70 mt-0.5">{data.subLabel}</div>}
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

export default function WorkflowGraph({ merchants, nodeStates, compensationNodes }) {
  const showCompensation = compensationNodes.length > 0;

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
          },
        });
      });
    }

    return result;
  }, [merchants, nodeStates, compensationNodes, showCompensation]);

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

  return (
    <div className="flex-1 relative" style={{ background: "#0d1117" }}>
      <div className="px-3 py-2 text-xs font-semibold text-[var(--text-muted)] border-b border-[var(--border)] uppercase tracking-wider bg-[var(--bg-secondary)]">
        Workflow Graph
      </div>
      <div style={{ height: "calc(100% - 32px)" }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#30363d" gap={20} />
          <Controls style={{ background: "#161b22", border: "1px solid #30363d" }} />
        </ReactFlow>
      </div>
    </div>
  );
}
