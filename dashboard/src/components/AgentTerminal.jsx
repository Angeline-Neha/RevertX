import { useEffect, useRef } from "react";

export default function AgentTerminal({ lines }) {
  const ref = useRef(null);

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines]);

  return (
    <div className="w-72 shrink-0 flex flex-col border-r border-[var(--border)] bg-[var(--bg-secondary)]">
      <div className="panel-head">
        <span className="dot" style={{ background: "var(--blue)" }} />
        <span className="title">Trace log</span>
        <span className="dim">{lines.length}</span>
      </div>
      <div ref={ref} className="flex-1 overflow-y-auto font-mono text-xs">
        {lines.length === 0 && (
          <div className="text-[var(--text-muted)] italic px-3.5 py-2">waiting for a run…</div>
        )}
        {lines.map((line, i) => {
          const cls =
            line.includes("✗") || line.includes("EXCEEDED") || line.includes("CRASHED")
              ? "entry is-fail"
              : line.includes("✓") || line.includes("settled")
              ? "entry is-pass"
              : line.includes("⚠") || line.includes("halted")
              ? "entry is-warn"
              : "entry";
          return (
            <div key={i} className={cls}>
              {line}
            </div>
          );
        })}
      </div>
    </div>
  );
}
