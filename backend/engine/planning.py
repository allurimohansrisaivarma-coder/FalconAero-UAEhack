"""Planning context, plan materialisation and validation (generic, data-driven).

A *core plan* is the decision {callsign: {runway, t, stand, diverted}}. Derived
times (taxi, on/off-block, stand occupancy) come from the airport model and the
operating conditions at that time, so any plan can be re-checked as soon as an
event changes the world.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .aircraft import fits
from .airport import Airport
from .rules import Ops, ops_at, weather_for
from .state import FlightState, WorldState

BIN = 15
HORIZON = 360            # planning / forecast horizon (minutes)
ASSUMED_DURATION = {"lightning": 30, "ground_stop": 45, "airspace": 180}
FREEZE_MIN = 3
# Longest airborne hold a plan may ask for before it must propose a diversion instead.
# Planning values (holding fuel is finite); validate with the operators before real use.
MAX_HOLD = 60
MAX_HOLD_EMERGENCY = 15


@dataclass
class Ctx:
    now: int
    airport: Airport
    flights: dict[str, FlightState]
    closed_twy: frozenset[str]
    ops_bins: dict[int, Ops]
    stand_blocks: dict[str, list[tuple[int, int, str]]]
    ramp_block: tuple[int, int] | None
    ground_stop: tuple[int, int, str] | None
    airspace: tuple[list[str], int] | None
    flow: dict[str, tuple[int, int, int]]
    assumptions: list[str] = field(default_factory=list)

    def ops(self, t: float) -> Ops:
        b = int(t) // BIN * BIN
        lo = min(self.ops_bins)
        hi = max(self.ops_bins)
        return self.ops_bins[min(max(b, lo), hi)]


def hold_limit(f: FlightState) -> int:
    return MAX_HOLD_EMERGENCY if f.priority == "emergency" else MAX_HOLD


def build_ctx(state: WorldState, worst_case: bool = False) -> Ctx:
    """Planning context. With `worst_case`, each bin uses the TAF's TEMPO/PROB/BECMG conditions
    where it has any, so a plan can be stress-tested against the forecast risk."""
    now = state.tnow
    start = now // BIN * BIN

    def ops(b: int) -> Ops:
        t = max(b, now)
        if worst_case:
            _, basis, worst = weather_for(state, t)
            if worst is not None:
                return ops_at(state, t, worst, basis)
        return ops_at(state, t)

    bins = {b: ops(b) for b in range(start, now + HORIZON + BIN, BIN)}
    assumptions = []

    def end_of(r, key) -> int:
        if r.until is not None:
            return r.until
        assumptions.append(f"{key.replace('_', ' ').title()} has no end time: planning assumes {ASSUMED_DURATION[key]} min.")
        return max(now, r.since) + ASSUMED_DURATION[key]

    stand_blocks: dict[str, list] = {}
    for sid, r in state.stands.items():
        stand_blocks.setdefault(sid, []).append((r.since, r.until if r.until is not None else now + HORIZON * 2, r.reason or "unavailable"))
    ramp = None
    if state.lightning.status == "RED":
        ramp = (state.lightning.since, end_of(state.lightning, "lightning"))
    gs = None
    if state.ground_stop.status == "ACTIVE":
        gs = (state.ground_stop.since, end_of(state.ground_stop, "ground_stop"), state.ground_stop.extra.get("scope", "DEP"))
    air = None
    if state.airspace.status == "CLOSED":
        air = (state.airspace.extra.get("regions", []), end_of(state.airspace, "airspace"))
    flow = {k: (r.extra["max_per_hour"], r.since, r.until if r.until is not None else now + HORIZON)
            for k, r in state.flow.items()}
    return Ctx(now, state.airport, copy.deepcopy(state.flights), state.closed_taxiways(), bins,
               stand_blocks, ramp, gs, air, flow, assumptions)


FAR_PAST_OFFSET = 1440


def taxi_in(ctx: Ctx, f: FlightState, runway: str, stand: str, t: float):
    return ctx.airport.taxi_in(runway, stand, f.code, ctx.ops(t).taxi_factor, ctx.closed_twy)


def taxi_out(ctx: Ctx, f: FlightState, runway: str, t: float):
    return ctx.airport.taxi_out(runway, f.stand, ctx.ops(t).taxi_factor, ctx.closed_twy)


def turnaround(ctx: Ctx, f: FlightState, t: float) -> int:
    return round(f.turnaround * ctx.ops(t).turn_factor)


def occupancy(ctx: Ctx, f: FlightState, runway: str, t: float) -> int:
    ro = ctx.ops(t).runways[runway]
    base = ro.occ_arr if f.kind == "ARR" else ro.occ_dep
    return base + (1 if f.code == "F" else 0)


def materialize(core: dict, ctx: Ctx) -> dict:
    out = {}
    for cs, d in core.items():
        f = ctx.flights.get(cs)
        if f is None:
            continue
        t = d["t"]
        runway = d.get("runway")
        stand = d.get("stand")
        row = {
            "callsign": cs, "kind": f.kind, "type": f.aircraft, "code": f.code, "code_known": f.code_known,
            "priority": f.priority, "origin_dest": f.origin_dest, "region": f.region,
            "sched": f.sched, "est": f.est, "earliest": f.earliest, "tobt": f.tobt,
            "status": f.status, "runway": runway, "t": t, "stand": stand,
            "diverted": d.get("diverted", False) or f.status == "DIVERTED",
            "delay": max(0, round(t - f.sched)), "added": max(0, round(t - f.earliest)),
            "actual": bool(f.aldt if f.kind == "ARR" else f.atot),
            "taxi_min": 0, "path": [], "onblock": None, "offblock": None,
            "stand_from": None, "stand_to": None, "rwy_occ": 0, "unreachable": False,
        }
        if row["diverted"] or f.status == "CANCELLED" or runway is None:
            out[cs] = row
            continue
        row["rwy_occ"] = occupancy(ctx, f, runway, t)
        if f.kind == "ARR":
            if f.aldt is not None:
                row["t"] = t = f.aldt
            res = taxi_in(ctx, f, runway, stand, t) if stand else None
            if res is None:
                row["unreachable"] = stand is not None
                out[cs] = row
                continue
            mins, path = res
            # Planned on-block may include a ground wait for the stand (or a ramp suspension)
            onb = f.aibt if f.aibt is not None else max(t + mins, d.get("onblock") or 0)
            row["ground_wait"] = max(0, onb - (t + mins)) if f.aibt is None else 0
            row.update(taxi_min=mins, path=path, onblock=onb, offblock=onb + turnaround(ctx, f, onb),
                       stand_from=onb, stand_to=onb + turnaround(ctx, f, onb) + ctx.airport.buffer)
        else:
            if f.atot is not None:
                row["t"] = t = f.atot
            res = taxi_out(ctx, f, runway, t)
            if res is None:
                row["unreachable"] = True
                out[cs] = row
                continue
            mins, path = res
            offb = f.aobt if f.aobt is not None else t - mins
            row.update(taxi_min=mins, path=path, offblock=offb)
            if f.aobt is None:   # once it has pushed back, the aircraft no longer occupies its stand
                row.update(stand_from=ctx.now - FAR_PAST_OFFSET, stand_to=offb + ctx.airport.buffer)
        out[cs] = row
    return out


def committed(ctx: Ctx, rows: dict) -> set[str]:
    """Movements that can no longer change: actual milestones, or about to happen.
    A movement still to use a runway that has become unusable is never committed."""
    out = set()
    for cs, r in rows.items():
        f = ctx.flights[cs]
        if not f.active:
            out.add(cs)
            continue
        if f.kind == "ARR" and (f.aldt is not None or f.status in ("LANDED", "ONBLOCK")):
            out.add(cs)
            continue
        if f.kind == "DEP" and (f.aobt is not None or f.status == "OFFBLOCK"):
            # Taxiing: can still be re-sequenced unless lining up
            if r["t"] < ctx.now + FREEZE_MIN and r["runway"] in ctx.ops(r["t"]).runways_for("DEP"):
                out.add(cs)
            continue
        if r.get("runway") and r["t"] < ctx.now + FREEZE_MIN and r["t"] >= f.earliest:
            if r["runway"] in ctx.ops(r["t"]).runways_for(f.kind):
                out.add(cs)
    return out


def validate(rows: dict, ctx: Ctx) -> list[dict]:
    conflicts: list[dict] = []
    now = ctx.now

    def add(kind, sev, cs_list, res, msg, t):
        conflicts.append({"type": kind, "severity": sev, "flights": cs_list, "resource": res, "message": msg, "t": int(t)})

    for cs, f in ctx.flights.items():
        if f.active and cs not in rows and f.earliest <= now + HORIZON:
            add("unplanned", "warning" if f.priority == "normal" else "critical", [cs], "—",
                f"{cs} ({f.kind} {f.aircraft}) has no runway slot or stand yet", max(f.earliest, now))

    live = {cs: r for cs, r in rows.items() if not r["diverted"] and r["runway"] and ctx.flights[cs].active}
    for cs, r in live.items():
        f = ctx.flights[cs]
        if r["actual"]:
            continue
        t = r["t"]
        if t < now:
            # Overdue: the plan says it should already have happened
            if f.kind == "ARR" and f.aldt is None:
                add("overdue", "warning", [cs], r["runway"], f"{cs} planned to land {round(now - t)} min ago and has not", t)
            continue
        ops = ctx.ops(t)
        if r["unreachable"]:
            add("route", "critical", [cs], r["stand"], f"{cs}: no taxi route to/from stand {r['stand']} with current closures", t)
        if r["runway"] not in ops.runways_for(f.kind):
            ro = ops.runways[r["runway"]]
            why = "; ".join(ro.reasons) or f"mode {ro.mode}"
            add("runway", "critical", [cs], r["runway"],
                f"{cs} planned on {r['runway']} which is not usable for {'arrivals' if f.kind == 'ARR' else 'departures'} ({why})", t)
        if t < f.earliest:
            what = "ETA" if f.kind == "ARR" else "ready time"
            add("timing", "warning", [cs], r["runway"],
                f"{cs} planned {round(f.earliest - t)} min before its {what}", t)
        if ctx.ground_stop:
            a, b, scope = ctx.ground_stop
            if a <= t < b and (f.kind == "DEP" or scope == "ALL"):
                add("ground_stop", "critical", [cs], r["runway"], f"{cs} planned during ground stop (until {b})", t)
        if ctx.airspace and f.region:
            regions, until = ctx.airspace
            if ("ALL" in regions or f.region.upper() in regions) and t < until:
                add("airspace", "critical", [cs], f.region, f"{cs} routes via closed airspace ({f.region})", t)
        if ctx.ramp_block:
            a, b = ctx.ramp_block
            pt = r["onblock"] if f.kind == "ARR" else r["offblock"]
            if pt is not None and a <= pt < b:
                add("ramp", "critical", [cs], r["stand"],
                    f"{cs} {'on-block' if f.kind == 'ARR' else 'push-back'} planned during lightning ramp suspension", pt)
        if f.kind == "ARR" and r["stand"]:
            if not fits(f.code, ctx.airport.stand[r["stand"]].max_code):
                add("stand_size", "critical", [cs], r["stand"], f"{cs} (Code {f.code}) too large for stand {r['stand']}", t)
            for a, b, why in ctx.stand_blocks.get(r["stand"], []):
                if r["stand_from"] is not None and r["stand_from"] < b and a < r["stand_to"]:
                    add("stand_closed", "critical", [cs], r["stand"], f"{cs} assigned to stand {r['stand']} which is unavailable ({why})", t)

    # Runway spacing
    by_rwy: dict[str, list] = {}
    for cs, r in live.items():
        by_rwy.setdefault(r["runway"], []).append(r)
    for rwy, rs in by_rwy.items():
        rs.sort(key=lambda r: r["t"])
        for a, b in zip(rs, rs[1:]):
            if b["t"] < now or b["actual"]:
                continue
            if b["t"] - a["t"] < a["rwy_occ"]:
                add("separation", "critical", [a["callsign"], b["callsign"]], rwy,
                    f"{a['callsign']} → {b['callsign']} on {rwy}: {round(b['t'] - a['t'])} min apart, needs {a['rwy_occ']}", b["t"])
    # Flow restrictions (airport-wide spacing)
    for kind, (cap, fa, fb) in ctx.flow.items():
        gap = 60 / cap
        seq = sorted((r for r in live.values() if r["kind"] == kind and fa <= r["t"] < fb and not r["actual"]), key=lambda r: r["t"])
        for a, b in zip(seq, seq[1:]):
            if b["t"] - a["t"] < gap - 0.01 and b["t"] >= now:
                add("flow", "warning", [a["callsign"], b["callsign"]], kind,
                    f"ATFM cap {cap}/h: {a['callsign']} and {b['callsign']} only {round(b['t'] - a['t'])} min apart", b["t"])
    # Stand overlap
    by_stand: dict[str, list] = {}
    for cs, r in live.items():
        if r["stand"] and r["stand_from"] is not None:
            f = ctx.flights[cs]
            # A departure that is not ready (TOBT later than planned) holds its stand longer
            if f.kind == "DEP" and f.aobt is None and r["t"] < f.earliest:
                r = {**r, "stand_to": f.earliest - r["taxi_min"] + ctx.airport.buffer}
            by_stand.setdefault(r["stand"], []).append(r)
    # A departure parked on its stand without a plan yet (a fresh turnaround while a decision is pending)
    # still physically blocks it: an arrival planned onto that stand cannot get on block.
    for cs, f in ctx.flights.items():
        if f.kind == "DEP" and f.active and f.stand and f.aobt is None and cs not in rows:
            by_stand.setdefault(f.stand, []).append({"callsign": cs, "stand_from": now - FAR_PAST_OFFSET,
                                                     "stand_to": max(f.earliest, now) + ctx.airport.buffer})
    for stand, rs in by_stand.items():
        rs.sort(key=lambda r: r["stand_from"])
        for i, a in enumerate(rs):
            for b in rs[i + 1:]:
                if b["stand_from"] < a["stand_to"] and b["stand_to"] >= now:
                    add("stand_overlap", "critical", [a["callsign"], b["callsign"]], stand,
                        f"Stand {stand}: {b['callsign']} on-block before {a['callsign']} has cleared", b["stand_from"])
    conflicts.sort(key=lambda c: (c["severity"] != "critical", c["t"]))
    return conflicts


def mark_committed(conflicts: list[dict], frozen: set[str]) -> list[dict]:
    out = []
    for c in conflicts:
        if c["flights"] and all(cs in frozen for cs in c["flights"]) and c["severity"] == "critical":
            c = {**c, "severity": "warning", "message": f"Committed, monitor: {c['message']}"}
        out.append(c)
    return out
