import { memo, useMemo, type ReactNode } from "react";
import type { Layout, Ops, PlanOption, Snapshot } from "../types";
import { clock } from "../sim";
import {
  NEIGHBOURS, RANGE_NM, REGIONS, arrivalRoute, departureRoute, fixes, headingInUse, path, pol, track,
  type P, type RunwayGeo, type Track,
} from "../airspace";
import { MapStatus } from "./AirfieldMap";
import type { Ac } from "../traffic";

interface Props {
  layout: Layout;
  snap: Snapshot;
  traffic: Map<string, Ac>;
  geos: RunwayGeo[];
  T: number;
  selected: string | null;
  onSelect: (cs: string | null) => void;
  proposal: PlanOption | null;
  toolbar: ReactNode;
}

const VIEW = [-68, -47, 136, 94];
const LABEL_W = 9.6, LABEL_H = 4;
/** Leader offsets tried in turn so data blocks do not sit on top of each other. */
const SLOTS: P[] = [[2.4, -4.4], [2.4, 1.2], [-LABEL_W - 2.4, -4.4], [-LABEL_W - 2.4, 1.2], [2.4, -8.8], [2.4, 5.6],
  [-LABEL_W - 2.4, -8.8], [-LABEL_W - 2.4, 5.6], [6, -13.2], [-LABEL_W - 6, 10], [6, 10], [-LABEL_W - 6, -13.2]];

const fl = (ft: number) => String(Math.round(ft / 100)).padStart(3, "0");
const TREND = { [-1]: "↓", 0: "=", 1: "↑" } as const;

