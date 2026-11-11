import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Snapshot } from "../types";
import { clock } from "../sim";
import { Spark } from "./icons";

interface Props { snap: Snapshot; notify: (t: string, err?: boolean) => void }

function describe(e: Record<string, unknown>): string {
  const t = String(e.type);
  const until = e.until ? ` until ${clock(Number(e.until))}Z` : "";
  switch (t) {
    case "RUNWAY_STATUS": return `Runway ${e.runway} → ${e.status}${e.ils_cat ? ` (ILS CAT ${e.ils_cat})` : ""}${e.rate_factor && e.rate_factor !== 1 ? ` ${Math.round(Number(e.rate_factor) * 100)}%` : ""}${until}`;
    case "STAND_STATUS": return `Stand ${e.stand} → ${e.status}${until}`;
    case "TAXIWAY_STATUS": return `Taxiway ${e.taxiway} → ${e.status}${until}`;
    case "LIGHTNING_ALERT": return `Lightning ${e.level}${until}`;
    case "GROUND_STOP": return `Ground stop ${e.active ? "ON" : "OFF"} (${e.scope ?? "DEP"})${until}`;
    case "FLOW_RESTRICTION": return `ATFM ${e.kind} cap ${e.max_per_hour ?? "lifted"}/h${until}`;
    case "AIRSPACE_CLOSURE": return `Airspace ${e.active === false ? "reopened" : "closed"}: ${(e.regions as string[]).join(", ")}${until}`;
    case "FLIGHT_UPDATE": return `${e.callsign}: ${["est", "tobt"].filter((k) => e[k]).map((k) => `${k.toUpperCase()} ${clock(Number(e[k]))}Z`).join(" ")}${e.priority ? ` priority ${e.priority}` : ""}${e.status ? ` ${e.status}` : ""}`;
    case "WEATHER_METAR": return `METAR ${String(e.raw).slice(0, 60)}`;
    case "WEATHER_TAF": return `TAF ${String(e.raw).slice(0, 60)}`;
    default: return t;
  }
}

