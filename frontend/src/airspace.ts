/**
 * Schematic terminal airspace around the airport, in nautical miles from the aerodrome (north up).
 *
 * The engine plans runway times; it does not model airspace. This module projects those times onto a
 * plausible approach and departure pattern so the radar view shows where each aircraft would be.
 * Fix names and routes are illustrative, not published procedures.
 */
import type { Flight, Layout, Ops, PlanRow } from "./types";

export type P = [number, number];
export const RANGE_NM = 40;
export const REGIONS = ["NORTH", "EAST", "SOUTH", "WEST"] as const;
export type Region = (typeof REGIONS)[number];

/** Bearing (degrees true) and distance (NM) from the aerodrome → map point. North is up, so y is negative north. */
export const pol = (brg: number, d: number, from: P = [0, 0]): P => {
  const a = (brg * Math.PI) / 180;
  return [from[0] + d * Math.sin(a), from[1] - d * Math.cos(a)];
};

const ENTRY: Record<Region, { brg: number; fix: string; hold: string }> = {
  NORTH: { brg: 345, fix: "NORLA", hold: "HOLD N" },
  EAST: { brg: 75, fix: "ESTAM", hold: "HOLD E" },
  SOUTH: { brg: 165, fix: "SUDAR", hold: "HOLD S" },
  WEST: { brg: 255, fix: "WESPA", hold: "HOLD W" },
};
const EXIT: Record<Region, { brg: number; fix: string }> = {
  NORTH: { brg: 15, fix: "DENOR" },
  EAST: { brg: 105, fix: "DEAST" },
  SOUTH: { brg: 195, fix: "DESUD" },
  WEST: { brg: 285, fix: "DEWES" },
};
const HOLD_NM = 26;        // holding fixes sit on the entry radial at this range
const FINAL_NM = 12;       // routes join the extended centreline here
const RWY_HALF = 1.1;      // half the drawn runway length
const RWY_OFFSET = 0.45;   // parallel runways drawn this far either side of the centreline (exaggerated to stay legible)

export const regionOf = (r: string | null | undefined): Region => (REGIONS as readonly string[]).includes(r ?? "") ? (r as Region) : "EAST";

export interface RunwayGeo { id: string; opposite: string; a: P; b: P; mid: P }

/** Runways as map segments; `a` is the threshold of the lower-numbered end (e.g. 12), `b` of the opposite end (30). */
export function runwayGeo(layout: Layout): RunwayGeo[] {
  // The surface schematic lists runways top to bottom; the first is the right-hand runway of the pair.
  return layout.runways.map((r) => {
    const side = r.id.endsWith("L") ? -90 : 90;
    const mid = pol(r.heading + side, RWY_OFFSET);
    return { id: r.id, opposite: r.opposite, mid, a: pol(r.heading + 180, RWY_HALF, mid), b: pol(r.heading, RWY_HALF, mid) };
  });
}

/** Heading of the runway direction in use (wind decides which end). */
export function headingInUse(layout: Layout, ops: Ops, runwayId: string): number {
  const r = layout.runways.find((x) => x.id === runwayId);
  if (!r) return 120;
  return ops.runways[runwayId]?.direction === r.opposite ? (r.heading + 180) % 360 : r.heading;
}

const dist = (a: P, b: P) => Math.hypot(b[0] - a[0], b[1] - a[1]);
const angDiff = (a: number, b: number) => ((a - b + 540) % 360) - 180;

export interface Route { pts: P[]; len: number; holdAt: number; hold: P; holdBrg: number }

/** Arrival route from the entry fix to the landing threshold: entry → hold → (downwind) → final → threshold. */
export function arrivalRoute(geo: RunwayGeo, hdg: number, region: Region): Route {
  const thr = pol(hdg + 180, RWY_HALF, geo.mid);
  const finalBrg = (hdg + 180) % 360;
  const e = ENTRY[region];
  const hold = pol(e.brg, HOLD_NM);
  const pts: P[] = [pol(e.brg, RANGE_NM), hold];
  const off = angDiff(e.brg, finalBrg);
  if (Math.abs(off) > 80) pts.push(pol(finalBrg + Math.sign(off) * 62, 15));   // downwind, then a base turn
  pts.push(pol(finalBrg, FINAL_NM, thr), thr);
  let len = 0;
  for (let i = 1; i < pts.length; i++) len += dist(pts[i - 1], pts[i]);
  return { pts, len, holdAt: len - dist(pts[0], pts[1]), hold, holdBrg: e.brg };
}

