import { useRef, useEffect } from "react";

export default function ReasoningStream({ llmStream, mathLine }) {
  const llmRef = useRef(null);

  useEffect(() => {
    if (llmRef.current) llmRef.current.scrollTop = llmRef.current.scrollHeight;
  }, [llmStream]);

  return (
    <div className="w-80 shrink-0 flex flex-col border-l border-[var(--border)] bg-[var(--bg-secondary)]">
      {/* LLM Stream section */}
      <div className="px-3 py-2 text-xs font-semibold text-[var(--text-muted)] border-b border-[var(--border)] uppercase tracking-wider flex items-center gap-2">
        <span>LLM Reasoning</span>
        <span className="text-[#3fb950] text-xs normal-case font-normal">← real token stream</span>
      </div>

      <div
        ref={llmRef}
        className="flex-1 overflow-y-auto px-3 py-2 font-mono text-xs leading-5 text-[var(--text-primary)]"
        style={{ minHeight: 0 }}
      >
        {!llmStream && (
          <div className="text-[var(--text-muted)] italic">
            Waiting for policy extraction call...
          </div>
        )}
        {llmStream && (
          <div className="whitespace-pre-wrap text-[var(--blue)]">{llmStream}</div>
        )}
      </div>

      {/* Deterministic math section */}
      <div className="border-t border-[var(--border)]">
        <div className="px-3 py-2 text-xs font-semibold text-[var(--text-muted)] border-b border-[var(--border)] uppercase tracking-wider flex items-center gap-2">
          <span>Deterministic Math</span>
          <span className="text-[#d29922] text-xs normal-case font-normal">← no LLM here</span>
        </div>
        <div className="px-3 py-3 font-mono text-xs">
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
                      border: "1px solid #d29922",
                      color: "#d29922",
                    }
                  : {
                      background: "#1a2a1a",
                      border: "1px solid var(--green)",
                      color: "#3fb950",
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
