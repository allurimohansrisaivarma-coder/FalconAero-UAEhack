"""Rule-based baseline: first-come-first-served, one resource at a time, no look-ahead.

Mirrors a simple manual policy: each movement in time order takes the next free
runway slot on a usable runway, keeps its stand if free, else the first
compatible free stand, else waits for the earliest stand to clear.

It obeys the same operating rules as the optimiser (runway modes and stops, ATFM
caps, lightning ramp suspension, holding limit), so a comparison between the two
measures the value of look-ahead and joint planning, not of rule-breaking.
"""
from __future__ import annotations

from .aircraft import fits
from .planning import BIN, FAR_PAST_OFFSET, Ctx, hold_limit, materialize, occupancy, taxi_in, taxi_out, turnaround


def plan_baseline(ctx: Ctx, reference: dict, frozen: set[str]) -> dict:
    now = ctx.now
    ap = ctx.airport
    core = {cs: dict(reference[cs]) for cs in frozen if cs in reference}
    rwy_free: dict[str, float] = {}
    flow_free: dict[str, int] = {}
    busy: dict[str, list] = {s.id: [] for s in ap.stands}
    for sid, blocks in ctx.stand_blocks.items():
        for a, b, _ in blocks:
            busy[sid].append((a, b))
    for cs, r in materialize(core, ctx).items():
        if r["diverted"] or not r["runway"] or not ctx.flights[cs].active:
            continue
        rwy_free[r["runway"]] = max(rwy_free.get(r["runway"], -1e9), r["t"] + r["rwy_occ"])
        if r["stand"] and r["stand_from"] is not None:
            busy[r["stand"]].append((r["stand_from"], r["stand_to"]))

    # A waiting departure holds its stand until it can push back; one that has pushed back has left it
    hold = {cs: (now - FAR_PAST_OFFSET, max(f.earliest, now) - 5 + ap.buffer) for cs, f in ctx.flights.items()
            if f.kind == "DEP" and f.active and cs not in core and f.aobt is None}

    def free(sid, a, b):
        spans = busy[sid] + [v for k, v in hold.items() if ctx.flights[k].stand == sid]
        return all(not (a < e and s < b) for s, e in spans)

    def free_from(sid, a):
        spans = sorted(busy[sid] + [v for k, v in hold.items() if ctx.flights[k].stand == sid])
        t = a
        for s, e in spans:
            if s <= t < e:
                t = e
        return t

    def slot(kind, e):
        """Earliest (time, runway) at or after e on a runway usable for this kind of movement."""
        last = max(ctx.ops_bins)
        for b in sorted(ctx.ops_bins):
            if b + BIN <= e:
                continue
            o = ctx.ops_bins[b]
            if (kind == "DEP" and o.dep_stopped) or (kind == "ARR" and o.arr_stopped):
                continue
            options = [(int(max(e, b, rwy_free.get(r, -1e9))), r) for r in o.runways_for(kind)]
            options = [x for x in options if x[0] < b + BIN or b == last]   # the slot must fall in this bin's conditions
            if options:
                return min(options)
        return None

    def after_ramp(t):
        """First time at or after t that is outside the lightning ramp suspension."""
        if ctx.ramp_block and ctx.ramp_block[0] <= t < ctx.ramp_block[1]:
            return ctx.ramp_block[1]
        return t

    todo = sorted((f for cs, f in ctx.flights.items() if f.active and cs not in core), key=lambda f: f.earliest)
    for f in todo:
        cs = f.callsign
        e = max(f.earliest, now)
        if ctx.airspace and f.region and ("ALL" in ctx.airspace[0] or f.region.upper() in ctx.airspace[0]):
            e = max(e, ctx.airspace[1])
        ready = e
        flow = ctx.flow.get(f.kind)
        if flow and e < flow[2]:
            e = max(e, flow_free.get(f.kind, e))            # ATFM: keep the hourly cap spacing

        if f.kind == "DEP":
            s = slot("DEP", e)
            res = taxi_out(ctx, f, s[1], s[0]) if s else None
            if res and f.aobt is None and after_ramp(s[0] - res[0]) > s[0] - res[0]:   # push-back would fall in the suspension
                s = slot("DEP", after_ramp(s[0] - res[0]) + res[0])
                res = taxi_out(ctx, f, s[1], s[0]) if s else None
            if not res:
                continue
            t, r = s
            core[cs] = {"runway": r, "t": t, "stand": f.stand, "diverted": False}
            if hold.pop(cs, None):
                busy[f.stand].append((now - FAR_PAST_OFFSET, t - res[0] + ap.buffer))
        else:
            s = slot("ARR", e)
            if s is None:
                core[cs] = {"runway": None, "t": e, "stand": None, "diverted": True}
                continue
            t, r = s
            ref_stand = reference.get(cs, {}).get("stand")
            cands = [st for st in ap.stands if fits(f.code, st.max_code) and taxi_in(ctx, f, r, st.id, t)]
            cands.sort(key=lambda st: (st.id != ref_stand, not st.contact))
            ta = turnaround(ctx, f, t) + ap.buffer
            chosen = None
            for st in cands:
                mins = taxi_in(ctx, f, r, st.id, t)[0]
                if free(st.id, after_ramp(t + mins), after_ramp(t + mins) + ta):
                    chosen = st.id
                    break
            if chosen is None and cands:
                # No stand free on arrival: hold airborne for the stand that clears first
                clears, chosen, mins = min((free_from(st.id, after_ramp(t + taxi_in(ctx, f, r, st.id, t)[0])), st.id,
                                            taxi_in(ctx, f, r, st.id, t)[0]) for st in cands)
                t = int(max(t, clears - mins))
            if chosen is None or t - ready > hold_limit(f):
                core[cs] = {"runway": None, "t": t, "stand": None, "diverted": True}
                continue
            onblock = after_ramp(t + mins)
            core[cs] = {"runway": r, "t": t, "stand": chosen, "diverted": False, "onblock": onblock}
            busy[chosen].append((onblock, onblock + ta))
        rwy_free[r] = t + occupancy(ctx, f, r, t)
        if flow and flow[1] <= t < flow[2]:
            flow_free[f.kind] = t + max(1, round(60 / flow[0]))
    return core
