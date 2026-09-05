import { useState } from "react";

const PRESETS = [
  // True happy path — budget comfortably covers both items (₹30k) so nothing
  // fails. Useful as the clean "before" baseline to contrast against the
  // failure-driven presets below.
  { label: "Happy path", goal: "Book a CRM license and hotel for our offsite", budget: 32000 },
  // Flexible hotel (₹20k) + catering (₹15k) = ₹35k vs ₹22k budget: hotel
  // succeeds, catering is rejected, compensation refunds the flexible hotel
  // under merchant_d's date-threshold policy — a partial/conditional refund
  // case, distinct from B's hard non-refundable and F's always-non-refundable.
  { label: "Flexible hotel", goal: "Book a flexible-cancellation hotel and catering for a conference", budget: 22000 },
  // Domain (₹12k, 10% cancellation penalty) + CRM (₹10k) = ₹22k vs ₹15k
  // budget: domain succeeds first, CRM is rejected, compensation refunds the
  // domain registration and must apply the exact 10% penalty math.
  { label: "Partial penalty", goal: "Register a domain with hosting, then get a CRM license for the team", budget: 15000 },
  { label: "Event launch", goal: "Book venue and catering for a product launch event", budget: 30000 },
  // Signage (₹8k) + domain hosting (₹12k) = ₹20k vs ₹12k budget: signage
  // succeeds first, domain is rejected, compensation must fetch_policy on
  // the flaky print vendor — the one preset that actually exercises the
  // intermittent /policy failure it's named for.
  { label: "Flaky vendor", goal: "Book signage printing for our product launch banner, then register the event's domain and hosting", budget: 12000 },
];

export default function TriggerPanel({ onLaunched }) {
  const [goal, setGoal] = useState("");
  const [budget, setBudget] = useState(25000);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState(null);
  const apiKey = import.meta.env.VITE_PROXY_API_KEY || "test-key-123";

  async function launch(goalOverride, budgetOverride) {
    const g = goalOverride !== undefined ? goalOverride : goal.trim() || null;
    const b = budgetOverride !== undefined ? budgetOverride : budget;
    setLaunching(true); setError(null);
    try {
      const resp = await fetch("http://localhost:8000/trigger_run", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({ goal: g, budget_limit: b }),
      });
      if (!resp.ok) throw new Error("Proxy returned " + resp.status);
      const data = await resp.json();
      onLaunched(data.workflow_id);
    } catch (err) { setError(err.message); }
    finally { setLaunching(false); }
  }

  return (
    <div className="w-[460px] bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg overflow-hidden">
      <div className="px-3 py-2 text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border)]">
        Start a new run
      </div>
      <div className="p-3 flex flex-col gap-2">

        {/* Pre-tested scenarios — ONE CLICK to launch */}
        <div>
          <div className="text-[10px] text-[var(--text-muted)] mb-1.5 font-medium">
            Pre-tested scenarios — click to launch instantly
          </div>
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map(p => (
              <button key={p.label} disabled={launching}
                className="text-[10px] bg-[var(--bg-primary)] border border-[var(--border)] rounded px-2 py-1 hover:border-[var(--blue)] hover:text-[var(--blue)] disabled:opacity-50 transition-colors"
                onClick={() => launch(p.goal, p.budget)}>
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <div className="border-t border-[var(--border)] pt-2">
          <div className="text-[10px] text-[var(--text-muted)] mb-1.5 font-medium">Custom goal</div>
          <textarea
            className="bg-[var(--bg-primary)] border border-[var(--border)] rounded px-2 py-1.5 text-xs w-full h-14 resize-none focus:outline-none focus:border-[var(--blue)] text-[var(--text)]"
            placeholder="Describe a custom goal in plain English..."
            value={goal} onChange={e => setGoal(e.target.value)} />
          <div className="flex items-center gap-2 mt-1.5">
            <label className="text-xs text-[var(--text-muted)] shrink-0">Budget Rs.</label>
            <input type="number" min={0} step={1000} value={budget}
              onChange={e => setBudget(Number(e.target.value) || 0)}
              className="bg-[var(--bg-primary)] border border-[var(--border)] rounded px-2 py-1 text-xs w-24 focus:outline-none focus:border-[var(--blue)] text-[var(--text)]" />
            <button disabled={launching || !goal.trim()} onClick={() => launch()}
              className="ml-auto bg-[var(--blue)] text-black font-semibold px-4 py-1.5 rounded text-xs hover:opacity-90 disabled:opacity-50">
              {launching ? "Launching..." : "Run"}
            </button>
          </div>
        </div>

        {error && <div className="text-[10px] text-[var(--red)]">{error}</div>}

        <button disabled={launching} onClick={() => launch(null, 35000)}
          className="text-[10px] text-[var(--text-muted)] hover:text-[var(--blue)] text-left disabled:opacity-50">
          ...or run the original fixed demo script (no LLM planner)
        </button>
      </div>
    </div>
  );
}