import { useRef, useEffect } from "react";

export default function ReasoningStream({ llmStream, mathLine }) {
  const llmRef = useRef(null);

  useEffect(() => {
    if (llmRef.current) llmRef.current.scrollTop = llmRef.current.scrollHeight;
  }, [llmStream]);

  return (
    <div className="w-80 shrink-0 flex flex-col border-l border-[var(--border)] bg-[var(--bg-secondary)]">
      {/* LLM Stream section */}
      <div className="panel-head">
        <span className="dot" style={{ background: "var(--text-muted)" }} />
        <span className="title">Reasoning</span>
        <span className="dim">token stream</span>
      </div>

      <div ref={llmRef} className="flex-1 overflow-y-auto px-3.5 py-3" style={{ minHeight: 0 }}>
        {!llmStream && (
          <div className="stream-text"><span className="placeholder">waiting for a run…</span></div>
        )}
        {llmStream && (
          <div className="stream-text" style={{ whiteSpace: "pre-wrap" }}>{llmStream}</div>
        )}
      </div>

      {/* Deterministic math section */}
      <div className="math-box" style={{ borderTop: "1px solid var(--border)", padding: 0 }}>
        <div className="panel-head" style={{ borderBottom: "1px solid var(--border)" }}>
          <span className="title">Deterministic math</span>
          <span className="dim">no LLM here</span>
        </div>
        <div className="px-3.5 py-3 font-mono text-xs">
          {!mathLine && (
            <div className="text-[var(--text-muted)] italic">Waiting for refund computation...</div>
          )}
          {mathLine && (
            <div
              className="rounded px-3 py-2 text-sm font-semibold"
              style={
                mathLine.isFailSafe
                  ? {
                      background: "#2a1f0a",
                      border: "1px solid var(--blue)",
                      color: "var(--blue)",
                    }
                  : {
                      background: "#132119",
                      border: "1px solid var(--green)",
                      color: "var(--green)",
                    }
              }
            >
              {mathLine.isFailSafe && (
                <div className="text-xs font-bold uppercase tracking-wide mb-1">
                  ⚠ Fail-safe default — not a genuine policy read
                </div>
              )}
              {mathLine.formula}
            </div>
          )}
        </div>
        <div className="px-3 pb-3 text-xs text-[var(--text-muted)] italic">
          LLM extracts penalty %. Code does the arithmetic.
        </div>
      </div>
    </div>
  );
}