export function AirspaceMap({ layout, snap, traffic, geos, T, selected, onSelect, proposal, toolbar }: Props) {
  const ops = snap.ops;
  const conflicted = useMemo(() => new Set(snap.conflicts.filter((c) => c.type !== "unplanned" && c.severity === "critical").flatMap((c) => c.flights)), [snap.conflicts]);

  const tracks = useMemo(() => {
    const out: (Track & { trail: P[]; ac: Ac })[] = [];
    for (const ac of traffic.values()) {
      if (!ac.air) continue;
      const trail: P[] = [];
      // No trail for overdue arrivals strung out on final: their past positions are not on this track
      const overdue = ac.row?.kind === "ARR" && ac.row.t < T;
      if (ac.row && !overdue) for (let k = 1; k <= 5; k++) { const h = track(ac.row, ac.f, T - k * 0.45, layout, ops, geos); if (h) trail.push(h.p); }
      out.push({ ...ac.air, trail, ac });
    }
    return out;
  }, [traffic, T, layout, ops, geos]);

  // Landing sequence, as an arrival manager would number it; arrivals with no slot get no number
  const seq = useMemo(() => {
    const m = new Map<string, number>();
    tracks.filter((t) => t.kind === "ARR" && t.ac.row?.runway).sort((a, b) => a.ac.row!.t - b.ac.row!.t).forEach((t, i) => m.set(t.cs, i + 1));
    return m;
  }, [tracks]);

  // Greedy label placement: nearest-to-landing first, each block takes the first free slot
  const labels = useMemo(() => {
    const placed: { x: number; y: number }[] = [];
    const out = new Map<string, P>();
    const order = [...tracks].sort((a, b) => Number(b.cs === selected) - Number(a.cs === selected) || a.dtg - b.dtg);
    for (const t of order) {
      let pick = SLOTS[0];
      for (const s of SLOTS) {
        const x = t.p[0] + s[0], y = t.p[1] + s[1];
        if (!placed.some((q) => Math.abs(q.x - x) < LABEL_W && Math.abs(q.y - y) < LABEL_H)) { pick = s; break; }
      }
      placed.push({ x: t.p[0] + pick[0], y: t.p[1] + pick[1] });
      out.set(t.cs, pick);
    }
    return out;
  }, [tracks, selected]);

  const ghosts = useMemo(() => {
    if (!proposal) return [];
    return proposal.changes.filter((c) => c.changes.some((m) => m.field === "time" || m.field === "runway" || m.field === "new"))
      .map((c) => ({ cs: c.callsign, row: proposal.plan[c.callsign] }))
      .filter((g) => g.row)
      .map((g) => ({ ...g, t: track(g.row, snap.flights[g.cs], T, layout, ops, geos) }))
      .filter((g): g is typeof g & { t: Track } => g.t !== null);
  }, [proposal, snap.flights, T, layout, ops, geos]);

  const holding = (region: string) => tracks.filter((t) => t.phase === "HOLD" && (t.ac.f.region ?? "EAST") === region).length;
  const fog = ops.lvp || ops.weather.vis_m < 1500;
  const closedRegions = ops.closed_regions.includes("ALL") ? [...REGIONS] : REGIONS.filter((r) => ops.closed_regions.includes(r));

  return (
    <div className="mapwrap radar">
      <svg viewBox={VIEW.join(" ")} preserveAspectRatio="xMidYMid meet" onClick={() => onSelect(null)}>
        <defs>
          <pattern id="tma-closed" width="1.6" height="1.6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="1.6" stroke="var(--crit)" strokeWidth="0.35" opacity="0.5" />
          </pattern>
          <radialGradient id="tma-fog"><stop offset="0%" stopColor="#aab4c0" stopOpacity="0.1" /><stop offset="100%" stopColor="#aab4c0" stopOpacity="0" /></radialGradient>
        </defs>

        <Chart />
        <Procedures layout={layout} ops={ops} geos={geos} />

        {closedRegions.map((r) => {
          const e = fixes.entries.find((x) => x.region === r)!;
          const a = e.brg - 45, b = e.brg + 45;
          const arc = (rad: number, from: number, to: number) => Array.from({ length: 13 }, (_, i) => pol(from + ((to - from) * i) / 12, rad));
          const pts = [...arc(27, a, b), ...arc(60, b, a)];
          const lab = pol(e.brg, 34);
          return (
            <g key={r} pointerEvents="none">
              <path d={`${path(pts)} Z`} fill="url(#tma-closed)" stroke="var(--crit)" strokeWidth="0.18" strokeOpacity="0.7" />
              <text className="tma-warn" x={lab[0]} y={lab[1]} textAnchor="middle">AIRSPACE CLOSED · {r}</text>
            </g>
          );
        })}

        {fixes.entries.map((e) => {
          const n = holding(e.region);
          return n > 0 ? <text key={e.region} className="tma-hold-n" x={e.hold[0]} y={e.hold[1] - 4.4} textAnchor="middle">HOLDING ×{n}</text> : null;
        })}

        {fog && <circle r={9} fill="url(#tma-fog)" pointerEvents="none" />}

        {/* Proposed positions and routes (not yet approved) */}
        {ghosts.map(({ cs, t }) => (
          <g key={`g${cs}`} pointerEvents="none">
            <path d={path(t.path)} fill="none" stroke="var(--ai)" strokeWidth="0.22" strokeDasharray="0.9 0.7" opacity="0.8" />
            <circle cx={t.p[0]} cy={t.p[1]} r={0.95} fill="none" stroke="var(--ai)" strokeWidth="0.22" />
          </g>
        ))}

        {tracks.map((t) => {
          const { row, f } = t.ac;
          const added = row?.added ?? 0;
          const sel = selected === t.cs;
          const emerg = f.priority === "emergency";
          const c = colour(t, emerg);
          const [dx, dy] = labels.get(t.cs) ?? SLOTS[0];
          const lx = t.p[0] + dx, ly = t.p[1] + dy;
          const vec = pol(t.trk, t.gs / 60, t.p);                     // where it will be in one minute
          const anchor: P = [dx > 0 ? lx - 0.3 : lx + LABEL_W - 1.6, ly + 1.6];
          const ghost = ghosts.find((g) => g.cs === t.cs);
          return (
            <g key={t.cs} className={`tma-ac ${sel ? "sel" : ""}`} onClick={(e) => { e.stopPropagation(); onSelect(sel ? null : t.cs); }}>
              <title>{`${t.cs} ${f.aircraft} · ${!row?.runway ? "awaiting a landing slot" : t.kind === "ARR" ? `landing ${row.runway} ${clock(row.t)}Z, ${t.dtg.toFixed(0)} NM to run` : `departed ${row.runway} to ${row.origin_dest}`} · ${t.phase}${added > 0 ? ` · +${added} min` : ""}`}</title>
              {/* Route still to fly, in the colour of the movement: quiet until the aircraft is hovered or selected */}
              <path className="tma-path" d={path(t.path)} fill="none" stroke={sel ? "var(--sel)" : c} strokeLinejoin="round" pointerEvents="none" />
              {t.trail.map((q, i) => <circle key={i} cx={q[0]} cy={q[1]} r={0.22} fill={c} opacity={0.55 - i * 0.09} />)}
              <line x1={t.p[0]} y1={t.p[1]} x2={vec[0]} y2={vec[1]} stroke={c} strokeWidth="0.18" />
              <line x1={t.p[0]} y1={t.p[1]} x2={anchor[0]} y2={anchor[1]} stroke="#5d6875" strokeWidth="0.1" />
              {t.kind === "ARR"
                ? <circle cx={t.p[0]} cy={t.p[1]} r={0.62} fill="var(--map-ground)" stroke={c} strokeWidth="0.3" />
                : <rect x={t.p[0] - 0.55} y={t.p[1] - 0.55} width={1.1} height={1.1} transform={`rotate(45 ${t.p[0]} ${t.p[1]})`} fill="var(--map-ground)" stroke={c} strokeWidth="0.3" />}
              {(sel || conflicted.has(t.cs)) && <circle cx={t.p[0]} cy={t.p[1]} r={1.5} fill="none" stroke={sel ? "var(--sel)" : "var(--crit)"} strokeWidth="0.18" />}
              <text className={`tma-label ${sel ? "sel" : ""}`} x={lx} y={ly + 1.25}>
                <tspan className="cs" fill={emerg ? "var(--crit)" : undefined}>{t.cs}</tspan>
                {t.kind === "ARR" && seq.has(t.cs) && <tspan className="dim" dx={0.7}>#{seq.get(t.cs)}</tspan>}
                <tspan x={lx} dy={1.7}>{fl(t.alt)}{TREND[t.vs]} {Math.round(t.gs / 10)}</tspan>
                {t.phase === "HOLD" && <tspan className="warn" dx={0.6}>HOLD</tspan>}
                {added > 0 && <tspan className="warn" dx={0.6}>+{added}</tspan>}
                {ghost && <tspan className="ai" dx={0.6}>→{clock(ghost.row.t)}</tspan>}
                {/* Third line only for the aircraft under the pointer or selected */}
                <tspan x={lx} dy={1.7} className="dim more">
                  {f.aircraft} {!row?.runway ? "NO SLOT" : `${ops.runways[row.runway]?.direction || row.runway}${t.kind === "ARR" ? ` ${clock(row.t)}` : ` ${row.origin_dest}`}`}
                </tspan>
              </text>
            </g>
          );
        })}
      </svg>

      <div className="map-legend">
        <span>{layout.icao} TMA · {RANGE_NM} NM · SCHEMATIC, NOT FOR NAVIGATION</span>
        <span><svg width="18" height="8"><line x1="0" y1="4" x2="18" y2="4" stroke="var(--arr)" strokeWidth="2" /><circle cx="9" cy="4" r="3" fill="var(--map-ground)" stroke="var(--arr)" strokeWidth="1.5" /></svg>ARRIVAL</span>
        <span><svg width="18" height="8"><line x1="0" y1="4" x2="18" y2="4" stroke="var(--dep)" strokeWidth="2" /><rect x="6.5" y="1.5" width="5" height="5" transform="rotate(45 9 4)" fill="var(--map-ground)" stroke="var(--dep)" strokeWidth="1.5" /></svg>DEPARTURE</span>
        <span><i style={{ background: "var(--warn)" }} />HOLDING</span>
        <span><i style={{ background: "var(--crit)" }} />EMERGENCY / CONFLICT</span>
        {proposal && <span><i style={{ background: "var(--ai)" }} />PROPOSED</span>}
      </div>
      <div className="map-tools">{toolbar}</div>
      <MapStatus snap={snap} />
      <div className="map-count mono">
        {tracks.filter((t) => t.kind === "ARR").length} ARR · {tracks.filter((t) => t.phase === "HOLD").length} HOLD · {tracks.filter((t) => t.kind === "DEP").length} DEP IN AREA
      </div>
    </div>
  );
}

function colour(t: Track, emergency: boolean): string {
  if (emergency) return "var(--crit)";
  if (t.phase === "HOLD") return "var(--warn)";
  return t.kind === "ARR" ? "var(--arr)" : "var(--dep)";
}

/** Range rings, compass rose, coastline and neighbouring aerodromes: nothing here changes with traffic. */
const Chart = memo(function Chart() {
  return (
    <g pointerEvents="none">
      {[10, 20, 30, 40].map((r) => {
        const lab = pol(128, r);
        return (
          <g key={r}>
            <circle r={r} fill="none" stroke={r === RANGE_NM ? "#33404f" : "#222c38"} strokeWidth="0.12" />
            <text className="tma-ring" x={lab[0] + 0.5} y={lab[1] + 1.4}>{r}</text>
          </g>
        );
      })}
      {Array.from({ length: 36 }, (_, i) => i * 10).map((b) => {
        const major = b % 30 === 0;
        const a = pol(b, RANGE_NM), c = pol(b, RANGE_NM + (major ? 1.3 : 0.6)), l = pol(b, RANGE_NM + 3);
        return (
          <g key={b}>
            <line x1={a[0]} y1={a[1]} x2={c[0]} y2={c[1]} stroke="#2b3643" strokeWidth="0.12" />
            {major && <text className="tma-ring" x={l[0]} y={l[1] + 0.5} textAnchor="middle">{String(b || 360).padStart(3, "0")}</text>}
          </g>
        );
      })}
      {NEIGHBOURS.map((n) => (
        <g key={n.icao}>
          <circle cx={n.p[0]} cy={n.p[1]} r={0.7} fill="none" stroke="#4d5a69" strokeWidth="0.14" />
          <line x1={n.p[0] - 1.1} y1={n.p[1]} x2={n.p[0] + 1.1} y2={n.p[1]} stroke="#4d5a69" strokeWidth="0.14" />
          <text className="tma-fix" x={n.p[0] + 1.5} y={n.p[1] + 0.5}>{n.icao}</text>
        </g>
      ))}
    </g>
  );
});

/** Runways, extended centrelines, and the arrival and departure routes for the runways in use. */
const Procedures = memo(function Procedures({ layout, ops, geos }: { layout: Layout; ops: Ops; geos: RunwayGeo[] }) {
  const usable = (g: RunwayGeo, kind: "ARR" | "DEP") => { const m = ops.runways[g.id]?.mode; return m === kind || m === "MIXED"; };
  return (
    <g pointerEvents="none">
      {geos.filter((g) => usable(g, "ARR")).map((g) => {
        const hdg = headingInUse(layout, ops, g.id);
        const thr = pol(hdg + 180, 1.1, g.mid);
        const far = pol(hdg + 180, 16, thr);
        return (
          <g key={`a${g.id}`}>
            {REGIONS.map((r) => <path key={r} d={path(arrivalRoute(g, hdg, r).pts)} fill="none" stroke="var(--arr)" strokeWidth="0.14" strokeOpacity="0.28" strokeLinejoin="round" />)}
            <line x1={thr[0]} y1={thr[1]} x2={far[0]} y2={far[1]} stroke="#3b4856" strokeWidth="0.14" />
            {[2, 4, 6, 8, 10, 12, 14].map((d) => {
              const c = pol(hdg + 180, d, thr), a = pol(hdg + 90, d % 4 ? 0.35 : 0.6, c), b = pol(hdg - 90, d % 4 ? 0.35 : 0.6, c);
              return <line key={d} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke="#3b4856" strokeWidth="0.14" />;
            })}
          </g>
        );
      })}
      {geos.filter((g) => usable(g, "DEP")).map((g) => {
        const hdg = headingInUse(layout, ops, g.id);
        return <g key={`d${g.id}`}>{REGIONS.map((r) => <path key={r} d={path(departureRoute(g, hdg, r).pts)} fill="none" stroke="var(--dep)" strokeWidth="0.14" strokeOpacity="0.26" strokeLinejoin="round" />)}</g>;
      })}

      {fixes.entries.map((e) => {
        return (
          <g key={e.name}>
            {/* Racetrack: 4 NM legs out along the entry radial, turns of 1.3 NM radius, drawn pointing north then rotated */}
            <rect x={-2.6} y={-5.3} width={2.6} height={6.6} rx={1.3} transform={`translate(${e.hold[0]} ${e.hold[1]}) rotate(${e.brg})`}
              fill="none" stroke="#44505e" strokeWidth="0.12" strokeDasharray="0.6 0.5" />
            <text className="tma-fix" x={e.hold[0] + 1.2} y={e.hold[1] + 2.6}>{e.holdName}</text>
            <path d={`M${e.p[0]} ${e.p[1] - 0.8} L${e.p[0] + 0.7} ${e.p[1] + 0.5} L${e.p[0] - 0.7} ${e.p[1] + 0.5} Z`} fill="none" stroke="var(--arr)" strokeWidth="0.16" />
            <text className="tma-fix" x={e.p[0] + 1.2} y={e.p[1] - 0.6}>{e.name}</text>
          </g>
        );
      })}
      {fixes.exits.map((e) => (
        <g key={e.name}>
          <path d={`M${e.p[0]} ${e.p[1] - 0.8} L${e.p[0] + 0.7} ${e.p[1] + 0.5} L${e.p[0] - 0.7} ${e.p[1] + 0.5} Z`} fill="none" stroke="var(--dep)" strokeWidth="0.16" />
          <text className="tma-fix" x={e.p[0] + 1.2} y={e.p[1] - 0.6}>{e.name}</text>
        </g>
      ))}

      {geos.map((g) => {
        const ro = ops.runways[g.id];
        const closed = ro?.mode === "CLOSED";
        const hdg = layout.runways.find((r) => r.id === g.id)!.heading;
        const out = hdg + (g.id.endsWith("L") ? -90 : 90);          // label on the outer side of each runway
        const inUse = ro?.direction === g.opposite ? g.opposite : g.id;
        // One label per runway, beside the threshold of the direction in use
        const lab = pol(out, 1.5, inUse === g.id ? g.a : g.b);
        return (
          <g key={g.id}>
            <line x1={g.a[0]} y1={g.a[1]} x2={g.b[0]} y2={g.b[1]} stroke={closed ? "var(--crit)" : "#c9d1d9"} strokeWidth="0.42" strokeLinecap="butt" />
            <text className="tma-rwy" x={lab[0]} y={lab[1] + 0.5} textAnchor="middle" fill={closed ? "var(--crit)" : undefined}>{inUse}</text>
          </g>
        );
      })}
    </g>
  );
});
