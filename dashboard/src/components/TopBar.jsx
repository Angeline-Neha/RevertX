import BeforeAfterToggle from "./BeforeAfterToggle.jsx";
import WalletPanel from "./WalletPanel.jsx";
import PolicyPanel from "./PolicyPanel.jsx";

export default function TopBar({ workflowId, budget, connected, onNewRun, liveWalletState }) {
  const pct = budget.limit > 0 ? (budget.used / budget.limit) * 100 : 0;
  const barColor = pct > 90 ? "#f85149" : pct > 70 ? "#d29922" : "#3fb950";

  return (
    <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--border)] bg-[var(--bg-secondary)] shrink-0">
      <div className="flex items-center gap-3">
        <span className="text-xl">🛡️</span>
        <div>
          <div className="text-sm font-semibold text-white">Aegis — Fault-Isolated Saga Orchestrator</div>
          <div className="text-xs text-[var(--text-muted)] font-mono truncate max-w-xs">{workflowId}</div>
        </div>
      </div>

      <div className="flex items-center gap-6">
        {/* Phase 9.1 — Before/After Aegis toggle */}
        <BeforeAfterToggle />

        {/* Budget meter */}
        <div className="text-right">
          <div className="text-xs text-[var(--text-muted)] mb-1">Budget</div>
          <div className="flex items-center gap-2">
            <div className="w-32 h-2 bg-[var(--bg-primary)] rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{ width: `${Math.min(pct, 100)}%`, backgroundColor: barColor }}
              />
            </div>
            <span className="text-sm font-mono font-semibold" style={{ color: barColor }}>
              ₹{budget.used.toLocaleString("en-IN")}
              <span className="text-[var(--text-muted)] font-normal"> / ₹{budget.limit.toLocaleString("en-IN")}</span>
            </span>
          </div>
        </div>

        {/* Agent Wallet meter — separate from Budget above: this is the
            agent's own standing financial authority, not this workflow's
            spending cap */}
        <WalletPanel liveState={liveWalletState} />

        {/* Policy rules — click-to-expand, static */}
        <PolicyPanel />

        {/* Connection status */}
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full ${connected ? "bg-[var(--green)]" : "bg-[var(--red)]"}`} />
          <span className="text-xs text-[var(--text-muted)]">{connected ? "Live" : "Disconnected"}</span>
        </div>

        {onNewRun && (
          <button
            className="text-xs border border-[var(--border)] rounded px-3 py-1.5 hover:border-[var(--blue)] hover:text-[var(--blue)]"
            onClick={onNewRun}
          >
            New run
          </button>
        )}
      </div>
    </div>
  );
}
