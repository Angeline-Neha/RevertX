import { useEffect, useRef } from "react";

export default function AgentTerminal({ lines }) {
  const ref = useRef(null);

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines]);

  return (
    <div className="w-72 shrink-0 flex flex-col border-r border-[var(--border)] bg-[var(--bg-panel)]">
      <div className="px-3 py-2 text-xs font-semibold text-[var(--text-muted)] border-b border-[var(--border)] uppercase tracking-wider">
        Primary Agent Log
      </div>
      <div
        ref={ref}
        className="flex-1 overflow-y-auto px-3 py-2 font-mono text-xs leading-5 text-[var(--text-primary)]"
      >
        {lines.length === 0 && (
          <div className="text-[var(--text-muted)] italic">Waiting for events...</div>
        )}
        {lines.map((line, i) => (
          <div
            key={i}
            className={
              line.includes("✗") || line.includes("EXCEEDED") || line.includes("CRASHED")
                ? "text-[var(--red)]"
                : line.includes("✓") || line.includes("settled")
                ? "text-[var(--green)]"
                : line.includes("⚠") || line.includes("halted")
                ? "text-[var(--yellow)]"
                : line.includes("🛡") || line.includes("★") || line.includes("[Aegis")
                ? "text-[var(--blue)]"
                : "text-[var(--text-primary)]"
            }
          >
            {line}
          </div>
        ))}
      </div>
    </div>
  );
}
