import { useMemo, useState } from "react";
import type { Layout, Snapshot } from "../types";
import { clock, position, type Phase } from "../sim";

interface Props {
  snap: Snapshot;
  layout: Layout;
  T: number;
  selected: string | null;
  onSelect: (cs: string | null) => void;
}

type Tab = "ARR" | "GND" | "DEP";
const AIR: Phase[] = ["INBOUND", "HOLDING", "FINAL"];
const GND: Phase[] = ["LANDING", "TAXI IN", "WAIT STAND", "ON BLOCK", "ON STAND", "TAXI OUT", "LINE UP"];
const DEP: Phase[] = ["ON STAND", "TAXI OUT", "LINE UP", "AIRBORNE", "DEPARTED", "CANCELLED"];
/** Movements that are over: listed dimmed at the end, left out of the tab counts. */
const DONE: Phase[] = ["AIRBORNE", "DEPARTED", "CANCELLED", "DIVERTED"];
const LIVE: Phase[] = ["FINAL", "LANDING", "TAXI IN", "TAXI OUT", "LINE UP", "AIRBORNE"];
/** ICAO wake turbulence category from the aerodrome code letter: super, heavy, medium. */
const WTC: Record<string, string> = { F: "J", E: "H", D: "H", C: "M" };

export function FlightStrips({ snap, layout, T, selected, onSelect }: Props) {
  const [tab, setTab] = useState<Tab>("ARR");
  const [q, setQ] = useState("");
  const conflicted = useMemo(() => new Set(snap.conflicts.filter((c) => c.type !== "unplanned").flatMap((c) => c.flights)), [snap.conflicts]);
  const unplannedSet = useMemo(() => new Set(snap.conflicts.filter((c) => c.type === "unplanned").flatMap((c) => c.flights)), [snap.conflicts]);

  const groups = useMemo(() => {
    const all = Object.values(snap.plan).map((r) => {
      const f = snap.flights[r.callsign];
      return { r, phase: position(r, T, layout, f?.aldt, f?.aobt, f?.atot).phase };
    });
    // Arrivals: still to land. On ground: every aircraft physically on the airfield, parked ones included.
    // Departures: still to take off. Finished movements stay listed, dimmed, but are not counted.
    const arr = all.filter(({ r, phase }) => r.kind === "ARR" && (AIR.includes(phase) || phase === "DIVERTED") && r.status !== "TURNED");
    const gnd = all.filter(({ phase }) => GND.includes(phase));
    const dep = all.filter(({ r, phase }) => r.kind === "DEP" && DEP.includes(phase));
    const unpl = Object.values(snap.flights).filter((f) => f.active && !snap.plan[f.callsign]);
    const by = (a: { r: { t: number } }, b: { r: { t: number } }) => a.r.t - b.r.t;
    const dsort = (a: { r: { t: number }; phase: Phase }, b: { r: { t: number }; phase: Phase }) => Number(DONE.includes(a.phase)) - Number(DONE.includes(b.phase)) || by(a, b);
    const live = (xs: { phase: Phase }[]) => xs.filter((x) => !DONE.includes(x.phase)).length;
    const count: Record<Tab, number> = {
      ARR: live(arr) + unpl.filter((f) => f.kind === "ARR").length,
      GND: gnd.length,
      DEP: live(dep) + unpl.filter((f) => f.kind === "DEP").length,
    };
    return { ARR: arr.sort(dsort), GND: gnd.sort(by), DEP: dep.sort(dsort), unpl, count };
  }, [snap.plan, snap.flights, T, layout]);

  const filt = (cs: string) => !q || cs.toLowerCase().includes(q.toLowerCase());
  const list = groups[tab].filter(({ r }) => filt(r.callsign));

  return (
    <aside className="panel strips">
      <div className="panel-h">
        <span className="cap">Flight strips</span>
        <span className="spacer" />
        <input className="search" placeholder="Find callsign" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find callsign" />
      </div>
      <div className="strip-tabs" role="tablist">
        {([["ARR", "Arrivals"], ["GND", "On ground"], ["DEP", "Departures"]] as [Tab, string][]).map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={`strip-tab ${tab === id ? "on" : ""}`} onClick={() => setTab(id)}>
            {label}<span className="n">{groups.count[id]}</span>
          </button>
        ))}
      </div>
      <div className="strip-list">
        {groups.unpl.filter((f) => (tab === "ARR" ? f.kind === "ARR" : tab === "DEP" ? f.kind === "DEP" : false) && filt(f.callsign)).map((f) => (
          <div key={f.callsign} className={`strip ${f.kind === "ARR" ? "arr" : "dep"} ${f.priority === "emergency" ? "emerg" : "hold"} ${selected === f.callsign ? "sel" : ""}`} onClick={() => onSelect(f.callsign)}>
            <div className="bar" />
            <div className="main">
              <div className="l1"><span className="cs">{f.callsign}</span><span className="typ">{f.aircraft}</span><span className="od">{f.origin_dest}</span></div>
              <div className="l2"><span style={{ color: f.priority === "emergency" ? "var(--crit)" : "var(--warn)" }}>{f.priority === "emergency" ? "EMERGENCY · " : ""}AWAITING ALLOCATION</span></div>
            </div>
            <div className="side"><span className="time">{clock(f.kind === "DEP" ? Math.max(f.est, f.tobt ?? 0) : f.est)}</span><span className="phase hold">{unplannedSet.has(f.callsign) ? "UNPLANNED" : "NEW"}</span></div>
          </div>
        ))}
        {list.map(({ r, phase }) => {
          const f = snap.flights[r.callsign];
          const cls = ["strip", r.kind === "ARR" ? "arr" : "dep", phase === "HOLDING" ? "hold" : "", r.priority === "emergency" ? "emerg" : "",
            conflicted.has(r.callsign) ? "conf" : "", selected === r.callsign ? "sel" : "",
            DONE.includes(phase) ? "done" : ""].join(" ");
          const late = f && f.kind === "DEP" && f.tobt && f.tobt > f.est;
          return (
            <div key={r.callsign} className={cls} onClick={() => onSelect(selected === r.callsign ? null : r.callsign)}>
              <div className="bar" />
              <div className="main">
                <div className="l1">
                  <span className="cs">{r.callsign}</span>
                  <span className="typ">{r.type}<span className="wtc" title="Wake turbulence category">/{WTC[r.code] ?? "M"}</span></span>
                  <span className="od">{r.kind === "ARR" ? `${r.origin_dest}→DXB` : `DXB→${r.origin_dest}`}</span>
                </div>
                <div className="l2">
                  <span>RWY <b>{r.runway ?? "—"}</b></span>
                  <span>STD <b>{r.diverted ? "DIV" : r.stand ?? "—"}</b></span>
                  <span>CAT <b>{r.code}</b></span>
                  {r.priority !== "normal" && <span style={{ color: "var(--crit)" }}>{r.priority.toUpperCase()}</span>}
                  {late && <span style={{ color: "var(--warn)" }} title="Target off-block time revised">TOBT {clock(f!.tobt)}</span>}
                  {r.ground_wait ? <span style={{ color: "var(--warn)" }} title="Planned ground wait for stand">WAIT {r.ground_wait}′</span> : null}
                </div>
              </div>
              <div className="side">
                <span className="time" title={r.kind === "ARR" ? "Target / actual landing time" : "Target / actual take-off time"}>
                  <small>{r.kind === "ARR" ? (r.actual ? "ALDT" : "TLDT") : (r.actual ? "ATOT" : "TTOT")}</small>{clock(r.t)}
                </span>
                {r.added > 0 ? <span className="delay" title="Airport-caused delay vs ETA / ready time">+{r.added}</span> : <span className="ontime">±0</span>}
                <span className={`phase ${phase === "HOLDING" || phase === "WAIT STAND" ? "hold" : LIVE.includes(phase) ? "live" : ""}`}>{phase}</span>
              </div>
            </div>
          );
        })}
        {list.length === 0 && <div className="empty">No movements in this list.</div>}
      </div>
    </aside>
  );
}