/** Departure route: runway → 5 NM straight ahead → exit fix on the 40 NM ring. */
export function departureRoute(geo: RunwayGeo, hdg: number, region: Region): Route {
  const start = pol(hdg + 180, RWY_HALF, geo.mid);
  const pts: P[] = [start, pol(hdg, RWY_HALF + 5, geo.mid), pol(EXIT[region].brg, RANGE_NM)];
  let len = 0;
  for (let i = 1; i < pts.length; i++) len += dist(pts[i - 1], pts[i]);
  return { pts, len, holdAt: 0, hold: start, holdBrg: 0 };
}

/** Point and track at `d` NM from the start of a polyline. */
function at(pts: P[], d: number): { p: P; trk: number } {
  let left = Math.max(0, d);
  for (let i = 1; i < pts.length; i++) {
    const seg = dist(pts[i - 1], pts[i]);
    if (left <= seg || i === pts.length - 1) {
      const f = seg === 0 ? 0 : Math.min(1, left / seg);
      const a = pts[i - 1], b = pts[i];
      return { p: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f], trk: (Math.atan2(b[0] - a[0], -(b[1] - a[1])) * 180) / Math.PI };
    }
    left -= seg;
  }
  return { p: pts[pts.length - 1], trk: 0 };
}

/** The part of a polyline from `d` NM onward (the track still to fly). */
export function ahead(pts: P[], d: number): P[] {
  const out: P[] = [at(pts, d).p];
  let acc = 0;
  for (let i = 1; i < pts.length; i++) {
    acc += dist(pts[i - 1], pts[i]);
    if (acc > d) out.push(pts[i]);
  }
  return out;
}

// Speed schedule on approach: 160 kt inside the final fix, 240 kt outside it.
const FINAL_MIN = (FINAL_NM / 160) * 60;
const toGoNm = (min: number) => (min <= FINAL_MIN ? (min / 60) * 160 : FINAL_NM + ((min - FINAL_MIN) / 60) * 240);
const toGoMin = (nm: number) => (nm <= FINAL_NM ? (nm / 160) * 60 : FINAL_MIN + ((nm - FINAL_NM) / 240) * 60);

export type AirPhase = "INBOUND" | "HOLD" | "APPROACH" | "FINAL" | "CLIMB";

export interface Track {
  cs: string; kind: "ARR" | "DEP"; p: P; trk: number; phase: AirPhase;
  alt: number;         // feet
  gs: number;          // knots
  vs: -1 | 0 | 1;      // descending / level / climbing
  path: P[];           // route still to fly
  dtg: number;         // NM to the threshold (arrivals) or flown (departures)
}

/** Racetrack at a holding fix: inbound leg on the entry radial, right turns, about 4 minutes a circuit. */
function racetrack(fix: P, inboundBrg: number, minutes: number, seed: number): { p: P; trk: number } {
  const leg = 4, r = 1.3, per = 2 * leg + 2 * Math.PI * r;
  let s = (((minutes + seed) / 4) % 1) * per;
  const out = (inboundBrg + 180) % 360;          // the outbound end points away from the airport
  const farIn = pol(out, leg, fix);
  const side = inboundBrg + 90;
  if (s < leg) return { p: pol(inboundBrg, s, farIn), trk: inboundBrg };                 // inbound leg, ends at the fix
  s -= leg;
  if (s < Math.PI * r) { const a = s / r; const c = pol(side, r, fix); return { p: pol(inboundBrg - 90 + (a * 180) / Math.PI, r, c), trk: inboundBrg + (a * 180) / Math.PI }; }
  s -= Math.PI * r;
  const nearOut = pol(side, 2 * r, fix);
  if (s < leg) return { p: pol(out, s, nearOut), trk: out };
  s -= leg;
  const a = s / r; const c = pol(side, r, farIn);
  return { p: pol(inboundBrg + 90 + (a * 180) / Math.PI, r, c), trk: out + (a * 180) / Math.PI };
}

const seedOf = (cs: string) => { let s = 0; for (const ch of cs) s = (s * 31 + ch.charCodeAt(0)) % 97; return s / 24; };

