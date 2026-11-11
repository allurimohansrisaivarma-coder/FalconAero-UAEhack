import { memo, useMemo, type ReactNode } from "react";
import type { Layout, PlanOption, Snapshot } from "../types";
import { position, type AcPos } from "../sim";

interface Props {
  layout: Layout;
  snap: Snapshot;
  T: number;
  selected: string | null;
  onSelect: (cs: string | null) => void;
  proposal: PlanOption | null;
  toolbar: ReactNode;
}

const SCALE: Record<string, number> = { C: 0.78, D: 0.9, E: 1, F: 1.18 };
const AC_PATH = "M11 0 L7 -1.6 L1.5 -1.6 L-3 -9.5 L-5.5 -9.5 L-2.6 -1.6 L-7.5 -1.4 L-9.6 -4.6 L-11 -4.6 L-10 0 L-11 4.6 L-9.6 4.6 L-7.5 1.4 L-2.6 1.6 L-5.5 9.5 L-3 9.5 L1.5 1.6 L7 1.6 Z";

export function AirfieldMap({ layout, snap, T, selected, onSelect, proposal, toolbar }: Props) {
  const conflictStands = useMemo(() => new Set(snap.conflicts.filter((c) => c.type.startsWith("stand")).map((c) => c.resource)), [snap.conflicts]);
  const conflictRwys = useMemo(() => new Set(snap.conflicts.filter((c) => ["separation", "runway"].includes(c.type)).map((c) => c.resource)), [snap.conflicts]);
  const conflictFlights = useMemo(() => new Set(snap.conflicts.filter((c) => c.type !== "unplanned").flatMap((c) => c.flights)), [snap.conflicts]);

  const positions = useMemo(() => {
    const out: { cs: string; pos: AcPos }[] = [];
    for (const row of Object.values(snap.plan)) {
      const f = snap.flights[row.callsign];
      const pos = position(row, T, layout, f?.aldt, f?.aobt, f?.atot);
      if (pos.visible) out.push({ cs: row.callsign, pos });
    }
    return out;
  }, [snap.plan, snap.flights, T, layout]);

  const parked = useMemo(() => {
    const m = new Set<string>();
    for (const { cs, pos } of positions) {
      const r = snap.plan[cs];
      if (!pos.moving && (pos.phase === "ON BLOCK" || pos.phase === "ON STAND") && r.stand) m.add(r.stand);
    }
    return m;
  }, [positions, snap.plan]);

  const ops = snap.ops;
  const closedStands = snap.world.stands;
  const closedTwy = new Set(Object.keys(snap.world.taxiways));
  const vb = layout.viewBox;
  const hf = layout.holding_fix;
  const holding = positions.filter((p) => p.pos.phase === "HOLDING").map((p) => p.cs);

  return (
    <div className="mapwrap">
      <svg viewBox={vb.join(" ")} preserveAspectRatio="xMidYMid meet" onClick={() => onSelect(null)}>
        <defs>
          <pattern id="hatch-red" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="8" stroke="var(--crit)" strokeWidth="2" opacity="0.55" />
          </pattern>
          <pattern id="hatch-grey" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="#8a6b2b" strokeWidth="1.5" />
          </pattern>
          <radialGradient id="fog" cx="50%" cy="50%" r="75%">
            <stop offset="0%" stopColor="#8a96a3" stopOpacity="0.04" />
            <stop offset="100%" stopColor="#8a96a3" stopOpacity="0.11" />
          </radialGradient>
        </defs>

        <Static layout={layout} />

        {/* Closed taxiways */}
        {layout.taxiways.filter((e) => closedTwy.has(e.name)).map((e, i) => (
          <line key={`ct${i}`} x1={e.a[0]} y1={e.a[1]} x2={e.b[0]} y2={e.b[1]} stroke="var(--crit)" strokeWidth={3} strokeDasharray="6 5" opacity={0.85} />
        ))}

        {/* Runways */}
        {layout.runways.map((r) => {
          const ro = ops.runways[r.id];
          const closed = ro?.mode === "CLOSED";
          const flagged = conflictRwys.has(r.id);
          const above = r.y < 300;
          const label = ro ? (ro.mode === "ARR" ? "ARRIVALS" : ro.mode === "DEP" ? "DEPARTURES" : ro.mode === "MIXED" ? "MIXED MODE" : ro.mode === "STANDBY" ? "STANDBY" : "") : "";
          return (
            <g key={r.id}>
              <rect x={r.x1} y={r.y - 9} width={r.x2 - r.x1} height={18} fill="var(--map-rwy)" stroke={flagged ? "var(--crit)" : "none"} strokeWidth={flagged ? 1.5 : 0} />
              <line x1={r.x1 + 40} x2={r.x2 - 40} y1={r.y} y2={r.y} stroke="#cfd6de" strokeOpacity="0.35" strokeWidth="1.2" strokeDasharray="14 12" />
              {[0, 1].map((side) => (
                <g key={side}>{[-6, -3, 0, 3, 6].map((o) => (
                  <line key={o} x1={side ? r.x2 - 18 : r.x1 + 6} x2={side ? r.x2 - 6 : r.x1 + 18} y1={r.y + o} y2={r.y + o} stroke="#cfd6de" strokeOpacity="0.4" strokeWidth="1" />
                ))}</g>
              ))}
              <text className="rwy-id" x={r.x1 - 8} y={r.y + 4.5} textAnchor="end">{r.id}</text>
              <text className="rwy-id" x={r.x2 + 8} y={r.y + 4.5}>{r.opposite}</text>
              {closed ? (
                <>
                  <rect x={r.x1} y={r.y - 9} width={r.x2 - r.x1} height={18} fill="url(#hatch-red)" />
                  {[0.2, 0.5, 0.8].map((f) => {
                    const cx = r.x1 + (r.x2 - r.x1) * f;
                    return (
                      <g key={f} stroke="var(--crit)" strokeWidth="2.4">
                        <line x1={cx - 10} y1={r.y - 7} x2={cx + 10} y2={r.y + 7} /><line x1={cx - 10} y1={r.y + 7} x2={cx + 10} y2={r.y - 7} />
                      </g>
                    );
                  })}
                  <text x={(r.x1 + r.x2) / 2} y={r.y + (above ? -16 : 28)} textAnchor="middle" style={{ font: "600 11px var(--mono)", fill: "var(--crit)", letterSpacing: "0.12em" }}>
                    RUNWAY CLOSED{ro?.reasons[0] ? ` · ${ro.reasons[0].toUpperCase().slice(0, 40)}` : ""}
                  </text>
                </>
              ) : (
                <text x={r.x1 + 26} y={r.y + (above ? -15 : 25)} style={{ font: "600 9.5px var(--mono)", fill: ro?.mode === "MIXED" || !ro?.arr_ok ? "var(--warn)" : "#6b7683", letterSpacing: "0.1em" }}>
                  {label}{ro && ro.reasons.length ? ` · ${ro.reasons.join(" · ").toUpperCase().slice(0, 60)}` : ""}
                  {ro?.direction && ro.direction !== r.id ? ` · RWY ${ro.direction} IN USE` : ""}
                </text>
              )}
            </g>
          );
        })}

        {/* Stands */}
        {layout.stands.map((s) => {
          const flagged = conflictStands.has(s.id);
          const closed = closedStands[s.id];
          const sel = selected && snap.plan[selected]?.stand === s.id;
          const above = s.lane_y < s.y;
          return (
            <g key={s.id}>
              <title>{`Stand ${s.id} · max Code ${s.max_code}${s.contact ? " · contact" : " · remote"}${closed ? ` · UNAVAILABLE: ${closed.reason}` : ""}`}</title>
              <line x1={s.x} x2={s.x} y1={s.lane_y} y2={s.y} stroke="#323b46" strokeWidth="1" />
              <rect x={s.x - 15} y={s.y - 10} width={30} height={20} rx={1.5} fill={parked.has(s.id) ? "#1d242d" : "transparent"}
                stroke={flagged ? "var(--crit)" : closed ? "var(--warn)" : sel ? "var(--sel)" : "#3a4450"}
                strokeDasharray={s.contact ? undefined : "3 2"} strokeWidth={flagged || sel || closed ? 1.5 : 1} />
              {closed && <rect x={s.x - 15} y={s.y - 10} width={30} height={20} fill="url(#hatch-grey)" />}
              <text className="stand-id" x={s.x} y={above ? s.y + 20 : s.y - 14} textAnchor="middle">{s.id}<tspan fill="#4b5560"> {s.max_code}</tspan></text>
            </g>
          );
        })}

        {(ops.lvp || ops.weather.vis_m < 1500) && <rect x={vb[0]} y={vb[1]} width={vb[2]} height={vb[3]} fill="url(#fog)" pointerEvents="none" />}

        {proposal && <Ghosts layout={layout} snap={snap} proposal={proposal} T={T} />}

        {/* Holding fix + stack */}
        <g opacity="0.85">
          <ellipse cx={hf.x} cy={hf.y} rx={22} ry={12} fill="none" stroke="#3c4652" strokeDasharray="3 3" />
          <text x={hf.x} y={hf.y + 28} textAnchor="middle" style={{ font: "600 9px var(--mono)", fill: "#56616d" }}>{hf.name}</text>
        </g>
        {holding.length > 0 && (
          <g>
            <text x={hf.x} y={hf.y - 28} textAnchor="middle" style={{ font: "600 11px var(--mono)", fill: "var(--warn)" }}>HOLD ×{holding.length}</text>
            <text x={hf.x} y={hf.y - 17} textAnchor="middle" style={{ font: "500 8.5px var(--mono)", fill: "#9aa5b2" }}>
              {holding.slice(0, 3).join(" ")}{holding.length > 3 ? " …" : ""}
            </text>
          </g>
        )}

        {/* Aircraft */}
        {positions.map(({ cs, pos }) => {
          const row = snap.plan[cs];
          const isSel = selected === cs;
          const isParked = !pos.moving && (pos.phase === "ON BLOCK" || pos.phase === "ON STAND");
          const color = row.priority === "emergency" ? "var(--crit)" : pos.phase === "HOLDING" || pos.phase === "WAIT STAND" ? "var(--warn)"
            : conflictFlights.has(cs) ? "#ff8a80" : isParked ? "#8d98a5" : "#e8edf2";
          const sc = SCALE[row.code] ?? 1;
          const showLabel = isSel || (!isParked && pos.phase !== "HOLDING");
          return (
            <g key={cs} onClick={(e) => { e.stopPropagation(); onSelect(isSel ? null : cs); }} style={{ cursor: "pointer" }}>
              <g transform={`translate(${pos.x} ${pos.y}) rotate(${pos.hdg}) scale(${sc * (isParked ? 0.8 : 1)})`}>
                <path d={AC_PATH} fill={color} stroke="#0b0e12" strokeWidth="0.8" />
              </g>
              {isSel && <circle cx={pos.x} cy={pos.y} r={15} fill="none" stroke="var(--sel)" strokeWidth="1.2" />}
              {showLabel && (
                <g>
                  <line x1={pos.x + 6} y1={pos.y - 6} x2={pos.x + 14} y2={pos.y - 14} stroke="#7d8894" strokeWidth="0.8" />
                  <text className="ac-label" x={pos.x + 16} y={pos.y - 24}>
                    <tspan fontWeight={600} fill={row.priority === "emergency" ? "var(--crit)" : undefined}>{cs}</tspan>
                    <tspan className="sub" x={pos.x + 16} dy={11}>
                      {row.type} {row.kind === "ARR" ? `→${row.stand ?? "--"}` : row.runway}{row.added > 0 ? ` +${row.added}` : ""}
                    </tspan>
                  </text>
                </g>
              )}
            </g>
          );
        })}
      </svg>
      <div className="map-legend">
        <span>{layout.icao} SURFACE · SCHEMATIC</span>
        <span><i style={{ background: "#e8edf2" }} />MOVING</span>
        <span><i style={{ background: "#8d98a5" }} />PARKED</span>
        <span><i style={{ background: "var(--warn)" }} />HOLD / WAIT</span>
        <span><i style={{ background: "var(--crit)" }} />CONFLICT / EMERG</span>
        {proposal && <span><i style={{ background: "var(--ai)" }} />PROPOSED</span>}
      </div>
      <div className="map-tools">{toolbar}</div>
      <MapStatus snap={snap} />
    </div>
  );
}

