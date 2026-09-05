import { motion, AnimatePresence } from "framer-motion";
import { JsonView, allExpanded, defaultStyles } from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";

export default function EndStatePanel({ data, onClose }) {
  if (!data) return null;
  const isUdir = data.type === "udir_payload";
  const isAuthBlocked = data.type === "authorization_blocked";

  // Three distinct pre-payment blocks share this one banner type — Wallet
  // (agent's own configured authority), Policy (rule compliance), and
  // Real Balance (does the money actually exist in RazorpayX right now).
  // block_source on the payload tells them apart; keep this label honest
  // instead of hardcoding "Agent Wallet / Policy" now that a third source
  // exists.
  const AUTH_BLOCK_LABELS = {
    wallet: "Agent Wallet — never attempted, not a fault",
    policy: "Policy — never attempted, not a fault",
    real_balance: "Real Balance — never attempted, not a fault",
  };
  const authBlockLabel = isAuthBlocked
    ? AUTH_BLOCK_LABELS[data.payload?.block_source] || "Agent Wallet / Policy — never attempted, not a fault"
    : null;

  return (
    <AnimatePresence>
      <motion.div
        key="end-state"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", stiffness: 300, damping: 30 }}
        className="absolute right-0 top-0 bottom-0 w-[480px] border-l border-[var(--border)] bg-[var(--bg-secondary)] flex flex-col z-50 shadow-2xl"
      >
        {/* Header */}
        <div
          className="px-4 py-3 flex items-start justify-between border-b"
          style={{ borderColor: isAuthBlocked ? "#d29922" : isUdir ? "#58a6ff" : "#3fb950" }}
        >
          <div>
            <div
              className="text-xs font-bold uppercase tracking-wider mb-1"
              style={{ color: isAuthBlocked ? "#d29922" : isUdir ? "#58a6ff" : "#3fb950" }}
            >
              {isAuthBlocked ? "Authorization Blocked" : isUdir ? "Network-Fault Detected" : "Agent-Fault Detected"}
            </div>
            <div className="text-sm font-semibold text-white">{data.label}</div>
          </div>
          <button
            className="text-[var(--text-muted)] hover:text-white text-lg mt-0.5"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        {/* Type badge */}
        <div className="px-4 py-2 flex gap-2">
          <span
            className="text-xs px-2 py-1 rounded font-semibold"
            style={{
              background: isAuthBlocked ? "rgba(210,153,34,0.15)" : isUdir ? "rgba(88,166,255,0.15)" : "rgba(63,185,80,0.15)",
              color: isAuthBlocked ? "#d29922" : isUdir ? "#58a6ff" : "#3fb950",
              border: `1px solid ${isAuthBlocked ? "#d29922" : isUdir ? "#58a6ff" : "#3fb950"}`,
            }}
          >
            {isAuthBlocked
              ? authBlockLabel
              : isUdir
              ? "UDIR Payload — ready for NPCI"
              : "Internal Liability Report — no dispute filed"}
          </span>
        </div>

        {/* JSON viewer */}
        <div className="flex-1 overflow-auto px-4 pb-4">
          <div
            className="rounded p-3 text-xs font-mono"
            style={{ background: "#0a0e14", border: "1px solid var(--border)" }}
          >
            <JsonView
              data={data.payload}
              shouldExpandNode={allExpanded}
              style={{
                ...defaultStyles,
                container: "font-mono text-xs",
                basicChildStyle: "ml-4",
                label: "text-[#79c0ff]",
                nullValue: "text-[#f85149]",
                undefinedValue: "text-[#f85149]",
                numberValue: "text-[#79c0ff]",
                stringValue: "text-[#a5d6ff]",
                booleanValue: "text-[#d2a8ff]",
                punctuation: "text-[#8b949e]",
              }}
            />
          </div>
        </div>

        {/* Safety note */}
        {isAuthBlocked && (
          <div
            className="mx-4 mb-4 rounded px-3 py-2 text-xs"
            style={{ background: "rgba(210,153,34,0.1)", border: "1px solid #d29922", color: "#d29922" }}
          >
            ⛔ No payout attempted — nothing was paid, so there is nothing for Aegis to compensate.
          </div>
        )}
        {!isAuthBlocked && !isUdir && (
          <div
            className="mx-4 mb-4 rounded px-3 py-2 text-xs"
            style={{ background: "rgba(63,185,80,0.1)", border: "1px solid #3fb950", color: "#3fb950" }}
          >
            ✓ No UDIR complaint filed — fault is agent logic, not a merchant or network failure.
          </div>
        )}
        {!isAuthBlocked && isUdir && (
          <div
            className="mx-4 mb-4 rounded px-3 py-2 text-xs"
            style={{ background: "rgba(88,166,255,0.1)", border: "1px solid #58a6ff", color: "#58a6ff" }}
          >
            ↗ UDIR-shaped payload ready — network/infrastructure failure confirmed by raw gateway code.
          </div>
        )}
      </motion.div>
    </AnimatePresence>
  );
}
