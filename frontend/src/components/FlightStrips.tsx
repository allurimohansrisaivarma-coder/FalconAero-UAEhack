import { useMemo, useState } from "react";
import type { Snapshot } from "../types";
import { clock, type Phase } from "../sim";
import type { Ac } from "../traffic";

interface Props {
  snap: Snapshot;
  traffic: Map<string, Ac>;
  selected: string | null;
  onSelect: (cs: string | null) => void;
}

type Tab = "ARR" | "GND" | "DEP";
const LIVE: Phase[] = ["APPROACH", "FINAL", "LANDING", "TAXI IN", "TAXI OUT", "LINE UP", "AIRBORNE"];
/** ICAO wake turbulence category from the aerodrome code letter: super, heavy, medium. */
const WTC: Record<string, string> = { F: "J", E: "H", D: "H", C: "M" };
/** How long a finished departure stays listed, dimmed. */
const RECENT_MIN = 30;

const when = (ac: Ac) => ac.row?.t ?? (ac.f.kind === "DEP" ? Math.max(ac.f.est, ac.f.tobt ?? 0) : ac.f.est);

/**
 * Tabs mirror the maps: Arrivals are the aircraft on the radar (later ones listed below a divider),
 * On ground is exactly what the surface map draws, Departures are those still on the field or climbing out.
 */
export function FlightStrips({ snap, traffic, selected, onSelect }: Props) {
  const [tab, setTab] = useState<Tab>("ARR");
  const [q, setQ] = useState("");
  const conflicted = useMemo(() => new Set(snap.conflicts.filter((c) => c.type !== "unplanned").flatMap((c) => c.flights)), [snap.conflicts]);

  const groups = useMemo(() => {
    const all = [...traffic.values()].sort((a, b) => when(a) - when(b));
    const T = snap.now;
    const arrIn = all.filter((a) => a.f.kind === "ARR" && a.where === "AIR");
    const arrLater = all.filter((a) => a.f.kind === "ARR" && a.where === "LATER");
    const arrDone = all.filter((a) => a.f.kind === "ARR" && a.where === "GONE" && a.phase === "DIVERTED");
    const gnd = all.filter((a) => a.where === "GROUND");
    const dep = all.filter((a) => a.f.kind === "DEP" && (a.where === "GROUND" || a.where === "AIR"));
    const depLater = all.filter((a) => a.f.kind === "DEP" && a.where === "LATER");
    const depDone = all.filter((a) => a.f.kind === "DEP" && a.where === "GONE" && a.f.atot != null && a.f.atot > T - RECENT_MIN).reverse();
    return {
      lists: { ARR: [arrIn, arrLater, arrDone], GND: [gnd, [], []], DEP: [dep, depLater, depDone] } as Record<Tab, Ac[][]>,
      count: { ARR: arrIn.length, GND: gnd.length, DEP: dep.length } as Record<Tab, number>,
    };
  }, [traffic, snap.now]);

  const filt = (a: Ac) => !q || a.cs.toLowerCase().includes(q.toLowerCase());
  const [main, later, done] = groups.lists[tab].map((xs) => xs.filter(filt));
  const laterLabel = tab === "ARR" ? "Beyond 40 NM" : "Not yet on stand";
  const strip = (a: Ac, dim = false) => <Strip key={a.cs} ac={a} dim={dim} conflicted={conflicted.has(a.cs)} selected={selected === a.cs} onSelect={onSelect} />;

  return (
    <aside className="panel strips">
      <div className="panel-h">
        <span className="cap">Flight strips</span>
        <span className="spacer" />
        <input className="search" placeholder="Find callsign" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find callsign" />
      </div>
      <div className="strip-tabs" role="tablist">
        {([["ARR", "Arrivals", "On the radar"], ["GND", "On ground", "Drawn on the surface map"], ["DEP", "Departures", "On the field or climbing out"]] as [Tab, string, string][]).map(([id, label, hint]) => (
          <button key={id} role="tab" title={hint} aria-selected={tab === id} className={`strip-tab ${tab === id ? "on" : ""}`} onClick={() => setTab(id)}>
            {label}<span className="n">{groups.count[id]}</span>
          </button>
        ))}
      </div>
      <div className="strip-list">
        {main.map((a) => strip(a))}
        {main.length === 0 && <div className="empty">{tab === "ARR" ? "No arrivals inside 40 NM." : tab === "GND" ? "No aircraft on the airfield." : "No departures on the field."}</div>}
        {later.length > 0 && <div className="strip-div">{laterLabel} · {later.length}</div>}
        {later.map((a) => strip(a))}
        {done.length > 0 && <div className="strip-div">{tab === "ARR" ? "Diverted" : "Departed"} · {done.length}</div>}
        {done.map((a) => strip(a, true))}
      </div>
    </aside>
  );
}

