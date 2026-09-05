import { useState, useEffect } from "react";

// Live meter for the Agent Wallet — separate from the workflow Budget meter
// next to it in TopBar. Budget is this *workflow's* goal-scoped spending
// cap; Wallet is the agent's own standing financial authority (per-txn +
// daily limits), independent of any single workflow. Fetches once on load
// for the initial state, then `liveState` (passed from App.jsx's
// authorization_trace handler) keeps it current without polling.
export default function WalletPanel({ agentId = "primary_agent", liveState }) {
  const [wallet, setWallet] = useState(null);
  // Phase 7 — real RazorpayX available balance, fetched once alongside the
  // wallet's initial load. Unlike wallet state, nothing in the event stream
  // updates this mid-run today, so a one-time fetch (matching the original
  // wallet fetch pattern before liveState existed) is enough for now.
  const [rzpBalance, setRzpBalance] = useState(null);

  useEffect(() => {
    fetch(`http://localhost:8000/wallet/${agentId}`)
      .then((r) => r.json())
      .then(setWallet)
      .catch(() => {});
  }, [agentId]);

  useEffect(() => {
    fetch("http://localhost:8000/razorpay-balance")
      .then((r) => r.json())
      .then((data) => setRzpBalance(data.available_balance))
      .catch(() => {});
  }, []);

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
      {/* Phase 7 — real RazorpayX balance, distinct row so judges see this
          isn't the same number as the Agent Wallet authority above it. */}
      {rzpBalance !== null && (
        <div className="text-xs text-[var(--text-muted)] mt-1">
          RazorpayX balance <span className="font-mono text-[var(--text-primary,#c9d1d9)]">₹{rzpBalance.toLocaleString("en-IN")}</span>
        </div>
      )}
    </div>
  );
}
