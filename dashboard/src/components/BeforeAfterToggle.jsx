import { useState, useEffect } from "react";

export default function BeforeAfterToggle() {
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const apiKey = import.meta.env.VITE_PROXY_API_KEY || "test-key-123";

  useEffect(() => {
    fetch("http://localhost:8000/aegis_status")
      .then(r => r.json())
      .then(d => setEnabled(d.aegis_enabled ?? true))
      .catch(() => {});
  }, []);

  async function toggle() {
    setBusy(true);
    try {
      const resp = await fetch("http://localhost:8000/aegis_mode", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({ enabled: !enabled }),
      });
      if (resp.ok) setEnabled((await resp.json()).aegis_enabled);
    } catch {} finally { setBusy(false); }
  }

  return (
    <div style={{display:"flex",alignItems:"center",gap:8}}>
      <span style={{fontSize:12,opacity:0.6}}>Aegis</span>
      <button
        onClick={toggle} disabled={busy}
        title={enabled ? "Disable Aegis" : "Enable Aegis"}
        style={{
          position:"relative",width:40,height:20,borderRadius:10,border:"none",
          cursor:"pointer",opacity:busy?0.5:1,transition:"background 0.2s",
          background:enabled?"#3fb950":"#f85149"
        }}
      >
        <span style={{
          position:"absolute",top:2,left:enabled?20:2,
          width:16,height:16,borderRadius:"50%",background:"white",
          transition:"left 0.2s",display:"block"
        }}/>
      </button>
      <span style={{fontSize:12,fontWeight:600,color:enabled?"#3fb950":"#f85149"}}>
        {enabled ? "ON" : "OFF"}
      </span>
    </div>
  );
}
