export default function MetricsBar({ metrics }) {
  const { matchRate, mismatchDetectionRate, falseDisputeRate } = metrics;
  const fmt = (v) => `${(v * 100).toFixed(1)}%`;

  return (
    <div
      className="flex items-center justify-around px-6 py-2 border-t border-[var(--border)] bg-[var(--bg-secondary)] shrink-0"
      title="From last run of test_harness/run_batch_eval.py (50 records)"
    >
      <Metric label="Match Rate" value={fmt(matchRate)} color="#3fb950" />
      <div className="w-px h-8 bg-[var(--border)]" />
      <Metric label="Mismatch Detection" value={fmt(mismatchDetectionRate)} color="#58a6ff" />
      <div className="w-px h-8 bg-[var(--border)]" />
      <Metric
        label="False-Dispute Rate ★"
        value={fmt(falseDisputeRate)}
        color={falseDisputeRate === 0 ? "#3fb950" : "#f85149"}
        subtitle="must be 0.0%"
      />
      <div className="w-px h-8 bg-[var(--border)]" />
      <div className="text-xs text-[var(--text-muted)] text-center">
        <div className="font-semibold text-white">50 records</div>
        <div>batch eval</div>
      </div>
    </div>
  );
}

function Metric({ label, value, color, subtitle }) {
  return (
    <div className="text-center">
      <div className="text-2xl font-bold font-mono" style={{ color }}>
        {value}
      </div>
      <div className="text-xs text-[var(--text-muted)]">{label}</div>
      {subtitle && <div className="text-xs" style={{ color }}>{subtitle}</div>}
    </div>
  );
}
