import { useState, useEffect } from "react";

// Slide-out policy drawer (ledger redesign) — same real GET /policy call
// PolicyPanel.jsx used to show in a small popover; wallet limits come from
// liveWalletState/WalletPanel's one-time fetch, passed down from App.jsx so
// this drawer doesn't need its own duplicate wallet fetch.
export default function PolicyDrawer({ open, onClose, walletState }) {
  const [policy, setPolicy] = useState(null);

  useEffect(() => {
    if (open && !policy) {
      fetch("http://localhost:8000/policy")
        .then((r) => r.json())
        .then(setPolicy)
        .catch(() => {});
    }
  }, [open, policy]);

  return (
    <>
      <div className={`backdrop ${open ? "open" : ""}`} onClick={onClose} />
      <aside className={`drawer ${open ? "open" : ""}`} aria-hidden={!open}>
        <div className="drawer-head">
          <div>
            <h2>Wallet policy</h2>
            <p>what governs every payout before it's attempted — read-only</p>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="drawer-section">
          <h3>Spend limits</h3>
          <p className="desc">Hard ceilings enforced before any transfer leaves the wallet.</p>
          {walletState ? (
            <>
              <div className="kv-row"><span className="k">Per-transaction cap</span><span className="v">₹{walletState.per_txn_limit?.toLocaleString("en-IN")}</span></div>
              <div className="kv-row"><span className="k">Daily budget</span><span className="v">₹{walletState.daily_limit?.toLocaleString("en-IN")}</span></div>
              <div className="kv-row"><span className="k">Used today</span><span className="v">₹{walletState.spent_today?.toLocaleString("en-IN")}</span></div>
              <div className="kv-row"><span className="k">Remaining today</span><span className="v">₹{walletState.remaining_today?.toLocaleString("en-IN")}</span></div>
            </>
          ) : (
            <div className="kv-row"><span className="k">—</span><span className="v">no run connected yet</span></div>
          )}
        </div>

        <div className="drawer-section">
          <h3>Allowed categories</h3>
          {!policy ? (
            <div className="desc">Loading…</div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {(policy.allowed_categories || []).map((c) => (
                <span key={c} className="v" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: 3, padding: "2px 8px", fontSize: 11 }}>{c}</span>
              ))}
            </div>
          )}
        </div>

        {policy && (
          <>
            <div className="drawer-section">
              <h3>Recipient allowlist</h3>
              <p className="desc">{policy.recipient_allowlist === null ? "none (unrestricted)" : policy.recipient_allowlist.join(", ") || "—"}</p>
            </div>

            <div className="drawer-section">
              <h3>Recipient denylist</h3>
              <p className="desc">{policy.recipient_denylist.length ? policy.recipient_denylist.join(", ") : "—"}</p>
            </div>

            <div className="drawer-section" style={{ borderBottom: "none" }}>
              <h3>Human-approval threshold</h3>
              <div className="kv-row"><span className="k">Above this, a payout is blocked pre-flight</span><span className="v">₹{policy.human_approval_threshold?.toLocaleString("en-IN")}</span></div>
            </div>
          </>
        )}
      </aside>
    </>
  );
}