function Strip({ ac, dim, conflicted, selected, onSelect }: { ac: Ac; dim: boolean; conflicted: boolean; selected: boolean; onSelect: (cs: string | null) => void }) {
  const { row, f, phase } = ac;
  const emerg = f.priority === "emergency";
  const hold = phase === "HOLDING" || phase === "WAIT STAND" || !row;
  const cls = ["strip", f.kind === "ARR" ? "arr" : "dep", hold ? "hold" : "", emerg ? "emerg" : "", conflicted ? "conf" : "", selected ? "sel" : "", dim ? "done" : ""].join(" ");
  const late = f.kind === "DEP" && f.tobt && f.tobt > f.est;
  return (
    <div className={cls} onClick={() => onSelect(selected ? null : ac.cs)}>
      <div className="bar" />
      <div className="main">
        <div className="l1">
          <span className="cs">{ac.cs}</span>
          <span className="typ">{f.aircraft}<span className="wtc" title="Wake turbulence category">/{WTC[f.code] ?? "M"}</span></span>
          <span className="od">{f.kind === "ARR" ? `${f.origin_dest}→DXB` : `DXB→${f.origin_dest}`}</span>
        </div>
        <div className="l2">
          {row ? (
            <>
              <span>RWY <b>{row.runway ?? "—"}</b></span>
              <span>STD <b>{row.diverted ? "DIV" : f.act_stand ?? row.stand ?? "—"}</b></span>
              <span>CAT <b>{f.code}</b></span>
              {emerg && <span style={{ color: "var(--crit)" }}>EMERGENCY</span>}
              {f.priority === "high" && <span style={{ color: "var(--warn)" }}>PRIORITY</span>}
              {late && <span style={{ color: "var(--warn)" }} title="Target off-block time revised">TOBT {clock(f.tobt)}</span>}
              {row.ground_wait ? <span style={{ color: "var(--warn)" }} title="Planned ground wait for stand">WAIT {row.ground_wait}′</span> : null}
            </>
          ) : (
            <span style={{ color: emerg ? "var(--crit)" : "var(--warn)" }}>{emerg ? "EMERGENCY · " : ""}AWAITING ALLOCATION{f.stand ? ` · ON ${f.stand}` : ""}</span>
          )}
        </div>
      </div>
      <div className="side">
        <span className="time" title={f.kind === "ARR" ? "Target / actual landing time" : "Target / actual take-off time"}>
          <small>{!row ? (f.kind === "ARR" ? "ETA" : "ETD") : f.kind === "ARR" ? (row.actual ? "ALDT" : "TLDT") : (row.actual ? "ATOT" : "TTOT")}</small>{clock(when(ac))}
        </span>
        {row && (row.added > 0 ? <span className="delay" title="Airport-caused delay vs ETA / ready time">+{row.added}</span> : <span className="ontime">±0</span>)}
        <span className={`phase ${hold ? "hold" : LIVE.includes(phase) ? "live" : ""}`}>{row ? phase : "NO PLAN"}</span>
      </div>
    </div>
  );
}