const TAKEOFF_MIN_RVR = 125;

/** Restrictions in force, shown on both map views. */
export function MapStatus({ snap }: { snap: Snapshot }) {
  const ops = snap.ops;
  const closedTwy = Object.keys(snap.world.taxiways);
  const rvr = ops.weather.rvr_m ?? ops.weather.vis_m;
  return (
    <div className="map-status">
      {ops.lvp && (rvr >= TAKEOFF_MIN_RVR
        ? <span className="tag warn" title="Low-visibility take-offs are permitted down to 125 m RVR, at the reduced LVP rate">LVP · TAKE-OFF PERMITTED · RVR {rvr} M ≥ {TAKEOFF_MIN_RVR} M</span>
        : <span className="tag crit">RVR {rvr} M BELOW TAKE-OFF MINIMA · NO DEPARTURES</span>)}
      {!ops.ramp_open && <span className="tag crit">LIGHTNING RED · RAMP SUSPENDED</span>}
      {ops.lightning === "AMBER" && <span className="tag warn">LIGHTNING AMBER</span>}
      {ops.dep_stopped && <span className="tag crit">GROUND STOP{ops.arr_stopped ? " · ALL MOVEMENTS" : " · DEPARTURES"}</span>}
      {Object.entries(ops.flow).map(([k, v]) => <span key={k} className="tag warn">ATFM {k} {v}/H</span>)}
      {ops.closed_regions.length > 0 && <span className="tag crit">AIRSPACE CLOSED · {ops.closed_regions.join(", ")}</span>}
      {closedTwy.length > 0 && <span className="tag warn">TWY {closedTwy.join(", ")} CLOSED</span>}
    </div>
  );
}

