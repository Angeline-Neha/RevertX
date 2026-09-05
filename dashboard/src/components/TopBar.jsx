import { useState, useEffect } from "react";
import BeforeAfterToggle from "./BeforeAfterToggle.jsx";
import PolicyDrawer from "./PolicyDrawer.jsx";

function Telemetry({ label, value, tone = "cyan" }) {
  return <div className={`telemetry telemetry-${tone}`}><span className="telemetry-led" /><span className="telemetry-label">{label}</span><strong>{value}</strong></div>;
}

export default function TopBar({ workflowId, budget, connected, onNewRun, liveWalletState }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [rzpBalance, setRzpBalance] = useState(null);
  useEffect(() => {
    fetch("http://localhost:8000/razorpay-balance").then((r) => r.json()).then((d) => setRzpBalance(d.available_balance)).catch(() => {});
  }, []);
  const pct = budget.limit > 0 ? (budget.used / budget.limit) * 100 : 0;
  const gaugeClass = pct > 85 ? "danger" : pct > 60 ? "warn" : "";
  const remaining = liveWalletState?.remaining_today;
  const dailyLimit = liveWalletState?.daily_limit || 75000;
  return (
    <>
      <header className="command-header">
        <div className="command-brand">
          <div className="brand-eyebrow"><span className="status-orb" /> AEGIS / AUTONOMOUS RECOVERY NETWORK</div>
          <div className="brand-row"><span className="wordmark">RevertX</span><span className={`live-pill ${connected ? "" : "off"}`}><span className="live-dot" />{connected ? "live link" : "offline"}</span></div>
          <div className="workflow-id">RUN // {(workflowId || "awaiting workflow").toUpperCase()}</div>
        </div>
        <div className="header-telemetry"><Telemetry label="PROXY" value={connected ? "ONLINE" : "WAIT"} tone={connected ? "green" : "warn"} /><Telemetry label="STREAM" value={connected ? "OPEN" : "CLOSED"} tone={connected ? "cyan" : "warn"} /><Telemetry label="AEGIS" value="ARMED" tone="purple" /></div>
        <div className="command-actions"><BeforeAfterToggle /><button className="ledger-btn" onClick={() => setDrawerOpen(true)}>policy <span className="button-key">P</span></button>{onNewRun && <button className="ledger-btn primary" onClick={onNewRun}>new run <span className="button-key">↗</span></button>}</div>
      </header>
      <section className="telemetry-deck">
        <div className="balance-module"><div className="module-kicker">RAZORPAYX / AVAILABLE BALANCE</div><div className="balance-value"><span>₹</span>{rzpBalance !== null ? rzpBalance.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—"}</div><div className="module-foot">LIVE ACCOUNT SNAPSHOT <i /></div></div>
        <div className="authority-module"><div className="module-kicker">AGENT AUTHORITY / DAILY WINDOW</div><div className="authority-row"><strong>₹{(remaining ?? dailyLimit).toLocaleString("en-IN")}</strong><span>remaining</span></div><div className="authority-track"><span style={{ width: `${Math.min(remaining != null ? (remaining / dailyLimit) * 100 : 100, 100)}%` }} /></div><div className="module-foot">PER TXN ≤ ₹{(liveWalletState?.per_txn_limit ?? 25000).toLocaleString("en-IN")}</div></div>
        <div className="mandate-module"><div className="module-kicker">WORKFLOW MANDATE / CURRENT RUN</div><div className="mandate-row"><strong>₹{budget.used.toLocaleString("en-IN")}</strong><span>/ ₹{budget.limit.toLocaleString("en-IN")}</span></div><div className="gauge-track"><div className={`gauge-fill ${gaugeClass}`} style={{ width: `${Math.min(pct, 100)}%` }} /></div><div className="module-foot">{pct >= 100 ? "LIMIT REACHED" : `${Math.round(pct)}% COMMITTED`}</div></div>
        <div className="wallet-card-wrap"><div className="wallet-card"><div className="wc-row-top"><div className="wc-chip" /><span className="card-signal">◉)))</span></div><div className="wc-number">•••• •••• •••• {(workflowId || "0000").slice(-4).padStart(4, "0")}</div><div className="wc-row-bottom"><div><div className="wc-label">AGENT</div><div className="wc-value">{liveWalletState?.agent_id || "primary_agent"}</div></div><div><div className="wc-label">LINK</div><div className={`wc-value ${connected ? "live" : "off"}`}>{connected ? "SECURE" : "OFFLINE"}</div></div></div><div className="wc-brand">RevertX / 01</div></div></div>
      </section>
      <PolicyDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} walletState={liveWalletState} />
    </>
  );
}
