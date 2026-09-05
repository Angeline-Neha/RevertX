import { useState, useEffect } from "react";

// Live meter for the Agent Wallet — separate from the workflow Budget meter
// next to it in TopBar. Budget is this *workflow's* goal-scoped spending
// cap; Wallet is the agent's own standing financial authority (per-txn +
// daily limits), independent of any single workflow. Fetches once on load
// for the initial state, then `liveState` (passed from App.jsx's
// authorization_trace handler) keeps it current without polling.
export default function WalletPanel({ agentId = "primary_agent", liveState }) {
  const [wallet, setWallet] = useState(null);

  useEffect(() => {
    fetch(`http://localhost:8000/wallet/${agentId}`)
      .then((r) => r.json())
      .then(setWallet)
      .catch(() => {});
  }, [agentId]);

  const state = liveState || wallet;
  if (!state) return null;

  const pct = state.daily_limit > 0 ? (state.spent_today / state.daily_limit) * 100 : 0;
  const barColor = pct > 90 ? "#f85149" : pct > 70 ? "#d29922" : "#3fb950";

  return (
    <div className="text-right">
      <div className="text-xs text-[var(--text-muted)] mb-1">
        Wallet <span className="opacity-60">(≤₹{state.per_txn_limit.toLocaleString("en-IN")}/txn)</span>
      </div>
      <div className="flex items-center gap-2">
        <div className="w-32 h-2 bg-[var(--bg-primary)] rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${Math.min(pct, 100)}%`, backgroundColor: barColor }}
          />
        </div>
        <span className="text-sm font-mono font-semibold" style={{ color: barColor }}>
          ₹{state.spent_today.toLocaleString("en-IN")}
          <span className="text-[var(--text-muted)] font-normal"> / ₹{state.daily_limit.toLocaleString("en-IN")}</span>
        </span>
      </div>
    </div>
  );
}
