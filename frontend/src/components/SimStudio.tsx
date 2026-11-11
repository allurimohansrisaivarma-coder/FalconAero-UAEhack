import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Layout, ScenarioMeta, SimStatus, Snapshot } from "../types";
import { clock } from "../sim";
import { Close, Minimize, Pause, Play, Restart } from "./icons";

interface Props {
  sim: SimStatus;
  setSim: (s: SimStatus) => void;
  snap: Snapshot;
  layout: Layout;
  T: number;
  notify: (t: string, err?: boolean) => void;
  onCompact: () => void;
  onBubble: () => void;
}

type Section = "playback" | "scenarios" | "weather" | "events" | "traffic" | "chaos" | "queue";
const SECTIONS: [Section, string][] = [
  ["playback", "Playback"], ["scenarios", "Scenario library"], ["weather", "Weather composer"], ["events", "Event builder"],
  ["traffic", "Traffic generator"], ["chaos", "Chaos mode"], ["queue", "Queue & history"],
];

export function SimStudio(p: Props) {
  const [sec, setSec] = useState<Section>("scenarios");
  const run = async (pr: Promise<unknown>, ok?: string) => {
    try {
      const r = await pr;
      if (r && typeof r === "object" && "playing" in (r as object)) p.setSim(r as SimStatus);
      if (ok) p.notify(ok);
      return r;
    } catch (e) { p.notify(e instanceof Error ? e.message : String(e), true); }
  };
  const sim = p.sim;

  return (
    <div className="studio-wrap">
      <div className="studio" role="dialog" aria-label="Scenario Studio">
        <header className="studio-h">
          <div>
            <div className="cap">Simulation · independent event source</div>
            <h2>Scenario Studio</h2>
          </div>
          <div className="studio-clock">
            <span className="mono">{clock(p.T, true)}Z</span>
            <button className="tbtn play" onClick={() => run(sim.playing ? api.sim.pause() : api.sim.play())}>{sim.playing ? <Pause /> : <Play />}</button>
            <div className="speeds">{sim.speeds.map((s) => <button key={s} className={sim.speed === s ? "on" : ""} onClick={() => run(api.sim.speed(s))}>×{s}</button>)}</div>
          </div>
          <span className="spacer" />
          <button className="icon-btn" onClick={p.onCompact} title="Restore to floating player"><Minimize /></button>
          <button className="icon-btn" onClick={p.onBubble} title="Minimise to bubble"><Close /></button>
        </header>
        <div className="studio-body">
          <nav className="studio-nav">
            {SECTIONS.map(([id, label]) => (
              <button key={id} className={`nav-item ${sec === id ? "on" : ""}`} onClick={() => setSec(id)}>
                {label}
                {id === "queue" && sim.scheduled.length > 0 && <span className="n">{sim.scheduled.length}</span>}
                {id === "chaos" && sim.chaos.enabled && <span className="n" style={{ color: "var(--warn)" }}>ON</span>}
                {id === "weather" && sim.weather_mode === "live" && <span className="n" style={{ color: "var(--ok)" }}>LIVE</span>}
              </button>
            ))}
            <div className="nav-foot dim3">
              The engine receives only events: exactly what it would get from AODB, MET and NOTAM feeds in production.
              <div className="mono" style={{ marginTop: 6 }}>{sim.sent} events sent</div>
            </div>
          </nav>
          <section className="studio-main">
            {sec === "playback" && <Playback {...p} run={run} />}
            {sec === "scenarios" && <Scenarios {...p} run={run} />}
            {sec === "weather" && <Weather {...p} run={run} />}
            {sec === "events" && <Events {...p} run={run} />}
            {sec === "traffic" && <Traffic {...p} run={run} />}
            {sec === "chaos" && <Chaos {...p} run={run} />}
            {sec === "queue" && <Queue {...p} run={run} />}
          </section>
        </div>
      </div>
    </div>
  );
}

type SP = Props & { run: (p: Promise<unknown>, ok?: string) => Promise<unknown> };

