import { useState } from "react";

// Click-to-expand policy summary — categories/denylist/allowlist/approval
// threshold. Unlike WalletPanel, policy has no live-changing state today
// (no event updates it mid-run), so this is a static fetch on open rather
// than something App.jsx needs to keep current off authorization_trace.
export default function PolicyPanel() {
  const [open, setOpen] = useState(false);
  const [policy, setPolicy] = useState(null);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !policy) {
      fetch("http://localhost:8000/policy")
        .then((r) => r.json())
        .then(setPolicy)
        .catch(() => {});
    }
  }

  return (
    <div className="relative">
      <button
        onClick={toggle}
        className="text-xs border border-[var(--border)] rounded px-3 py-1.5 hover:border-[var(--blue)] hover:text-[var(--blue)]"
      >
        Policy
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-72 z-10 bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg shadow-lg p-3 text-xs">
          {!policy ? (
            <div className="text-[var(--text-muted)]">Loading…</div>
          ) : (
            <div className="flex flex-col gap-2">
              <div>
                <div className="text-[var(--text-muted)] mb-1">Allowed categories</div>
                <div className="flex flex-wrap gap-1">
                  {policy.allowed_categories.map((c) => (
                    <span key={c} className="px-1.5 py-0.5 rounded bg-[var(--bg-primary)] font-mono">{c}</span>
                  ))}
                </div>
              </div>

              <div>
                <div className="text-[var(--text-muted)] mb-1">Recipient allowlist</div>
                <div className="font-mono">
                  {policy.recipient_allowlist === null ? "none (unrestricted)" : policy.recipient_allowlist.join(", ") || "—"}
                </div>
              </div>

              <div>
                <div className="text-[var(--text-muted)] mb-1">Recipient denylist</div>
                <div className="font-mono">
                  {policy.recipient_denylist.length ? policy.recipient_denylist.join(", ") : "—"}
                </div>
              </div>

              <div>
                <div className="text-[var(--text-muted)] mb-1">Human-approval threshold</div>
                <div className="font-mono">₹{policy.human_approval_threshold.toLocaleString("en-IN")}</div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
