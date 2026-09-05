import { useEffect, useMemo, useRef, useState } from "react";

const TOTAL_MS = 60_000;
const TASK_LINES = ["Book the venue.", "Handle the payment."];

function PixelRobot({ active }) {
  return (
    <div className={`pixel-robot ${active ? "is-active" : ""}`} aria-label="Aegis robot sprite">
      <div className="robot-antenna" />
      <div className="robot-head"><span /><span /></div>
      <div className="robot-neck" />
      <div className="robot-body">
        <div className="robot-chest"><i /><b>AEGIS</b></div>
        <div className="robot-arm robot-arm-left" />
        <div className="robot-arm robot-arm-right" />
      </div>
      <div className="robot-leg robot-leg-left" /><div className="robot-leg robot-leg-right" />
    </div>
  );
}

function PixelTypewriter({ progress }) {
  const full = TASK_LINES.join("\n");
  const count = Math.min(full.length, Math.floor(Math.max(0, progress) * (full.length + 1)));
  const visible = full.slice(0, count);
  return (
    <div className="typewriter-paper">
      {visible.split("\n").map((line, index) => <div key={index}>{line}<span className="caret">▌</span></div>)}
    </div>
  );
}

function CrtScreen({ errorCount, phase }) {
  return (
    <div className={`crt ${phase >= 3 ? "crt-failing" : ""}`}>
      <div className="crt-bezel">
        <div className="crt-glass">
          <div className="crt-ui">
            <div className="ui-title">AEGIS PAYMENT TERMINAL</div>
            <div className="ui-field"><span>VENUE</span><strong>RIVERSIDE HALL</strong></div>
            <div className="ui-field"><span>AMOUNT</span><strong>₹ 84,000</strong></div>
            <div className="ui-button">AUTHORIZE PAYMENT</div>
            <div className="pixel-cursor" />
          </div>
          <div className="scanlines" />
          <div className="error-stack">
            {Array.from({ length: errorCount }).map((_, i) => (
              <div className="error-window" style={{ "--stack": i }} key={i}>
                <div className="error-bar"><span>ERROR</span><b>×</b></div>
                <div className="error-body"><span className="warning">!</span><strong>PAYMENT STATE<br />UNKNOWN</strong></div>
              </div>
            ))}
          </div>
        </div>
        <div className="crt-controls"><span /><span /><span /><b>AEGIS-486</b></div>
      </div>
      <div className="crt-keyboard"><div className="keys" /> <div className="space-key" /></div>
    </div>
  );
}

function PixelStarburst() {
  return <div className="starburst"><div className="crack crack-1" /><div className="crack crack-2" /><div className="crack crack-3" /><div className="crack crack-4" /><div className="crack crack-5" /><div className="crack crack-6" /></div>;
}

export default function IntroSequence({ onComplete }) {
  const [elapsed, setElapsed] = useState(0);
  const startedAt = useRef(Date.now());

  useEffect(() => {
    let frame;
    const tick = () => {
      const next = Date.now() - startedAt.current;
      setElapsed(next);
      if (next < TOTAL_MS) frame = requestAnimationFrame(tick);
      else onComplete?.();
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [onComplete]);

  const seconds = elapsed / 1000;
  const phase = seconds < 8 ? 0 : seconds < 14 ? 1 : seconds < 26 ? 2 : seconds < 34 ? 3 : seconds < 40 ? 4 : seconds < 46 ? 5 : 6;
  const taskProgress = Math.min(1, Math.max(0, (seconds - 1) / 6));
  const errorCount = phase === 3 ? Math.min(10, Math.max(0, Math.floor((seconds - 26) / 0.6) + 1)) : phase >= 4 ? 10 : 0;
  const resolveProgress = Math.min(1, Math.max(0, (seconds - 46) / 14));
  const message = useMemo(() => {
    if (phase === 0) return "TASK RECEIVED";
    if (phase === 1) return "AGENT ONLINE";
    if (phase === 2) return "EXECUTING PAYMENT SAGA";
    if (phase === 3) return "RECONCILIATION REQUIRED";
    if (phase === 4) return "TRUTH LOST";
    if (phase === 5) return "";
    return "Aegis is watching.";
  }, [phase]);

  return (
    <section className={`intro-sequence intro-phase-${phase}`} role="dialog" aria-label="RevertX cinematic introduction">
      <div className="intro-grain" />
      <div className="intro-topline"><span>REVERTX // AEGIS</span><span>DEMO MODE // {Math.min(60, Math.floor(seconds)).toString().padStart(2, "0")}S</span></div>
      {phase === 0 && <div className="scene scene-task"><div className="scene-label">01 / THE TASK</div><div className="typewriter"><div className="typewriter-roller" /><PixelTypewriter progress={taskProgress} /><div className="typewriter-body"><div className="typewriter-keys" /><div className="typewriter-space" /></div></div></div>}
      {phase === 1 && <div className="scene scene-agent"><div className="scene-label">02 / THE AGENT ACTIVATES</div><PixelRobot active={seconds > 9} /><div className="activation-burst">{message}<span>▮</span></div></div>}
      {(phase === 2 || phase === 3) && <div className="scene scene-crt"><div className="scene-label">03 / PAYMENT EXECUTION</div><PixelRobot active /><div className="cable"><i /><i /><i /><i /></div><CrtScreen errorCount={errorCount} phase={phase} /><div className="crt-caption">{message}<span className="blink">_</span></div></div>}
      {phase === 4 && <div className="scene scene-shatter"><PixelStarburst /><div className="shatter-copy">PAYMENT OUTCOME<br /><span>UNKNOWN</span></div></div>}
      {phase === 5 && <div className="scene scene-question"><p>When an AI agent fails mid-payment...<br /><strong>who tells it the truth?</strong></p></div>}
      {phase === 6 && <div className="scene scene-resolve"><div className="resolve-mark">✓</div><div className="resolve-title">AEGIS ONLINE</div><div className="resolve-copy">Watching. Verifying. Able to undo.</div><div className="resolve-progress"><i style={{ width: `${resolveProgress * 100}%` }} /></div><div className="resolve-handoff">HANDING OFF TO LIVE DASHBOARD<span>_</span></div></div>}
      {phase !== 5 && <div className="intro-status">{message}</div>}
      <button className="skip-intro" onClick={() => onComplete?.()}>SKIP INTRO <span>↵</span></button>
      <div className="intro-vignette" />
    </section>
  );
}
