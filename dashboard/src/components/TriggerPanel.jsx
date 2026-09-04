import { useState } from "react";

// Phase 8.2 — five pre-tested autofill buttons, each mapped to a specific
// case exercising a different part of Phases 6-7. Wording is fixed
// deliberately (per the build plan: "do NOT invent new wording live —
// these five are chosen to be safe under demo pressure") — editing the
// text after clicking a preset is fine, but the presets themselves should
// stay as-is rather than being rephrased.
const PRESETS = [
  {
    label: "Happy path",
    goal: "Book a CRM license and a hotel for our offsite, budget ₹35,000",
    budget: 35000,
    note: "Overspend → full refund + protected non-refundable merchant (the original scripted demo, run via the planner)",
  },
  {
    label: "Flexible hotel",
    goal: "Book flights and 2 hotel nights for a conference, budget ₹40,000, use a flexible-cancellation hotel",
    budget: 40000,
    note: "Exercises the partial-penalty merchant (merchant_d, 30%/5 days)",
  },
  {
    label: "Strict non-refundable",
    goal: "Book a certification exam voucher and domain hosting, budget ₹15,000",
    budget: 15000,
    note: "Exercises the strict non-refundable merchant, proves correct merchant protection",
  },
  {
    label: "Event launch",
    goal: "Book venue and catering for a product launch, budget ₹50,000",
    budget: 50000,
    note: "Exercises the unrelated vendor set (merchant_f venue + merchant_g catering)",
  },
  {
    label: "Flaky vendor",
    goal: "Book signage printing for our product launch banner, budget ₹10,000",
    budget: 10000,
    note: "Routes through the flaky merchant (merchant_e) — proves the fetch_policy fail-safe path, not just a clean run",
  },
];

export default function TriggerPanel({ onLaunched }) {
  const [goal, setGoal] = useState("");
  const [budget, setBudget] = useState(35000);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState(null);

  const proxyApiKey = import.meta.env.VITE_PROXY_API_KEY || "test-key-123";

  function applyPreset(preset) {
    setGoal(preset.goal);
    setBudget(preset.budget);
    setError(null);
  }

  async function runFixedDemo() {
    // Button 1's underlying case ("existing case") is the original fixed
    // 3-step script, not the planner — POSTing with goal omitted routes
    // /trigger_run to run_procurement() unchanged, matching Phase 8.2's
    // "existing case" description exactly rather than approximating it
    // through the LLM planner like the other four presets.
    await launch(null, 35000);
  }

  async function launch(goalOverride, budgetOverride) {
    const effectiveGoal = goalOverride !== undefined ? goalOverride : goal.trim() || null;
    const effectiveBudget = budgetOverride !== undefined ? budgetOverride : budget;

    setLaunching(true);
    setError(null);
    try {
      const resp = await fetch("http://localhost:8000/trigger_run", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": proxyApiKey },
        body: JSON.stringify({ goal: effectiveGoal, budget_limit: effectiveBudget }),
      });
      if (!resp.ok) throw new Error(`Proxy returned ${resp.status}`);
      const data = await resp.json();
      onLaunched(data.workflow_id);
    } catch (err) {
      setError(`Couldn't launch — is the proxy running? (${err.message})`);
    } finally {
      setLaunching(false);
    }
  }

  return (
    <div className="w-[460px] bg-[var(--bg-secondary)] border border-[var(--border)] rounded overflow-hidden">
      <div className="px-3 py-2 text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border)]">
        Start a new run
      </div>

      <div className="p-3 flex flex-col gap-2">
        <textarea
          className="bg-[var(--bg-primary)] border border-[var(--border)] rounded px-2 py-1.5 text-xs w-full h-16 resize-none focus:outline-none focus:border-[var(--blue)]"
          placeholder="Describe the goal in plain English, or pick a preset below…"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
        />

        <div className="flex items-center gap-2">
          <label className="text-xs text-[var(--text-muted)] shrink-0">Budget ₹</label>
          <input
            type="number"
            className="bg-[var(--bg-primary)] border border-[var(--border)] rounded px-2 py-1 text-xs w-28 focus:outline-none focus:border-[var(--blue)]"
            value={budget}
            min={0}
            step={1000}
            onChange={(e) => setBudget(Number(e.target.value) || 0)}
          />
          <button
            className="ml-auto bg-[var(--blue)] text-black font-semibold px-4 py-1.5 rounded text-xs hover:opacity-90 disabled:opacity-50"
            disabled={launching || !goal.trim()}
            onClick={() => launch()}
          >
            {launching ? "Launching…" : "Run"}
          </button>
        </div>

        {error && <div className="text-xs text-[var(--red)]">{error}</div>}

        <div className="border-t border-[var(--border)] pt-2 mt-1">
          <div className="text-xs text-[var(--text-muted)] mb-1.5">Pre-tested scenarios</div>
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                title={p.note}
                className="text-[10px] bg-[var(--bg-primary)] border border-[var(--border)] rounded px-2 py-1 hover:border-[var(--blue)] hover:text-[var(--blue)]"
                onClick={() => applyPreset(p)}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <button
          className="text-[10px] text-[var(--text-muted)] hover:text-[var(--blue)] text-left mt-1 disabled:opacity-50"
          disabled={launching}
          onClick={runFixedDemo}
        >
          …or just run the original fixed demo script (no LLM planner)
        </button>
      </div>
    </div>
  );
}