function Delay({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  return (
    <label className="field inline">
      <span>Start in</span>
      <select className="input" value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {[0, 5, 10, 15, 30, 45, 60, 90, 120].map((m) => <option key={m} value={m}>{m === 0 ? "now" : `${m} min`}</option>)}
      </select>
    </label>
  );
}

// ---------------------------------------------------------------- Playback
function Playback({ sim, T, run }: SP) {
  const frac = Math.max(0, Math.min(1, (T - sim.start) / Math.max(1, sim.end - sim.start)));
  return (
    <div className="pane">
      <h3>Playback</h3>
      <p className="dim">Simulated time drives the engine clock. Time only moves forward: the engine is event-sourced, like a real operation.</p>
      <div className="big-clock mono">{clock(T, true)}<small>Z</small></div>
      <div className="prog" style={{ height: 6 }}><div style={{ width: `${frac * 100}%` }} /></div>
      <div className="scrub-axis"><span>{clock(sim.start)}Z start</span><span>{clock(sim.end)}Z end of generated traffic</span></div>
      <div className="row-btns">
        <button className="btn" onClick={() => run(sim.playing ? api.sim.pause() : api.sim.play())}>{sim.playing ? <><Pause /> Pause</> : <><Play /> Play</>}</button>
        {[15, 30, 60].map((m) => <button key={m} className="btn" onClick={() => run(api.sim.jump(m), `Fast-forwarded ${m} min`)}>+{m} min</button>)}
        <button className="btn" onClick={() => confirm("Restart with the current traffic settings? Engine state resets.") && run(api.sim.restart(), "Restarted")}><Restart /> Restart</button>
      </div>
      <h4>Active scenarios</h4>
      {sim.active_scenarios.length === 0 && <div className="dim">None. Inject one from the Scenario library.</div>}
      {sim.active_scenarios.map((a) => (
        <div key={a.group} className="qrow">
          <span className="mono">{clock(a.at)}–{clock(a.until)}Z</span><span>{a.name}</span><span className="spacer" />
          <button className="btn sm ghost" onClick={() => run(api.sim.cancelGroup(a.group), "Remaining events cancelled")}>Cancel remaining</button>
        </div>
      ))}
      <h4>Current simulated METAR</h4>
      <div className="mono raw">{sim.current_metar ?? "—"}</div>
    </div>
  );
}

// ---------------------------------------------------------------- Scenarios
function Scenarios({ run }: SP) {
  const [items, setItems] = useState<ScenarioMeta[]>([]);
  const [cats, setCats] = useState<string[]>([]);
  const [cat, setCat] = useState("All");
  const [delay, setDelay] = useState(0);
  useEffect(() => { api.sim.scenarios().then((r) => { setItems(r.items); setCats(r.categories); }).catch(() => undefined); }, []);
  const list = items.filter((s) => cat === "All" || s.category === cat);
  return (
    <div className="pane">
      <div className="pane-h">
        <div><h3>Scenario library</h3><p className="dim">Timed event scripts modelled on real airport disruptions. Combine as many as you like.</p></div>
        <span className="spacer" />
        <Delay value={delay} onChange={setDelay} />
      </div>
      <div className="chips" style={{ marginBottom: 12 }}>
        {["All", ...cats].map((c) => <button key={c} className={`chip ${cat === c ? "on" : ""}`} onClick={() => setCat(c)}>{c}</button>)}
      </div>
      <div className="cards">
        {list.map((s) => (
          <article key={s.id} className="card">
            <div className="card-h">
              <span className={`tag ${s.severity === "critical" ? "crit" : s.severity === "high" ? "warn" : ""}`}>{s.severity.toUpperCase()}</span>
              <span className="dim3 mono">{s.category} · {s.duration} min</span>
            </div>
            <h4>{s.name}</h4>
            <p>{s.description}</p>
            <p className="ref">{s.reference}</p>
            <button className="btn sm" onClick={() => run(api.sim.scenario(s.id, delay), `${s.name} ${delay ? `scheduled in ${delay} min` : "injected"}`)}>
              {delay ? `Schedule in ${delay} min` : "Inject now"}
            </button>
          </article>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Weather
const VIS = [9999, 8000, 6000, 5000, 4000, 3000, 2000, 1500, 1200, 1000, 800, 600, 500, 400, 300, 250, 200, 150, 100, 50];
const WX = ["BR", "FG", "MIFG", "BCFG", "HZ", "DU", "SA", "BLDU", "BLSA", "SS", "DS", "-RA", "RA", "+RA", "SHRA", "VCTS", "TS", "TSRA", "+TSRA", "SQ"];
const CEIL = ["", "3000", "1500", "1000", "500", "300", "200", "100", "0"];

function Weather({ sim, run }: SP) {
  const b = sim.base_metar as Record<string, number | string | string[] | boolean | null>;
  const [f, setF] = useState<Record<string, unknown>>({
    wind_dir: b.wind_dir ?? 320, wind_kt: b.wind_kt ?? 8, gust_kt: 0, vis_m: b.vis_m ?? 9999, rvr_on: false, rvr_m: 550,
    wx: (b.wx as string[]) ?? [], ceiling_ft: "", vv: false, temp_c: b.temp_c ?? 31, dew_c: b.dew_c ?? 18, qnh: b.qnh ?? 1011,
  });
  const [prev, setPrev] = useState<Awaited<ReturnType<typeof api.sim.preview>> | null>(null);
  const [raw, setRaw] = useState("");
  const [delay, setDelay] = useState(0);
  const [live, setLive] = useState<Record<string, unknown> | null>(null);
  const fields = useMemo(() => ({
    wind_dir: f.wind_dir === "VRB" ? null : f.wind_dir, wind_kt: f.wind_kt, gust_kt: Number(f.gust_kt) || null, vis_m: f.vis_m,
    rvr_m: f.rvr_on ? f.rvr_m : null, wx: f.wx, ceiling_ft: f.ceiling_ft === "" ? null : Number(f.ceiling_ft), vv: f.vv,
    temp_c: f.temp_c, dew_c: f.dew_c, qnh: f.qnh, cavok: true,
  }), [f]);
  useEffect(() => {
    const h = window.setTimeout(() => api.sim.preview({ fields }).then(setPrev).catch(() => undefined), 250);
    return () => window.clearTimeout(h);
  }, [fields]);
  useEffect(() => { api.liveWeatherStatus().then(setLive).catch(() => undefined); }, [sim.weather_mode]);
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const toggleWx = (w: string) => set("wx", (f.wx as string[]).includes(w) ? (f.wx as string[]).filter((x) => x !== w) : [...(f.wx as string[]), w]);
  const visIdx = Math.max(0, VIS.indexOf(Number(f.vis_m)));

  return (
    <div className="pane">
      <div className="pane-h">
        <div><h3>Weather composer</h3><p className="dim">Compose a METAR; the engine's live assessment shows what it would do before you send it.</p></div>
        <span className="spacer" /><Delay value={delay} onChange={setDelay} />
      </div>
      <div className="wx-grid">
        <div className="form">
          <label className="field"><span>Visibility <b className="mono">{Number(f.vis_m) >= 9999 ? "10 km+" : `${f.vis_m} m`}</b></span>
            <input type="range" min={0} max={VIS.length - 1} value={visIdx} onChange={(e) => set("vis_m", VIS[Number(e.target.value)])} /></label>
          <label className="field"><span><input type="checkbox" checked={Boolean(f.rvr_on)} onChange={(e) => set("rvr_on", e.target.checked)} /> RVR <b className="mono">{f.rvr_on ? `${f.rvr_m} m` : "not reported"}</b></span>
            <input type="range" min={50} max={2000} step={25} value={Number(f.rvr_m)} disabled={!f.rvr_on} onChange={(e) => set("rvr_m", Number(e.target.value))} /></label>
          <div className="two">
            <label className="field"><span>Wind dir <b className="mono">{f.wind_dir === "VRB" ? "VRB" : `${f.wind_dir}°`}</b></span>
              <input type="range" min={0} max={360} step={10} value={f.wind_dir === "VRB" ? 0 : Number(f.wind_dir)} onChange={(e) => set("wind_dir", Number(e.target.value))} /></label>
            <label className="field"><span>Speed <b className="mono">{String(f.wind_kt)} kt</b></span>
              <input type="range" min={0} max={50} value={Number(f.wind_kt)} onChange={(e) => set("wind_kt", Number(e.target.value))} /></label>
          </div>
          <div className="two">
            <label className="field"><span>Gust <b className="mono">{Number(f.gust_kt) ? `${f.gust_kt} kt` : "none"}</b></span>
              <input type="range" min={0} max={65} value={Number(f.gust_kt)} onChange={(e) => set("gust_kt", Number(e.target.value))} /></label>
            <label className="field"><span>Ceiling / vertical visibility</span>
              <div style={{ display: "flex", gap: 6 }}>
                <select className="input" value={String(f.ceiling_ft)} onChange={(e) => set("ceiling_ft", e.target.value)}>
                  {CEIL.map((c) => <option key={c} value={c}>{c === "" ? "none" : `${c} ft`}</option>)}
                </select>
                <label className="nav-check" style={{ padding: 0 }}><input type="checkbox" checked={Boolean(f.vv)} onChange={(e) => set("vv", e.target.checked)} /> VV</label>
              </div></label>
          </div>
          <div className="field"><span>Present weather</span>
            <div className="chips">{WX.map((w) => <button key={w} className={`chip ${(f.wx as string[]).includes(w) ? "on" : ""}`} onClick={() => toggleWx(w)}>{w}</button>)}</div></div>
          <div className="three">
            <label className="field"><span>Temp °C</span><input className="input" type="number" value={Number(f.temp_c)} onChange={(e) => set("temp_c", Number(e.target.value))} /></label>
            <label className="field"><span>Dew °C</span><input className="input" type="number" value={Number(f.dew_c)} onChange={(e) => set("dew_c", Number(e.target.value))} /></label>
            <label className="field"><span>QNH</span><input className="input" type="number" value={Number(f.qnh)} onChange={(e) => set("qnh", Number(e.target.value))} /></label>
          </div>
        </div>
        <div className="assess">
          <div className="cap">Engine assessment (preview)</div>
          <div className="mono raw">{prev?.raw ?? "…"}</div>
          {prev && (
            <>
              <div className="assess-row"><span>Category</span><b className={prev.lvp ? "warn" : ""}>{prev.category.replace("LVP", "LVP CAT ")}</b></div>
              <div className="assess-row"><span>ILS required</span><b>CAT {prev.required_ils}</b></div>
              <div className="assess-row"><span>Taxi speed factor</span><b>×{prev.taxi_factor}</b></div>
              {Object.entries(prev.runways).map(([id, r]) => (
                <div key={id} className="assess-row"><span>Runway {id}</span><b className={r.mode === "CLOSED" ? "crit" : r.mode === "MIXED" ? "warn" : ""}>{r.mode} · {Math.round(60 / r.occ_arr)} arr/h · {Math.round(60 / r.occ_dep)} dep/h</b></div>
              ))}
              {Object.values(prev.runways).flatMap((r) => r.reasons).map((x) => <div key={x} className="adv">{x}</div>)}
              {prev.advisories.map((a) => <div key={a} className="adv">{a}</div>)}
            </>
          )}
          <button className="btn primary block" style={{ marginTop: 12 }} onClick={() => run(api.sim.weather({ fields, delay }), delay ? `METAR scheduled in ${delay} min` : "METAR sent")}>
            {delay ? `Schedule METAR in ${delay} min` : "Send METAR now"}
          </button>
        </div>
      </div>
      <h4>Raw METAR / TAF</h4>
      <textarea className="input mono" rows={3} value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="METAR OMDB 100400Z 00000KT 0150 R12L/0250N FG VV001 21/21 Q1015   or   TAF OMDB 100500Z 1006/1112 …" />
      <div className="row-btns"><button className="btn sm" disabled={!raw.trim()} onClick={() => run(api.sim.weather({ raw, delay }), "Weather report sent")}>Send report</button></div>
      <h4>Live OMDB weather</h4>
      <div className="live-card">
        <div>
          <div>Use the real Dubai METAR/TAF from aviationweather.gov (NOAA AWC) instead of simulated weather.</div>
          {live && <div className="mono dim3" style={{ fontSize: 11, marginTop: 6 }}>{String(live.metar ?? live.error ?? "not fetched yet")}</div>}
        </div>
        <button className={`switch ${sim.weather_mode === "live" ? "on" : ""}`} role="switch" aria-checked={sim.weather_mode === "live"} aria-label="Live weather"
          onClick={() => run(api.liveWeather(sim.weather_mode !== "live").then((s) => { setLive(s); return api.sim.state(); }), sim.weather_mode === "live" ? "Simulated weather" : "Live OMDB weather on")} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Event builder
type FieldSpec = { k: string; label: string; kind: "select" | "text" | "number" | "until" | "bool" | "multi" | "minutes"; options?: string[]; def?: unknown };

function specs(layout: Layout, snap: Snapshot): Record<string, FieldSpec[]> {
  const rw = layout.runways.map((r) => r.id);
  const st = layout.stands.map((s) => s.id);
  const fl = Object.values(snap.flights).filter((f) => f.active).map((f) => f.callsign).sort();
  return {
    RUNWAY_STATUS: [{ k: "runway", label: "Runway", kind: "select", options: rw }, { k: "status", label: "Status", kind: "select", options: ["CLOSED", "RESTRICTED", "OPEN"] },
      { k: "rate_factor", label: "Capacity factor (restricted)", kind: "number", def: 0.6 }, { k: "ils_cat", label: "ILS category", kind: "select", options: ["", "III", "II", "I", "NONE"] },
      { k: "reason", label: "Reason", kind: "text", def: "Runway inspection" }, { k: "until", label: "Duration", kind: "until", def: 30 }],
    STAND_STATUS: [{ k: "stand", label: "Stand", kind: "select", options: st }, { k: "status", label: "Status", kind: "select", options: ["UNAVAILABLE", "AVAILABLE"] },
      { k: "reason", label: "Reason", kind: "text", def: "Jet bridge fault" }, { k: "until", label: "Duration", kind: "until", def: 60 }],
    TAXIWAY_STATUS: [{ k: "taxiway", label: "Taxiway", kind: "select", options: layout.taxiway_names }, { k: "status", label: "Status", kind: "select", options: ["CLOSED", "OPEN"] },
      { k: "reason", label: "Reason", kind: "text", def: "Works in progress" }, { k: "until", label: "Duration", kind: "until", def: 60 }],
    LIGHTNING_ALERT: [{ k: "level", label: "Level", kind: "select", options: ["RED", "AMBER", "CLEAR"] }, { k: "reason", label: "Reason", kind: "text", def: "Strikes within 5 km" },
      { k: "until", label: "Duration", kind: "until", def: 30 }],
    GROUND_STOP: [{ k: "active", label: "Active", kind: "bool", def: true }, { k: "scope", label: "Scope", kind: "select", options: ["DEP", "ALL"] },
      { k: "reason", label: "Reason", kind: "text", def: "Security" }, { k: "until", label: "Duration", kind: "until", def: 30 }],
    FLOW_RESTRICTION: [{ k: "kind", label: "Movements", kind: "select", options: ["ARR", "DEP"] }, { k: "max_per_hour", label: "Max per hour (blank = lift)", kind: "number", def: 20 },
      { k: "reason", label: "Reason", kind: "text", def: "ATC staffing" }, { k: "until", label: "Duration", kind: "until", def: 90 }],
    AIRSPACE_CLOSURE: [{ k: "regions", label: "Regions", kind: "multi", options: ["NORTH", "WEST", "EAST", "SOUTH", "ALL"], def: ["NORTH"] },
      { k: "active", label: "Closed", kind: "bool", def: true }, { k: "reason", label: "Reason", kind: "text", def: "Airspace restriction" }, { k: "until", label: "Duration", kind: "until", def: 120 }],
    FLIGHT_UPDATE: [{ k: "callsign", label: "Flight", kind: "select", options: fl }, { k: "est", label: "New ETA/ETD in (min)", kind: "minutes" },
      { k: "tobt", label: "New TOBT in (min, departures)", kind: "minutes" }, { k: "priority", label: "Priority", kind: "select", options: ["", "normal", "high", "emergency"] },
      { k: "status", label: "Status", kind: "select", options: ["", "CANCELLED", "DIVERTED"] }, { k: "reason", label: "Reason", kind: "text", def: "" }],
    FLIGHT_UPSERT: [{ k: "callsign", label: "Callsign", kind: "text", def: "UAE7777" }, { k: "kind", label: "Kind", kind: "select", options: ["ARR", "DEP"] },
      { k: "aircraft", label: "Aircraft type", kind: "select", options: ["B77W", "A388", "A359", "B789", "A320", "B38M", "A321", "ZZZZ"] },
      { k: "sched", label: "STA/STD in (min)", kind: "minutes", def: 20 }, { k: "origin_dest", label: "Origin / destination", kind: "text", def: "LHR" },
      { k: "region", label: "Route region", kind: "select", options: ["NORTH", "WEST", "EAST", "SOUTH"] }, { k: "priority", label: "Priority", kind: "select", options: ["normal", "high", "emergency"] },
      { k: "stand", label: "Parked stand (DEP)", kind: "select", options: ["", ...st] }],
    NOTAM: [{ k: "text", label: "NOTAM text", kind: "text", def: "RWY 12R CLSD DUE WIP" }],
    _SIM_FEED_OUTAGE: [{ k: "minutes", label: "MET feed outage (min)", kind: "number", def: 60 }],
  };
}

const TYPE_LABEL: Record<string, string> = {
  RUNWAY_STATUS: "Runway status", STAND_STATUS: "Stand status", TAXIWAY_STATUS: "Taxiway status", LIGHTNING_ALERT: "Lightning alert",
  GROUND_STOP: "Ground stop", FLOW_RESTRICTION: "ATFM flow restriction", AIRSPACE_CLOSURE: "Airspace closure", FLIGHT_UPDATE: "Flight update",
  FLIGHT_UPSERT: "New flight", NOTAM: "NOTAM (free text)", _SIM_FEED_OUTAGE: "Data feed outage",
};

function Events({ layout, snap, sim, run }: SP) {
  const S = useMemo(() => specs(layout, snap), [layout, snap]);
  const [type, setType] = useState("RUNWAY_STATUS");
  const [vals, setVals] = useState<Record<string, unknown>>({});
  const [delay, setDelay] = useState(0);
  useEffect(() => {
    const v: Record<string, unknown> = {};
    for (const f of S[type]) v[f.k] = f.def ?? (f.options ? f.options[0] : f.kind === "bool" ? true : "");
    setVals(v);
  }, [type]); // eslint-disable-line react-hooks/exhaustive-deps

  const build = () => {
    const at = sim.now + delay;
    const ev: Record<string, unknown> = { type };
    for (const f of S[type]) {
      const v = vals[f.k];
      if (v === "" || v === undefined || v === null) continue;
      if (f.kind === "until") { if (Number(v) > 0) ev.until = Math.round(at + Number(v)); continue; }
      if (f.kind === "minutes") { ev[f.k] = Math.round(at + Number(v)); continue; }
      if (f.kind === "number") { ev[f.k] = Number(v); continue; }
      ev[f.k] = v;
    }
    if (type === "FLOW_RESTRICTION" && vals.max_per_hour === "") ev.max_per_hour = null;
    if (type === "RUNWAY_STATUS" && ev.status !== "RESTRICTED") delete ev.rate_factor;
    return ev;
  };

  return (
    <div className="pane">
      <div className="pane-h">
        <div><h3>Event builder</h3><p className="dim">Send any single operational event with your own parameters. These are the same event types a production feed uses.</p></div>
        <span className="spacer" /><Delay value={delay} onChange={setDelay} />
      </div>
      <div className="chips" style={{ marginBottom: 14 }}>
        {Object.keys(S).map((t) => <button key={t} className={`chip ${type === t ? "on" : ""}`} onClick={() => setType(t)}>{TYPE_LABEL[t]}</button>)}
      </div>
      <div className="form grid2">
        {S[type].map((f) => (
          <label key={f.k} className="field">
            <span>{f.label}</span>
            {f.kind === "select" && (
              <select className="input" value={String(vals[f.k] ?? "")} onChange={(e) => setVals((v) => ({ ...v, [f.k]: e.target.value }))}>
                {f.options!.map((o) => <option key={o} value={o}>{o === "" ? "—" : o}</option>)}
              </select>
            )}
            {f.kind === "text" && <input className="input" value={String(vals[f.k] ?? "")} onChange={(e) => setVals((v) => ({ ...v, [f.k]: e.target.value }))} />}
            {(f.kind === "number" || f.kind === "minutes") && <input className="input" type="number" value={String(vals[f.k] ?? "")} onChange={(e) => setVals((v) => ({ ...v, [f.k]: e.target.value }))} placeholder={f.kind === "minutes" ? "minutes from start" : ""} />}
            {f.kind === "until" && (
              <select className="input" value={String(vals[f.k] ?? 0)} onChange={(e) => setVals((v) => ({ ...v, [f.k]: Number(e.target.value) }))}>
                {[0, 15, 20, 30, 45, 60, 90, 120, 180, 240].map((m) => <option key={m} value={m}>{m === 0 ? "no end time" : `${m} min`}</option>)}
              </select>
            )}
            {f.kind === "bool" && <button className={`switch ${vals[f.k] ? "on" : ""}`} onClick={(e) => { e.preventDefault(); setVals((v) => ({ ...v, [f.k]: !v[f.k] })); }} />}
            {f.kind === "multi" && (
              <div className="chips">{f.options!.map((o) => {
                const arr = (vals[f.k] as string[]) ?? [];
                return <button key={o} className={`chip ${arr.includes(o) ? "on" : ""}`} onClick={(e) => { e.preventDefault(); setVals((v) => ({ ...v, [f.k]: arr.includes(o) ? arr.filter((x) => x !== o) : [...arr, o] })); }}>{o}</button>;
              })}</div>
            )}
          </label>
        ))}
      </div>
      <div className="mono raw" style={{ marginTop: 12 }}>{JSON.stringify(build())}</div>
      <div className="row-btns">
        <button className="btn primary" onClick={() => run(api.sim.event(build(), delay), delay ? `Scheduled in ${delay} min` : "Event sent")}>{delay ? `Schedule in ${delay} min` : "Send event now"}</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Traffic
const TP: { k: string; label: string; min: number; max: number; step: number; fmt?: (v: number) => string }[] = [
  { k: "duration_h", label: "Duration (h)", min: 1, max: 8, step: 0.5 },
  { k: "arrivals_per_h", label: "Arrivals per hour", min: 2, max: 20, step: 1 },
  { k: "mix_f", label: "A380 / Code F share", min: 0, max: 0.3, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%` },
  { k: "mix_e", label: "Wide-body (Code E) share", min: 0, max: 0.9, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%` },
  { k: "eta_sd", label: "ETA uncertainty σ (min)", min: 0, max: 20, step: 1 },
  { k: "late_prob", label: "Departures with ground delays", min: 0, max: 0.6, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%` },
  { k: "initial_parked", label: "Stands occupied at start", min: 0.2, max: 0.9, step: 0.05, fmt: (v) => `${Math.round(v * 100)}%` },
  { k: "seed", label: "Random seed", min: 1, max: 999, step: 1 },
];

function Traffic({ sim, run }: SP) {
  const [p, setP] = useState<Record<string, number | boolean>>({ ...sim.params });
  return (
    <div className="pane">
      <h3>Traffic generator</h3>
      <p className="dim">Synthetic Gulf-hub traffic: banked waves, wide-body heavy, real ICAO types. Arrivals turn into departures after on-block.
        Restarting resets the engine and sends a fresh schedule feed.</p>
      <div className="form grid2">
        {TP.map((t) => (
          <label key={t.k} className="field">
            <span>{t.label} <b className="mono">{t.fmt ? t.fmt(Number(p[t.k])) : String(p[t.k])}</b></span>
            <input type="range" min={t.min} max={t.max} step={t.step} value={Number(p[t.k])} onChange={(e) => setP((x) => ({ ...x, [t.k]: Number(e.target.value) }))} />
          </label>
        ))}
        <label className="field"><span>Hub banks (waves)</span><button className={`switch ${p.waves ? "on" : ""}`} onClick={() => setP((x) => ({ ...x, waves: !x.waves }))} /></label>
        <label className="field"><span>Turnaround departures</span><button className={`switch ${p.turn_departures ? "on" : ""}`} onClick={() => setP((x) => ({ ...x, turn_departures: !x.turn_departures }))} /></label>
      </div>
      <div className="row-btns">
        <button className="btn primary" onClick={() => confirm("Restart the simulation with these traffic settings?") && run(api.sim.restart(p), "Restarted with new traffic")}><Restart /> Restart with these settings</button>
        <button className="btn ghost" onClick={() => setP({ ...sim.params })}>Revert</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Chaos
function Chaos({ sim, run }: SP) {
  const [rate, setRate] = useState(sim.chaos.per_hour);
  const [seed, setSeed] = useState(sim.chaos.seed);
  return (
    <div className="pane">
      <h3>Chaos mode</h3>
      <p className="dim">Randomly injects realistic minor disruptions (FOD, bird strikes, stand faults, AOG, bunching, ATFM, lightning, medical,
        cancellations) while the simulation runs. Use it to test how FalconAero copes with an unscripted day.</p>
      <div className="live-card">
        <div><b>Chaos {sim.chaos.enabled ? "enabled" : "disabled"}</b><div className="dim3">~{rate} disruptions per simulated hour</div></div>
        <button className={`switch ${sim.chaos.enabled ? "on" : ""}`} onClick={() => run(api.sim.chaos(!sim.chaos.enabled, rate, seed).then(() => api.sim.state()), sim.chaos.enabled ? "Chaos off" : "Chaos on")} />
      </div>
      <div className="form grid2" style={{ marginTop: 12 }}>
        <label className="field"><span>Disruptions per hour <b className="mono">{rate}</b></span>
          <input type="range" min={0.5} max={12} step={0.5} value={rate} onChange={(e) => setRate(Number(e.target.value))} /></label>
        <label className="field"><span>Seed</span><input className="input" type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></label>
      </div>
      <div className="row-btns"><button className="btn" onClick={() => run(api.sim.chaos(sim.chaos.enabled, rate, seed).then(() => api.sim.state()), "Chaos settings applied")}>Apply</button></div>
    </div>
  );
}

// ---------------------------------------------------------------- Queue
function Queue({ sim, run }: SP) {
  return (
    <div className="pane">
      <h3>Scheduled events</h3>
      {sim.scheduled.length === 0 && <div className="dim">Nothing queued.</div>}
      {sim.scheduled.map((s) => (
        <div key={s.id} className="qrow">
          <span className="mono">{clock(s.at)}Z</span><span className="tag">{s.type.replace("_", " ")}</span><span>{s.label}</span><span className="spacer" />
          <button className="btn sm ghost" onClick={() => run(api.sim.cancel(s.id))}>Cancel</button>
        </div>
      ))}
      <h4>History</h4>
      {sim.history.length === 0 && <div className="dim">No events fired yet.</div>}
      {sim.history.map((h, i) => (
        <div key={i} className="qrow">
          <span className="mono">{clock(h.t)}Z</span><span style={{ color: h.ok ? undefined : "var(--crit)" }}>{h.label}{h.error ? ` · ${h.error}` : ""}</span>
        </div>
      ))}
    </div>
  );
}
