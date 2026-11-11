import type { Layout, PlanRow, Pt } from "./types";

/** Epoch minutes → "HHMM" (UTC), optionally with seconds "HH:MM:SS". */
export function clock(t: number | null | undefined, withSeconds = false): string {
  if (t === null || t === undefined || !isFinite(t)) return "—";
  const d = new Date(t * 60000);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  if (!withSeconds) return `${hh}${mm}`;
  return `${hh}:${mm}:${String(d.getUTCSeconds()).padStart(2, "0")}`;
}

export type Phase =
  | "INBOUND" | "HOLDING" | "FINAL" | "LANDING" | "TAXI IN" | "WAIT STAND" | "ON BLOCK"
  | "ON STAND" | "TAXI OUT" | "LINE UP" | "AIRBORNE" | "DEPARTED" | "DIVERTED" | "CANCELLED" | "TURNED";

export interface AcPos { x: number; y: number; hdg: number; phase: Phase; moving: boolean; visible: boolean }

function along(path: Pt[], frac: number): { p: Pt; hdg: number } {
  if (path.length === 0) return { p: [0, 0], hdg: 0 };
  if (path.length === 1) return { p: path[0], hdg: 0 };
  const segs: number[] = [];
  let total = 0;
  for (let i = 1; i < path.length; i++) {
    const d = Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]);
    segs.push(d);
    total += d;
  }
  let target = Math.max(0, Math.min(1, frac)) * total;
  for (let i = 0; i < segs.length; i++) {
    if (target <= segs[i] || i === segs.length - 1) {
      const a = path[i];
      const b = path[i + 1];
      const f = segs[i] === 0 ? 0 : Math.min(1, target / segs[i]);
      return { p: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f], hdg: (Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI };
    }
    target -= segs[i];
  }
  return { p: path[path.length - 1], hdg: 0 };
}

function standHeading(layout: Layout, standId: string | null): number {
  const s = layout.stands.find((x) => x.id === standId);
  if (!s) return 0;
  return s.lane_y < s.y ? 90 : -90;
}

const hidden = (phase: Phase): AcPos => ({ x: 0, y: 0, hdg: 0, phase, moving: false, visible: false });

/** Aircraft position at time T from the plan row + actual milestones (status). */
export function position(row: PlanRow, T: number, layout: Layout, aldt?: number | null, aobt?: number | null, atot?: number | null): AcPos {
  if (row.status === "CANCELLED") return hidden("CANCELLED");
  if (row.diverted || row.status === "DIVERTED") return hidden("DIVERTED");
  if (row.status === "TURNED") return hidden("TURNED");
  const rwy = layout.runways.find((r) => r.id === row.runway);
  const hf = layout.holding_fix;

  if (row.kind === "ARR") {
    if (row.status === "ONBLOCK") {
      const last = row.path[row.path.length - 1];
      if (!last) return hidden("ON BLOCK");
      return { x: last[0], y: last[1], hdg: standHeading(layout, row.stand), phase: "ON BLOCK", moving: false, visible: true };
    }
    if (row.status === "LANDED" && rwy) {
      const t0 = aldt ?? row.t;
      const exitX = layout.exits[row.code] ?? 750;
      if (T < t0 + 1) {
        const f = Math.max(0, T - t0);
        const ease = 1 - (1 - f) * (1 - f);
        return { x: rwy.x1 + (exitX - rwy.x1) * ease, y: rwy.y, hdg: 0, phase: "LANDING", moving: true, visible: true };
      }
      const taxiEnd = t0 + row.taxi_min;
      if (T < taxiEnd && row.path.length) {
        const { p, hdg } = along(row.path, (T - (t0 + 1)) / Math.max(0.5, taxiEnd - (t0 + 1)));
        return { x: p[0], y: p[1], hdg, phase: "TAXI IN", moving: true, visible: true };
      }
      const pre = row.path[Math.max(0, row.path.length - 2)] ?? [exitX, rwy.y];
      return { x: pre[0], y: pre[1], hdg: 0, phase: "WAIT STAND", moving: false, visible: true };
    }
    // Airborne / scheduled
    if (!rwy) {
      if (T >= row.earliest - 6) return holdPos(row, T, hf);
      return hidden("INBOUND");
    }
    if (T < row.t - 4) {
      if (T >= row.earliest - 4) return holdPos(row, T, hf);
      return hidden("INBOUND");
    }
    const f = Math.max(0, (row.t - T) / 4);
    return { x: rwy.x1 - f * 170, y: rwy.y, hdg: 0, phase: "FINAL", moving: true, visible: true };
  }

  // Departure
  if (row.status === "DEPARTED") {
    const t0 = atot ?? row.t;
    if (rwy && T < t0 + 1.2) {
      const f = Math.max(0, (T - t0) / 1.2);
      return { x: layout.entry_x + (rwy.x2 + 80 - layout.entry_x) * f * f, y: rwy.y, hdg: 0, phase: "AIRBORNE", moving: true, visible: true };
    }
    return hidden("DEPARTED");
  }
  const first = row.path[0];
  if (row.status !== "OFFBLOCK") {
    if (!first) {
      const s = layout.stands.find((x) => x.id === row.stand);
      if (!s) return hidden("ON STAND");
      return { x: s.x, y: s.y, hdg: standHeading(layout, row.stand), phase: "ON STAND", moving: false, visible: true };
    }
    return { x: first[0], y: first[1], hdg: standHeading(layout, row.stand), phase: "ON STAND", moving: false, visible: true };
  }
  const off = aobt ?? row.offblock ?? row.t - row.taxi_min;
  const lineup = row.t - 1;
  if (T < lineup && row.path.length) {
    const { p, hdg } = along(row.path, (T - off) / Math.max(0.5, lineup - off));
    return { x: p[0], y: p[1], hdg, phase: "TAXI OUT", moving: true, visible: true };
  }
  if (rwy) return { x: layout.entry_x, y: rwy.y, hdg: 0, phase: "LINE UP", moving: false, visible: true };
  return hidden("TAXI OUT");
}

function holdPos(row: PlanRow, T: number, hf: { x: number; y: number }): AcPos {
  let seed = 0;
  for (const ch of row.callsign) seed = (seed * 31 + ch.charCodeAt(0)) % 997;
  const ang = (T / 3) * Math.PI * 2 + seed;
  return { x: hf.x + Math.cos(ang) * 22, y: hf.y + Math.sin(ang) * 12, hdg: (ang * 180) / Math.PI + 90, phase: "HOLDING", moving: true, visible: true };
}
