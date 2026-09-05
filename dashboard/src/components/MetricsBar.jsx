import { useState, useEffect } from "react";

export default function MetricsBar({ metrics }) {
  const { matchRate, mismatchDetectionRate, falseDisputeRate, totalRecords } = metrics;
  const fmt = (v) => `${(v * 100).toFixed(1)}%`;

  // Phase 9.2 — live running recovery counter, polled from /session_metrics.
  // Resets only on proxy restart (server-side), not on page reload.
  const [recovery, setRecovery] = useState({ total_recovered_inr: 0, disputes_resolved: 0 });
  useEffect(() => {
    function poll() {
      fetch("http://localhost:8000/session_metrics")
        .then((r) => r.json())
        .then((d) => setRecovery(d))
        .catch(() => {});
    }
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="receipt" title="From last run of test_harness/run_batch_eval.py (50 records)">
      <div className="receipt-cell">
        <div className="label">auto-recovered, this session</div>
        <div className="value gold">
          ₹{recovery.total_recovered_inr.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
        </div>
        <div className="label" style={{ marginTop: 4, marginBottom: 0 }}>
          {recovery.disputes_resolved} dispute{recovery.disputes_resolved !== 1 ? "s" : ""} resolved
        </div>
      </div>
      <div className="receipt-cell">
        <div className="label">match rate</div>
        <div className="value green">{fmt(matchRate)}</div>
      </div>
      <div className="receipt-cell">
        <div className="label">mismatch detection</div>
        <div className="value gold">{fmt(mismatchDetectionRate)}</div>
      </div>
      <div className="receipt-cell">
        <div className="label">false-dispute rate — must be 0.0%</div>
        <div className={`value ${falseDisputeRate === 0 ? "green" : "coral"}`}>{fmt(falseDisputeRate)}</div>
      </div>
      <div className="receipt-cell">
        <div className="label">records, batch eval</div>
        <div className="value">{totalRecords}</div>
      </div>
    </div>
  );
}
