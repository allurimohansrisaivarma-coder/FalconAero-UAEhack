"""FalconAero recovery optimiser (OR-Tools CP-SAT), fully driven by the planning context.

Runway slots and stand allocation are solved together under hard constraints:
  * runway usable for the movement type at that time (mode, closures, minima, ground stops)
  * runway occupancy/separation per conditions (LVP, rain, wind, restrictions, A380 wake)
  * ATFM hourly caps, airspace closures by region
  * stand size (ICAO code), stand availability windows, no stand overlap (+ buffer)
  * lightning ramp suspension: no on-block or push-back inside the window
Arrivals may wait on the ground for a stand (cheaper than airborne holding) or, once the
holding limit is reached, be proposed for diversion.

Emergencies are solved first (lexicographic): their delay is minimised on its own and then
locked, so no amount of delay to other traffic can buy an emergency a later slot. Only the
second-stage objective is tunable through the presets.

The search starts from the rule-based plan (solution hint) and improves on it within a fixed
time budget. It is an anytime search: the result is the best plan found in that budget, usually
not a proven optimum, and two runs on the same world state can differ in detail.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ortools.sat.python import cp_model
from ortools.util.python import sorted_interval_list

from .aircraft import fits
from .planning import BIN, HORIZON, Ctx, hold_limit, materialize, occupancy, taxi_in, taxi_out, turnaround

Domain = sorted_interval_list.Domain

PRIORITY_W = {"normal": 1, "high": 4, "emergency": 60}
MAX_GROUND_WAIT = 40
W_DIVERT = 20000
W_UNPLACED = 30000
SOLVED = (cp_model.OPTIMAL, cp_model.FEASIBLE)
EMERGENCY_SHARE = 0.25     # part of the time budget spent on the emergency-only first stage


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    tagline: str
    w_delay: int
    w_taxi: int
    w_change: int
    w_remote: int
    w_wait: int = 6


PRESETS = [
    Preset("min_delay", "Minimum delay", "Lowest total delay, accepts more changes", 10, 1, 3, 4),
    Preset("balanced", "Balanced", "Good delay with a stable, readable plan", 10, 2, 25, 12),
    Preset("min_change", "Minimum change", "Keeps the approved plan wherever safe", 10, 1, 150, 6),
]
PRESET_BY_ID = {p.id: p for p in PRESETS}


def _merge(intervals: list[tuple[int, int]]) -> list[list[int]]:
    out: list[list[int]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _solver(seconds: float, seed: int) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.random_seed = seed
    return solver


def solve(ctx: Ctx, preset: Preset, reference: dict, frozen: set[str], hint: dict | None = None,
          time_limit: float = 3.0, seed: int = 7) -> tuple[dict, dict]:
    now = ctx.now
    off = 1440 - now                   # shift so all times are non-negative
    H = now + HORIZON + 240
    m = cp_model.CpModel()
    ap = ctx.airport

    rwy_iv: dict[str, list] = {r.id: [] for r in ap.runways}
    stand_iv: dict[str, list] = {s.id: [] for s in ap.stands}
    flow_iv: dict[str, list] = {k: [] for k in ctx.flow}
    cost: list = []
    delays: list = []
    emergency: list = []

    # ---- usable time domains per runway/kind (from conditions over time) ----
    bins = sorted(ctx.ops_bins)
    usable: dict[tuple[str, str], list[list[int]]] = {}
    for kind in ("ARR", "DEP"):
        for r in ap.runways:
            spans = []
            for b in bins:
                o = ctx.ops_bins[b]
                if r.id not in o.runways_for(kind):
                    continue
                if kind == "DEP" and o.dep_stopped:
                    continue
                if kind == "ARR" and o.arr_stopped:
                    continue
                spans.append((max(b, now), b + BIN - 1))
            if spans and spans[-1][1] >= bins[-1]:
                spans[-1] = (spans[-1][0], H)      # last state persists beyond the horizon
            if ctx.ground_stop:
                ga, gb, scope = ctx.ground_stop
                if kind == "DEP" or scope == "ALL":
                    cut = []
                    for a, b in spans:
                        if b < ga or a >= gb:
                            cut.append((a, b))
                        else:
                            if a < ga:
                                cut.append((a, ga - 1))
                            if b >= gb:
                                cut.append((gb, b))
                    spans = cut
            usable[(kind, r.id)] = _merge([(a + off, b + off) for a, b in spans if b >= a])

    # ---- frozen movements: fixed intervals (clipped so facts never conflict) ----
    m_ref = materialize({cs: reference[cs] for cs in frozen if cs in reference}, ctx)
    fixed_r: dict[str, list] = {}
    fixed_s: dict[str, list] = {}
    for cs, r in m_ref.items():
        if r["diverted"] or not r["runway"] or not ctx.flights[cs].active:
            continue
        if r["t"] >= now - 10:
            fixed_r.setdefault(r["runway"], []).append([r["t"], r["t"] + r["rwy_occ"], cs])
        if r["stand"] and r["stand_from"] is not None and r["stand_to"] > now:
            fixed_s.setdefault(r["stand"], []).append([r["stand_from"], r["stand_to"], cs])

    def _clip(spans):
        spans.sort()
        for a, b in zip(spans, spans[1:]):
            a[1] = min(a[1], b[0])
        return [s for s in spans if s[1] > s[0]]

    for rid, spans in fixed_r.items():
        for a, b, cs in _clip(spans):
            rwy_iv[rid].append(m.NewFixedSizeIntervalVar(int(a) + off, int(b - a), f"rf_{cs}"))
    for sid, spans in fixed_s.items():
        for a, b, cs in _clip(spans):
            a = max(int(a), now - 60)
            if b > a:
                stand_iv[sid].append(m.NewFixedSizeIntervalVar(a + off, int(b - a), f"sf_{cs}"))
    for sid, blocks in ctx.stand_blocks.items():
        for a, b, _ in blocks:
            a, b = max(int(a), now - 60), min(int(b), H)
            if b > a:
                stand_iv[sid].append(m.NewFixedSizeIntervalVar(a + off, b - a, f"blk_{sid}_{a}"))

    ramp = ctx.ramp_block
    choice: dict = {}
    tvars: dict = {}
    bounds: dict[str, tuple[int, int]] = {}

    for cs, f in ctx.flights.items():
        if cs in frozen or not f.active:
            continue
        if f.earliest > now + HORIZON:
            continue
        pw = PRIORITY_W[f.priority]
        ref = reference.get(cs, {})
        e = max(f.earliest, now)
        unavoidable = max(0, now - f.earliest)
        if ctx.airspace and f.region:
            regions, until = ctx.airspace
            if "ALL" in regions or f.region.upper() in regions:
                e = max(e, until)
        unavoidable = max(unavoidable, e - f.earliest)
        hi = min(e + hold_limit(f), H) if f.kind == "ARR" else H

        t = m.NewIntVar(e + off, hi + off, f"t_{cs}")
        tvars[cs] = t
        bounds[cs] = (e + off, hi + off)

        if f.kind == "DEP":
            ys = {}
            pushed = f.aobt is not None      # already off its stand: only the runway slot is left to plan
            for r in ap.runways:
                dom = [iv for iv in usable[("DEP", r.id)] if iv[1] >= e + off]
                res = taxi_out(ctx, f, r.id, e)
                if not dom or res is None:
                    continue
                mins, _ = res
                y = m.NewBoolVar(f"y_{cs}_{r.id}")
                ys[r.id] = y
                m.AddLinearExpressionInDomain(t, Domain.FromIntervals(dom)).OnlyEnforceIf(y)
                occ = occupancy(ctx, f, r.id, e)
                rwy_iv[r.id].append(m.NewOptionalFixedSizeIntervalVar(t, occ, y, f"ri_{cs}_{r.id}"))
                if not pushed:
                    if ramp:   # push-back not inside the lightning window
                        a, b = ramp
                        m.AddLinearExpressionInDomain(
                            t, Domain.FromIntervals([[0, a + mins + off - 1], [b + mins + off, H + off]])).OnlyEnforceIf(y)
                    end = m.NewIntVar(0, H + off, f"se_{cs}_{r.id}")
                    m.Add(end == t - mins + ap.buffer).OnlyEnforceIf(y)
                    m.Add(end == 0).OnlyEnforceIf(y.Not())
                    stand_iv[f.stand].append(m.NewOptionalIntervalVar(m.NewConstant(0), end, end, y, f"si_{cs}_{r.id}"))
                cost.append(preset.w_taxi * mins * y)
                if ref.get("runway") and ref["runway"] != r.id:
                    cost.append(preset.w_change * y)
            skipped = m.NewBoolVar(f"u_{cs}")
            m.AddExactlyOne([*ys.values(), skipped])
            m.Add(t == e + off).OnlyEnforceIf(skipped)
            cost.append(W_UNPLACED * skipped)
            choice[cs] = ("DEP", ys, skipped)
            for k, (cap, fa, fb) in ctx.flow.items():
                if k == "DEP" and e < fb:
                    flow_iv[k].append(m.NewOptionalFixedSizeIntervalVar(t, max(1, round(60 / cap)), skipped.Not(), f"fl_{cs}"))
        else:
            ys = {}
            x_s: dict[str, list] = {}
            z_r: dict[str, list] = {}
            for s in ap.stands:
                if not fits(f.code, s.max_code):
                    continue
                for r in ap.runways:
                    dom = [iv for iv in usable[("ARR", r.id)] if iv[1] >= e + off]
                    if not dom:
                        continue
                    res = taxi_in(ctx, f, r.id, s.id, e)
                    if res is None:
                        continue
                    mins, _ = res
                    y = m.NewBoolVar(f"y_{cs}_{r.id}_{s.id}")
                    ys[(r.id, s.id)] = (y, mins)
                    x_s.setdefault(s.id, []).append((r.id, y, mins))
                    z_r.setdefault(r.id, []).append(y)
                    cost.append(preset.w_taxi * mins * y)
                    if not s.contact:
                        cost.append(preset.w_remote * y)
                    if ref.get("stand") and ref["stand"] != s.id:
                        cost.append(preset.w_change * y)
            skipped = m.NewBoolVar(f"div_{cs}")
            m.AddExactlyOne([*(y for y, _ in ys.values()), skipped])
            m.Add(t == e + off).OnlyEnforceIf(skipped)
            cost.append(W_DIVERT * skipped)
            for rid, lst in z_r.items():
                z = m.NewBoolVar(f"z_{cs}_{rid}")
                m.Add(sum(lst) == z)
                m.AddLinearExpressionInDomain(t, Domain.FromIntervals(usable[("ARR", rid)])).OnlyEnforceIf(z)
                rwy_iv[rid].append(m.NewOptionalFixedSizeIntervalVar(t, occupancy(ctx, f, rid, e), z, f"ri_{cs}_{rid}"))
                if ref.get("runway") and ref["runway"] != rid:
                    cost.append(preset.w_change * z)
            onb_vars = {}
            ta = turnaround(ctx, f, e) + ap.buffer
            for sid, pairs in x_s.items():
                x = m.NewBoolVar(f"x_{cs}_{sid}")
                m.Add(sum(y for _, y, _ in pairs) == x)
                start = m.NewIntVar(0, H + 120 + off, f"st_{cs}_{sid}")
                wait = m.NewIntVar(0, MAX_GROUND_WAIT, f"w_{cs}_{sid}")
                for rid, y, mins in pairs:
                    m.Add(start == t + mins + wait).OnlyEnforceIf(y)
                m.Add(start == 0).OnlyEnforceIf(x.Not())
                m.Add(wait == 0).OnlyEnforceIf(x.Not())
                if ramp:
                    a, b = ramp
                    m.AddLinearExpressionInDomain(
                        start, Domain.FromIntervals([[0, a + off - 1], [b + off, H + 200 + off]])).OnlyEnforceIf(x)
                cost.append(preset.w_wait * wait)
                stand_iv[sid].append(m.NewOptionalFixedSizeIntervalVar(start, ta, x, f"si_{cs}_{sid}"))
                onb_vars[sid] = start
            choice[cs] = ("ARR", ys, skipped, onb_vars)
            for k, (cap, fa, fb) in ctx.flow.items():
                if k == "ARR" and e < fb:
                    flow_iv[k].append(m.NewOptionalFixedSizeIntervalVar(t, max(1, round(60 / cap)), skipped.Not(), f"fl_{cs}"))

        delay = m.NewIntVar(0, max(0, H - f.earliest), f"d_{cs}")
        m.Add(delay >= t - (f.earliest + off))
        cost.append(preset.w_delay * pw * delay)
        delays.append((delay, unavoidable))
        if f.priority == "emergency":
            emergency.append(delay + W_DIVERT * skipped)

    span = H - now
    worst = m.NewIntVar(0, span, "worst")
    for d, unav in delays:
        excess = m.NewIntVar(0, span, "")
        m.Add(excess >= d - unav - 15)
        cost.append(preset.w_delay * 2 * excess)
        m.Add(worst >= d - unav)
    cost.append(preset.w_delay * 3 * worst)

    for ivs in list(rwy_iv.values()) + list(flow_iv.values()):
        if ivs:
            m.AddNoOverlap(ivs)
    for ivs in stand_iv.values():
        if ivs:
            m.AddNoOverlap(ivs)

    def start_from(plan: dict) -> None:
        """Seed the search with a known plan. Values outside a variable's domain are ignored."""
        m.ClearHints()
        for cs, ch in choice.items():
            d = plan.get(cs)
            if d is None:
                continue
            lo, hi = bounds[cs]
            m.AddHint(tvars[cs], min(max(int(d["t"]) + off, lo), hi))
            if ch[0] == "DEP":
                _, ys, skipped = ch
                for rid, y in ys.items():
                    m.AddHint(y, int(rid == d.get("runway")))
                m.AddHint(skipped, int(d.get("runway") not in ys))
            else:
                _, ys, skipped, _ = ch
                key = (d.get("runway"), d.get("stand"))
                placed = key in ys and not d.get("diverted")
                for k, (y, _) in ys.items():
                    m.AddHint(y, int(placed and k == key))
                m.AddHint(skipped, int(not placed))

    def read(solver: cp_model.CpSolver) -> dict:
        plan = {}
        for cs, ch in choice.items():
            tv = solver.Value(tvars[cs]) - off
            if ch[0] == "DEP":
                _, ys, skipped = ch
                if solver.Value(skipped):
                    continue
                rid = next(r for r, y in ys.items() if solver.Value(y))
                plan[cs] = {"runway": rid, "t": tv, "stand": ctx.flights[cs].stand, "diverted": False}
            else:
                _, ys, skipped, onb = ch
                if solver.Value(skipped):
                    plan[cs] = {"runway": None, "t": tv, "stand": None, "diverted": True}
                    continue
                (rid, sid), _ = next((k, v) for k, v in ys.items() if solver.Value(v[0]))
                plan[cs] = {"runway": rid, "t": tv, "stand": sid, "diverted": False,
                            "onblock": solver.Value(onb[sid]) - off}
        return plan

    t0 = time.perf_counter()
    if hint:
        start_from(hint)
    if emergency:
        # Stage 1: emergencies alone. Their result becomes a hard bound for stage 2.
        first = _solver(time_limit * EMERGENCY_SHARE, seed)
        m.Minimize(sum(emergency))
        if first.Solve(m) in SOLVED:
            m.Add(sum(emergency) <= round(first.ObjectiveValue()))
            start_from(read(first))
    m.Minimize(sum(cost))
    solver = _solver(time_limit, seed)
    status = solver.Solve(m)
    info = {"status": solver.StatusName(status), "ms": round((time.perf_counter() - t0) * 1000),
            "objective": solver.ObjectiveValue() if status in SOLVED else None,
            "variables": len(m.Proto().variables)}
    if status not in SOLVED:
        return {}, info
    return {**{cs: dict(reference[cs]) for cs in frozen if cs in reference}, **read(solver)}, info
