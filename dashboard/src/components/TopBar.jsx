import { useState, useEffect } from "react";
import BeforeAfterToggle from "./BeforeAfterToggle.jsx";
import PolicyDrawer from "./PolicyDrawer.jsx";

// Ledger redesign masthead + hero strip — wired to real data, no canned
// animation. Budget/connected/liveWalletState all come straight from
// App.jsx's WebSocket event handling, same as the previous TopBar.
export default function TopBar({ workflowId, budget, connected, onNewRun, liveWalletState }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [rzpBalance, setRzpBalance] = useState(null);

  useEffect(() => {
    fetch("http://localhost:8000/razorpay-balance")
      .then((r) => r.json())
      .then((d) => setRzpBalance(d.available_balance))
      .catch(() => {});
  }, []);

  const pct = budget.limit > 0 ? (budget.used / budget.limit) * 100 : 0;
  const gaugeClass = pct > 85 ? "danger" : pct > 60 ? "warn" : "";

  return (
    <>
      <div className="masthead" style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24, padding: "12px 16px", borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
            <span className="wordmark">RevertX</span>
            <span className={`live-pill ${connected ? "" : "off"}`}>
              <span className="live-dot" />{connected ? "live" : "disconnected"}
            </span>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            fault-isolated saga orchestrator · <span style={{ fontFamily: "var(--mono)" }}>{workflowId}</span>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <BeforeAfterToggle />
          <button className="ledger-btn" onClick={() => setDrawerOpen(true)}>policy</button>
          {onNewRun && (
            <button className="ledger-btn primary" onClick={onNewRun}>new run</button>
          )}
        </div>
      </div>

      <div className="hero-strip">
        <div className="hero-figure">
          <div className="label">RazorpayX wallet available</div>
          <div className="amount">
            <span className="rupee">₹</span>
            {rzpBalance !== null ? rzpBalance.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—"}
          </div>
          {liveWalletState && (
            <div className="sub">per-transaction limit ₹{liveWalletState.per_txn_limit?.toLocaleString("en-IN")}</div>
          )}
        </div>

        <div className="gauge-block">
          <div className="gauge-top">
            <span>budget used, this workflow</span>
            <strong>₹{budget.used.toLocaleString("en-IN")} / ₹{budget.limit.toLocaleString("en-IN")}</strong>
          </div>
          <div className="gauge-track">
            <div className={`gauge-fill ${gaugeClass}`} style={{ width: `${Math.min(pct, 100)}%` }} />
          </div>
        </div>

        <div>
          <div className="wallet-card">
            <div className="wc-row-top">
              <div className="wc-chip" />
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="1.6">
                <path d="M8.5 15.5a5 5 0 0 1 0-7" />
                <path d="M5.5 18.5a9 9 0 0 1 0-13" />
              </svg>
            </div>
            <div className="wc-number">•••• •••• •••• {(workflowId || "0000").slice(-4).padStart(4, "0")}</div>
            <div className="wc-row-bottom">
              <div>
                <div className="wc-label">agent</div>
                <div className="wc-value">{liveWalletState?.agent_id || "primary_agent"}</div>
              </div>
              <div>
                <div className="wc-label">status</div>
                <div className={`wc-value ${connected ? "live" : "off"}`}>
                  {connected && <span className="dot" />}{connected ? "live" : "offline"}
                </div>
              </div>
            </div>
            <div className="wc-brand">RevertX</div>
          </div>
        </div>
      </div>

      <PolicyDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} walletState={liveWalletState} />
    </>
  );
}
