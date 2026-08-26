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

const MERCHANT_NODES = [
  { id: "merchant_a", label: "CRM Corp", subLabel: "₹10,000", x: 100, y: 120 },
  { id: "merchant_b", label: "Grand Hotel", subLabel: "₹20,000", x: 320, y: 120 },
  { id: "merchant_c", label: "Domain / Flights", subLabel: "₹12,000", x: 540, y: 120 },
];

const COMP_NODE_LABELS = {
  load_workflow_log:       "Load Log",
  select_next_step_to_undo:"Select Step",
  fetch_policy:            "Fetch Policy",
  extract_policy_terms:    "Extract Terms (LLM)",
  compute_refund_amount:   "Compute Refund",
  attempt_refund:          "Attempt Refund",
  classify_and_route:      "Classify & Route",
  generate_udir_payload:   "UDIR Payload",
  generate_liability_report:"Liability Report",
};

export default function WorkflowGraph({ nodeStates, compensationNodes }) {
  const showCompensation = compensationNodes.length > 0;

  const nodes = useMemo(() => {
    const result = MERCHANT_NODES.map((m) => ({
      id: m.id,
      type: "aegis",
      position: { x: m.x, y: m.y },
      data: {
        label: m.label,
        subLabel: m.subLabel,
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
          },
        });
      });
    }

    return result;
  }, [nodeStates, compensationNodes, showCompensation]);

  const edges = useMemo(() => {
    const e = [
      { id: "a-b", source: "merchant_a", target: "merchant_b", animated: true, style: { stroke: "#58a6ff" } },
      { id: "b-c", source: "merchant_b", target: "merchant_c", animated: true, style: { stroke: "#58a6ff" } },
    ];

    if (showCompensation) {
      e.push({
        id: "c-comp",
        source: "merchant_c",
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
  }, [nodes, compensationNodes, showCompensation]);

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
