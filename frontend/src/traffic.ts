/**
 * One picture of the traffic for the whole screen. The flight strips, the surface map and the airspace
 * map all read from here, so an aircraft is in the same place, phase and list everywhere.
 */
import type { Flight, Layout, PlanRow, Snapshot } from "./types";
import { atStand, holdPos, position, type AcPos, type Phase } from "./sim";
import { holdTrack, track, type AirPhase, type RunwayGeo, type Track } from "./airspace";

/** AIR: inside the 40 NM area · GROUND: on the airfield · LATER: not yet in sight · GONE: finished or diverted. */
export type Where = "AIR" | "GROUND" | "LATER" | "GONE";

export interface Ac {
  cs: string;
  f: Flight;
  /** The approved plan for this flight, or null while it waits for an allocation. */
  row: PlanRow | null;
  /** Position on the surface schematic, or null when it is not drawn there. */
  surf: AcPos | null;
  /** Track in the terminal area, or null when it is not drawn there. */
  air: Track | null;
  phase: Phase;
  where: Where;
}

const AIR_PHASE: Record<AirPhase, Phase> = { INBOUND: "INBOUND", HOLD: "HOLDING", APPROACH: "APPROACH", FINAL: "FINAL", CLIMB: "AIRBORNE" };
const ON_FIELD: Phase[] = ["LANDING", "TAXI IN", "WAIT STAND", "ON BLOCK", "ON STAND", "TAXI OUT", "LINE UP"];

export function buildTraffic(snap: Snapshot, layout: Layout, T: number, geos: RunwayGeo[]): Map<string, Ac> {
  const out = new Map<string, Ac>();
  const rows = Object.values(snap.plan);
  // Arrivals past their planned landing time that have not landed, in landing order per runway
  const late = rows.filter((r) => r.kind === "ARR" && r.runway && !r.diverted && r.t < T && snap.flights[r.callsign]?.aldt == null).sort((a, b) => a.t - b.t);
  const queue = new Map(late.map((r) => [r.callsign, late.filter((x) => x.runway === r.runway).indexOf(r)]));

  for (const f of Object.values(snap.flights)) {
    const row = snap.plan[f.callsign] ?? null;
    if (!row) {
      out.set(f.callsign, unplanned(f, layout, T));
      continue;
    }
    const air = track(row, f, T, layout, snap.ops, geos, queue.get(row.callsign) ?? 0);
    let surf: AcPos | null = position(row, T, layout, f.aldt, f.aobt, f.atot, f.act_stand);
    // The radar decides who is holding; the surface map shows the same aircraft at its holding fix
    if (air?.phase === "HOLD") surf = holdPos(row, T, layout.holding_fix);
    else if (surf.phase === "HOLDING") surf = { ...surf, visible: false };
    if (!surf.visible) surf = null;

    let phase: Phase;
    let where: Where;
    if (surf && ON_FIELD.includes(surf.phase)) { phase = surf.phase; where = "GROUND"; }
    else if (air) { phase = AIR_PHASE[air.phase]; where = "AIR"; }
    else if (surf) { phase = surf.phase; where = "AIR"; }                       // on short final, drawn on the surface only
    else {
      phase = position(row, T, layout, f.aldt, f.aobt, f.atot).phase;
      const over = ["DEPARTED", "AIRBORNE", "DIVERTED", "CANCELLED", "TURNED"].includes(phase) || !f.active;
      where = over ? "GONE" : "LATER";
    }
    out.set(f.callsign, { cs: f.callsign, f, row, surf, air, phase, where });
  }
  return out;
}

/** A flight the approved plan does not cover yet. A departure is already parked on its stand;
 *  an arrival that is due circles at its entry hold until it gets a slot. */
function unplanned(f: Flight, layout: Layout, T: number): Ac {
  const base = { cs: f.callsign, f, row: null };
  if (!f.active) return { ...base, surf: null, air: null, phase: f.status === "CANCELLED" ? "CANCELLED" : "DEPARTED", where: "GONE" };
  if (f.kind === "DEP") {
    const surf = f.aobt == null && f.stand ? atStand(layout, f.stand) : null;
    return { ...base, surf: surf?.visible ? surf : null, air: null, phase: "ON STAND", where: surf?.visible ? "GROUND" : "LATER" };
  }
  const due = Math.max(f.est, f.sched);
  const air = holdTrack(f.callsign, f.region, due, T);
  return { ...base, surf: air ? holdPos(f, T, layout.holding_fix) : null, air, phase: air ? "HOLDING" : "INBOUND", where: air ? "AIR" : "LATER" };
}
