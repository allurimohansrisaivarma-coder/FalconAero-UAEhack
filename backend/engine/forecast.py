"""Forecast: demand vs capacity in 15-minute bins over the planning horizon.

Capacity comes from the operating conditions in each bin (METAR nowcast, then
TAF prevailing). A worst-case band uses TAF TEMPO/PROB groups. A fluid queue
model turns any excess demand into predicted holding/delay, and stand demand is
projected from the plan, giving early warnings before problems materialise.
"""
from __future__ import annotations

from .explain import clock
from .planning import BIN, Ctx
from .rules import Ops, ops_at, weather_for
from .state import WorldState


def _capacity(o: Ops, kind: str) -> float:
    cap = 0.0
    for r in o.runways.values():
        occ = r.occ_arr if kind == "ARR" else r.occ_dep
        if r.mode == kind:
            cap += BIN / occ
        elif r.mode == "MIXED":
            cap += 0.5 * BIN / ((r.occ_arr + r.occ_dep) / 2)
    if (kind == "DEP" and o.dep_stopped) or (kind == "ARR" and o.arr_stopped):
        cap = 0.0
    if kind in o.flow:
        cap = min(cap, o.flow[kind] * BIN / 60)
    return cap


def forecast(state: WorldState, ctx: Ctx, rows: dict) -> dict:
    now = ctx.now
    bins = sorted(ctx.ops_bins)
    out = []
    q = {"ARR": 0.0, "DEP": 0.0}
    # Backlog: flights already due but not yet moved
    for f in ctx.flights.values():
        if f.active and f.earliest < bins[0] and not (f.kind == "ARR" and f.aldt):
            q[f.kind] += 1
    alerts: list[dict] = []
    first_alert = {}

    stands_total = len(ctx.airport.stands)
    f_stands = [s.id for s in ctx.airport.stands if s.max_code == "F"]

    for b in bins:
        o = ctx.ops_bins[b]
        _, basis, worst = weather_for(state, max(b, now))
        o_risk = ops_at(state, max(b, now), worst, basis) if worst is not None else o
        row = {"t": b, "category": o.category, "risk_category": o_risk.category, "basis": o.basis,
               "lvp": o.lvp, "ramp_open": o.ramp_open, "modes": {k: v.mode for k, v in o.runways.items()}}
        for kind in ("ARR", "DEP"):
            dem = sum(1 for f in ctx.flights.values()
                      if f.active and f.kind == kind and b <= max(f.earliest, now) < b + BIN
                      and not (kind == "ARR" and f.aldt) and not (kind == "DEP" and f.atot))
            cap = _capacity(o, kind)
            cap_risk = _capacity(o_risk, kind)
            q[kind] = max(0.0, q[kind] + dem - cap)
            rate = cap / BIN if cap > 0 else 0
            delay = (q[kind] / rate) if rate > 0 else (BIN * 4 if q[kind] > 0 else 0)
            k = kind.lower()
            row.update({f"{k}_demand": dem, f"{k}_cap": round(cap * 60 / BIN, 1),
                        f"{k}_cap_risk": round(cap_risk * 60 / BIN, 1),
                        f"{k}_queue": round(q[kind], 1), f"{k}_delay": round(delay)})
            if delay >= 15 and kind not in first_alert:
                first_alert[kind] = (b, delay, dem, cap)
        # Stand demand at the middle of the bin
        mid = b + BIN // 2
        blocked = {sid for sid, bl in ctx.stand_blocks.items() if any(a <= mid < e for a, e, _ in bl)}
        used = 0
        f_used = 0
        for r in rows.values():
            if not ctx.flights[r["callsign"]].active:
                continue
            if r["stand"] and r["stand_from"] is not None and r["stand_from"] <= mid < r["stand_to"] and not r["diverted"]:
                used += 1
                if r["stand"] in f_stands:
                    f_used += 1
        # Unplanned arrivals expected on stand by then
        for f in ctx.flights.values():
            if f.active and f.kind == "ARR" and f.callsign not in rows and f.earliest + 10 <= mid < f.earliest + 10 + f.turnaround:
                used += 1
                if f.code == "F":
                    f_used += 1
        row.update(stands_used=used, stands_avail=stands_total - len(blocked),
                   f_used=f_used, f_avail=len([s for s in f_stands if s not in blocked]))
        out.append(row)

    # ---- predictive alerts ----
    for kind, (b, delay, dem, cap) in first_alert.items():
        peak = max(out, key=lambda r: r[f"{kind.lower()}_delay"])
        label = "Arrival holding" if kind == "ARR" else "Departure delay"
        lead = b - now
        alerts.append({
            "key": f"forecast:{kind}", "severity": "critical" if peak[f"{kind.lower()}_delay"] >= 30 else "warning",
            "category": "forecast",
            "title": f"{label} predicted from {clock(b)} ({'now' if lead <= 0 else f'in {lead} min'})",
            "body": (f"Demand {dem} per 15 min vs capacity {round(cap, 1)} under {ctx.ops_bins[b].category}. "
                     f"Peak predicted {label.lower()} ~{peak[f'{kind.lower()}_delay']} min around {clock(peak['t'])}."),
            "t": b,
        })
    lvp_bin = next((r for r in out if r["lvp"] and r["t"] > now + 15), None)
    if lvp_bin and not ctx.ops_bins[bins[0]].lvp:
        alerts.append({"key": "forecast:lvp", "severity": "warning", "category": "weather",
                       "title": f"Low-visibility procedures forecast from {clock(lvp_bin['t'])}",
                       "body": f"Basis: {lvp_bin['basis']}. Arrival capacity drops to {lvp_bin['arr_cap']}/h. Prepare LVP and review stand plan.",
                       "t": lvp_bin["t"]})
    risk_bin = next((r for r in out if r["risk_category"] != r["category"] and r["risk_category"].startswith(("LVP", "IMC"))), None)
    if risk_bin:
        alerts.append({"key": "forecast:risk", "severity": "info", "category": "weather",
                       "title": f"TAF risk: {risk_bin['risk_category']} possible from {clock(risk_bin['t'])}",
                       "body": f"{risk_bin['basis']} group. Worst-case arrival capacity {risk_bin['arr_cap_risk']}/h vs {risk_bin['arr_cap']}/h expected.",
                       "t": risk_bin["t"]})
    short = next((r for r in out if r["stands_used"] > r["stands_avail"]), None)
    if short:
        alerts.append({"key": "forecast:stands", "severity": "warning", "category": "forecast",
                       "title": f"Stand shortage predicted at {clock(short['t'])}",
                       "body": f"{short['stands_used']} aircraft need stands, {short['stands_avail']} available. Expect ground waits or remote stands.",
                       "t": short["t"]})
    fshort = next((r for r in out if r["f_used"] > r["f_avail"]), None)
    if fshort:
        alerts.append({"key": "forecast:stands_f", "severity": "warning", "category": "forecast",
                       "title": f"Code F (A380) stand shortage at {clock(fshort['t'])}",
                       "body": f"{fshort['f_used']} A380s need Code F stands, {fshort['f_avail']} available.",
                       "t": fshort["t"]})
    return {"bins": out, "alerts": alerts}