/**
 * Where an aircraft is in the terminal area at time T, or null if it is on the ground or outside it.
 * `queue` is the aircraft's place among arrivals whose planned landing time has passed without a landing:
 * they are strung out along final, three miles apart, instead of all sitting on the threshold.
 */
export function track(row: PlanRow, f: Flight | undefined, T: number, layout: Layout, ops: Ops, geos: RunwayGeo[], queue = 0): Track | null {
  if (row.diverted || !row.runway || row.status === "CANCELLED" || row.status === "DIVERTED" || row.status === "TURNED") return null;
  const geo = geos.find((g) => g.id === row.runway);
  if (!geo) return null;
  const hdg = headingInUse(layout, ops, row.runway);
  const region = regionOf(row.region);

  if (row.kind === "ARR") {
    if (f?.aldt != null || row.status === "LANDED" || row.status === "ONBLOCK") return null;
    const route = arrivalRoute(geo, hdg, region);
    const land = Math.max(row.t, T + toGoMin(2 + 3 * queue));   // overdue: in the queue on final until it reports landed
    const held = Math.max(0, row.t - row.earliest);
    const holdOut = land - toGoMin(route.holdAt);          // leaves the hold in time to land at the planned time
    const holdIn = holdOut - (held >= 2 ? held : 0);
    let dtg: number, phase: AirPhase, p: P, trk: number;
    if (T >= holdOut) {
      dtg = toGoNm(land - T);
      ({ p, trk } = at(route.pts, route.len - dtg));
      phase = dtg <= FINAL_NM ? "FINAL" : "APPROACH";
    } else if (T >= holdIn) {
      dtg = route.holdAt;
      ({ p, trk } = racetrack(route.hold, (route.holdBrg + 180) % 360, T - holdIn, seedOf(row.callsign)));
      phase = "HOLD";
    } else {
      dtg = route.holdAt + ((holdIn - T) / 60) * 240;
      if (dtg > route.len) return null;                    // not yet inside the area
      ({ p, trk } = at(route.pts, route.len - dtg));
      phase = "INBOUND";
    }
    const alt = dtg <= FINAL_NM ? dtg * 318 : Math.min(13000, 3800 + (dtg - FINAL_NM) * 330);
    return { cs: row.callsign, kind: "ARR", p, trk, phase, alt, gs: dtg <= 5 ? 140 : dtg <= FINAL_NM ? 170 : phase === "HOLD" ? 210 : 240,
      vs: phase === "HOLD" ? 0 : -1, path: phase === "HOLD" ? [p, ...ahead(route.pts, route.len - route.holdAt)] : ahead(route.pts, route.len - dtg), dtg };
  }

  const off = f?.atot;
  if (off == null || T < off) return null;
  const route = departureRoute(geo, hdg, region);
  const min = T - off;
  const flown = min <= 2 ? (min / 60) * 170 : (2 / 60) * 170 + ((min - 2) / 60) * 280;
  if (flown > route.len) return null;                      // has left the area
  const { p, trk } = at(route.pts, flown);
  return { cs: row.callsign, kind: "DEP", p, trk, phase: "CLIMB", alt: Math.min(15000, min * 2400), gs: min <= 2 ? 170 : 280, vs: 1,
    path: ahead(route.pts, flown), dtg: flown };
}

export const fixes = {
  entries: REGIONS.map((r) => ({ region: r, name: ENTRY[r].fix, p: pol(ENTRY[r].brg, RANGE_NM), brg: ENTRY[r].brg, hold: pol(ENTRY[r].brg, HOLD_NM), holdName: ENTRY[r].hold })),
  exits: REGIONS.map((r) => ({ region: r, name: EXIT[r].fix, p: pol(EXIT[r].brg, RANGE_NM), brg: EXIT[r].brg })),
};

/** Nearby aerodromes, placed by approximate bearing and distance from the airport (diversion alternates). */
export const NEIGHBOURS = [
  { icao: "OMSJ", p: pol(61, 9.4) },
  { icao: "OMDW", p: pol(207, 24) },
];

export const path = (pts: P[]) => pts.map((q, i) => `${i ? "L" : "M"}${q[0].toFixed(2)} ${q[1].toFixed(2)}`).join(" ");