export function Assistant({ snap, notify }: Props) {
  const [brief, setBrief] = useState<{ text: string; engine: string } | null>(null);
  const [briefing, setBriefing] = useState(false);
  const [note, setNote] = useState("");
  const [parsed, setParsed] = useState<{ events: Record<string, unknown>[]; errors: string[]; summary: string; engine: string } | null>(null);
  const [keep, setKeep] = useState<boolean[]>([]);
  const [q, setQ] = useState("");
  const [chat, setChat] = useState<{ role: "user" | "assistant"; content: string; meta?: string }[]>([]);
  const [asking, setAsking] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  const loadBrief = async () => {
    setBriefing(true);
    try { setBrief(await api.aiBrief()); } catch (e) { notify(String(e), true); } finally { setBriefing(false); }
  };
  useEffect(() => { loadBrief(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [chat]);

  const parse = async () => {
    if (!note.trim()) return;
    try {
      const r = await api.aiParse(note);
      setParsed(r);
      setKeep(r.events.map(() => true));
    } catch (e) { notify(String(e), true); }
  };
  const ingest = async () => {
    if (!parsed) return;
    const evs = parsed.events.filter((_, i) => keep[i]).map((e) => ({ ...e, source: "operator" }));
    if (!evs.length) return;
    try {
      const r = await api.events(evs);
      const bad = r.results.filter((x) => !x.ok);
      notify(bad.length ? `${evs.length - bad.length} ingested, ${bad.length} rejected: ${bad[0].error}` : `${evs.length} event${evs.length > 1 ? "s" : ""} ingested`, bad.length > 0);
      setParsed(null);
      setNote("");
    } catch (e) { notify(String(e), true); }
  };
  const ask = async (question: string) => {
    if (!question.trim()) return;
    const history = chat.map(({ role, content }) => ({ role, content }));
    setChat((c) => [...c, { role: "user", content: question }]);
    setQ("");
    setAsking(true);
    try {
      const r = await api.aiAsk(question, history);
      setChat((c) => [...c, { role: "assistant", content: r.text, meta: r.tools.length ? `used ${[...new Set(r.tools)].join(", ")}` : r.engine }]);
    } catch (e) { setChat((c) => [...c, { role: "assistant", content: String(e), meta: "error" }]); } finally { setAsking(false); }
  };

  return (
    <div className="side-scroll assistant">
      <div className="section">
        <div className="sec-h">
          <span className="cap">Situation briefing</span>
          <span className={`tag ${brief?.engine === "llm" ? "ai" : ""}`}>{brief?.engine === "llm" ? `AI · ${snap.ai.model}` : "TEMPLATE"}</span>
          <span className="spacer" />
          <button className="btn sm ghost" onClick={loadBrief} disabled={briefing}>{briefing ? "Writing…" : "Refresh"}</button>
        </div>
        <p className="brief">{brief?.text ?? "…"}</p>
        {!snap.ai.enabled && <p className="dim3" style={{ fontSize: 11.5 }}>Add <span className="mono">GROQ_API_KEY</span> to <span className="mono">backend/.env</span> for AI briefings, NOTAM parsing and Q&amp;A.</p>}
      </div>

      <div className="section">
        <div className="sec-h"><span className="cap">Report / NOTAM intake</span></div>
        <textarea className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)}
          placeholder={"e.g. RWY 30L CLSD DUE FOD UNTIL 1645\nStand B3 jet bridge u/s for 90 min\nUAE231 delayed 40 min, catering"} />
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button className="btn sm" onClick={parse} disabled={!note.trim()}><Spark size={13} /> Interpret</button>
          <span className="dim3" style={{ fontSize: 11.5, alignSelf: "center" }}>Proposed events are shown for confirmation before ingestion.</span>
        </div>
        {parsed && (
          <div className="parsed">
            <div className="dim3" style={{ fontSize: 11.5, marginBottom: 6 }}>{parsed.engine === "llm" ? "AI" : "Rule-based"} interpretation · {parsed.summary}</div>
            {parsed.events.length === 0 && <div className="dim">Nothing recognised. Rephrase or use the Simulation event builder.</div>}
            {parsed.events.map((e, i) => (
              <label key={i} className="pe">
                <input type="checkbox" checked={keep[i] ?? true} onChange={(x) => setKeep((k) => k.map((v, j) => (j === i ? x.target.checked : v)))} />
                <span className="mono">{describe(e)}</span>
              </label>
            ))}
            {parsed.errors.map((er, i) => <div key={i} className="err">{er}</div>)}
            {parsed.events.length > 0 && <button className="btn sm primary" style={{ marginTop: 8 }} onClick={ingest}>Ingest {keep.filter(Boolean).length} event(s)</button>}
          </div>
        )}
      </div>

      <div className="section chat">
        <div className="sec-h"><span className="cap">Ask FalconAero</span></div>
        <div className="msgs">
          {chat.length === 0 && (
            <div className="suggest">
              {["What is driving the delays right now?", "Which arrivals are holding and why?", "What does the next 2 hours look like?", "Summarise the recommended recovery plan"].map((s) => (
                <button key={s} className="btn sm ghost" onClick={() => ask(s)} disabled={!snap.ai.enabled}>{s}</button>
              ))}
            </div>
          )}
          {chat.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div>{m.content}</div>
              {m.meta && <div className="meta">{m.meta}</div>}
            </div>
          ))}
          {asking && <div className="msg assistant"><div className="dim">Checking live state…</div></div>}
          <div ref={endRef} />
        </div>
        <form onSubmit={(e) => { e.preventDefault(); ask(q); }} style={{ display: "flex", gap: 6 }}>
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder={snap.ai.enabled ? "Ask about flights, capacity, the plan…" : "AI disabled (no API key)"} disabled={!snap.ai.enabled} />
          <button className="btn sm" disabled={!snap.ai.enabled || asking || !q.trim()}>Ask</button>
        </form>
      </div>
    </div>
  );
}