const Static = memo(function Static({ layout }: { layout: Layout }) {
  const a = layout.apron;
  return (
    <g>
      <rect x={a.x} y={a.y} width={a.w} height={a.h} fill="var(--map-apron)" />
      {layout.taxiways.map((e, i) => (
        <line key={i} x1={e.a[0]} y1={e.a[1]} x2={e.b[0]} y2={e.b[1]} stroke="var(--map-twy)" strokeWidth={e.name === "X" ? 6 : 7} strokeLinecap="round" />
      ))}
      {layout.taxiways.map((e, i) => (
        <line key={`c${i}`} x1={e.a[0]} y1={e.a[1]} x2={e.b[0]} y2={e.b[1]} stroke="#5d5531" strokeOpacity="0.55" strokeWidth={0.8} />
      ))}
      {layout.buildings.filter((b) => b.h > 0).map((b) => (
        <g key={b.id}>
          <rect x={b.x} y={b.y} width={b.w} height={b.h} fill="var(--map-bldg)" stroke="#262e38" />
          <text className="bldg-id" x={b.x + b.w / 2} y={b.y + b.h / 2 + 3} textAnchor="middle">{b.label}</text>
        </g>
      ))}
      {layout.buildings.filter((b) => b.h === 0).map((b) => (
        <text key={b.id} className="bldg-id" x={b.x + b.w / 2} y={b.y + 7} textAnchor="middle">{b.label}</text>
      ))}
      {layout.taxiway_labels.map((l) => (
        <g key={l.text}>
          <rect x={l.x - 9} y={l.y - 7} width={18} height={14} fill="#11151a" stroke="#3b4450" rx={1} />
          <text className="twy-id" x={l.x} y={l.y + 3.2} textAnchor="middle">{l.text}</text>
        </g>
      ))}
    </g>
  );
});

function Ghosts({ layout, snap, proposal, T }: { layout: Layout; snap: Snapshot; proposal: PlanOption; T: number }) {
  const standById = new Map(layout.stands.map((s) => [s.id, s]));
  return (
    <g pointerEvents="none">
      {proposal.changes.map((c) => {
        const sc = c.changes.find((x) => x.field === "stand" || x.field === "new");
        if (!sc || !c.stand) return null;
        const to = standById.get(c.stand);
        if (!to) return null;
        const cur = snap.plan[c.callsign];
        const f = snap.flights[c.callsign];
        const from = cur ? position(cur, T, layout, f?.aldt, f?.aobt, f?.atot) : null;
        const fromStand = cur?.stand ? standById.get(cur.stand) : null;
        const fx = from?.visible ? from.x : fromStand?.x;
        const fy = from?.visible ? from.y : fromStand?.y;
        return (
          <g key={c.callsign}>
            <rect x={to.x - 17} y={to.y - 12} width={34} height={24} fill="var(--ai-dim)" stroke="var(--ai)" strokeWidth="1.4" rx={2} />
            {fx !== undefined && fy !== undefined && <line x1={fx} y1={fy} x2={to.x} y2={to.y} stroke="var(--ai)" strokeWidth="1.2" strokeDasharray="5 4" opacity="0.85" />}
            <text x={to.x} y={to.lane_y < to.y ? to.y + 32 : to.y - 26} textAnchor="middle" style={{ font: "600 9.5px var(--mono)", fill: "var(--ai)" }}>{c.callsign}</text>
          </g>
        );
      })}
    </g>
  );
}
