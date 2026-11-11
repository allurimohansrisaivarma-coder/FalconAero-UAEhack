"""Plain-language explanations generated only from verified, structured plan data."""
from __future__ import annotations

from .planning import Ctx

CODE_NAME = {"C": "narrow-body", "D": "mid-size wide-body", "E": "wide-body", "F": "A380 / Code F"}


def clock(t: float | None) -> str:
    if t is None:
        return "—"
    t = int(t)
    return f"{(t // 60) % 24:02d}{t % 60:02d}Z"


def _occupant(stand: str, a: float, b: float, plan: dict, me: str) -> dict | None:
    for cs, r in plan.items():
        if cs == me or r["stand"] != stand or r["diverted"] or r["stand_from"] is None:
            continue
        if r["stand_from"] < b and a < r["stand_to"]:
            return r
    return None


def _delay_causes(ctx: Ctx, n: dict) -> list[str]:
    o = ctx.ops(n["t"])
    f = ctx.flights[n["callsign"]]
    causes = []
    if f.kind == "DEP" and f.tobt and f.tobt > f.est:
        causes.append(f"aircraft not ready until {clock(f.tobt)} (TOBT)")
    if o.lvp:
        causes.append(f"low-visibility procedures ({n['rwy_occ']} min per movement)")
    elif o.category == "IMC":
        causes.append("instrument conditions")
    if any(r.mode == "MIXED" for r in o.runways.values()):
        causes.append("single-runway mixed operations")
    if ctx.ground_stop and ctx.ground_stop[0] <= n["t"] <= ctx.ground_stop[1] + 30:
        causes.append("ground stop")
    if ctx.ramp_block:
        causes.append("lightning ramp suspension")
    if ctx.airspace and f.region and ("ALL" in ctx.airspace[0] or f.region.upper() in ctx.airspace[0]):
        causes.append(f"airspace closure ({f.region})")
    if f.kind in ctx.flow:
        causes.append(f"ATFM cap {ctx.flow[f.kind][0]}/h")
    if n.get("ground_wait"):
        causes.append(f"waits {n['ground_wait']} min on the ground for its stand")
    return causes or ["sequencing behind other traffic"]


def explain_changes(new: dict, ref: dict, ctx: Ctx) -> list[dict]:
    rows = []
    for cs, n in sorted(new.items(), key=lambda kv: kv[1]["t"]):
        r = ref.get(cs)
        f = ctx.flights.get(cs)
        if f is None or not f.active or n["actual"]:
            continue
        reasons, changes = [], []
        if r is None:
            if n["diverted"]:
                changes.append({"field": "stand", "from": None, "to": "DIVERT"})
                reasons.append("No usable runway/stand within the holding limit: diversion recommended.")
            else:
                changes.append({"field": "new", "from": None, "to": n["stand"]})
                reasons.append(f"Newly allocated {n['runway']} at {clock(n['t'])}" + (f", stand {n['stand']}" if n["stand"] else "") + ".")
        else:
            if n["diverted"] and not r["diverted"]:
                changes.append({"field": "stand", "from": r["stand"], "to": "DIVERT"})
                reasons.append("No compatible stand or usable runway within the holding limit: diversion recommended.")
            if n["stand"] != r["stand"] and not n["diverted"] and f.kind == "ARR":
                changes.append({"field": "stand", "from": r["stand"], "to": n["stand"]})
                old = r["stand"]
                blocked = [why for a, b, why in ctx.stand_blocks.get(old or "", []) if n["stand_from"] is not None and a < (n["stand_to"] or 0) and n["stand_from"] < b]
                if blocked:
                    reasons.append(f"Stand {old} unavailable ({blocked[0]}).")
                elif r.get("unreachable"):
                    reasons.append(f"Stand {old} not reachable with current taxiway closures.")
                else:
                    occ = _occupant(old, n["stand_from"] or 0, n["stand_to"] or 0, new, cs) if old else None
                    if occ is not None:
                        reasons.append(f"Stand {old} still occupied by {occ['callsign']} until {clock(occ['stand_to'])}.")
                    elif old:
                        reasons.append(f"Stand {old} is needed to keep another flight conflict-free.")
                st = ctx.airport.stand[n["stand"]]
                reasons.append(f"{n['stand']} fits Code {f.code} ({CODE_NAME[f.code]})" +
                               (", contact stand" if st.contact else ", remote stand (bus boarding)") + f", taxi {n['taxi_min']} min.")
            if n["runway"] != r["runway"] and n["runway"]:
                changes.append({"field": "runway", "from": r["runway"], "to": n["runway"]})
                ro = ctx.ops(n["t"]).runways.get(r["runway"]) if r["runway"] else None
                if ro and (not ro.open or ro.mode not in (f.kind, "MIXED")):
                    reasons.append(f"Runway {r['runway']} not usable ({'; '.join(ro.reasons) or ro.mode}); {n['runway']} in {ctx.ops(n['t']).runways[n['runway']].mode.lower()} mode.")
                else:
                    reasons.append(f"Moved to {n['runway']} to balance runway load.")
            dt = round(n["t"] - r["t"])
            if dt != 0 and not n["diverted"]:
                changes.append({"field": "time", "from": r["t"], "to": n["t"]})
                verb = "Lands" if f.kind == "ARR" else "Departs"
                if dt > 0:
                    reasons.append(f"{verb} {dt} min later: " + ", ".join(_delay_causes(ctx, n)) + ".")
                else:
                    reasons.append(f"{verb} {-dt} min earlier, using a free slot.")
            elif n.get("ground_wait") and not r.get("ground_wait"):
                changes.append({"field": "wait", "from": 0, "to": n["ground_wait"]})
                reasons.append(f"Lands on time but waits {n['ground_wait']} min on the ground for stand {n['stand']} (saves airborne holding).")
        if not changes:
            continue
        rows.append({"callsign": cs, "kind": f.kind, "type": f.aircraft, "priority": f.priority,
                     "changes": changes, "reasons": reasons, "t": n["t"], "stand": n["stand"],
                     "runway": n["runway"], "delay": n["delay"]})
    return rows


def summarize(label: str, k: dict, n_changes: int) -> str:
    parts = [f"holds {k['holding']} arrivals (avg {k['avg_arr_delay']} min)" if k["holding"] else "no airborne holding",
             f"{n_changes} flight{'s' if n_changes != 1 else ''} change"]
    if k["ground_waits"]:
        parts.append(f"{k['ground_waits']} ground wait{'s' if k['ground_waits'] != 1 else ''} for stands")
    if k["remote_stands"]:
        parts.append(f"{k['remote_stands']} on remote stands")
    if k["diverted"]:
        parts.append(f"{k['diverted']} diversion{'s' if k['diverted'] != 1 else ''}")
    no_slot = k["unserved"] - k["diverted"]
    if no_slot:
        parts.append(f"{no_slot} flight{'s' if no_slot != 1 else ''} left without a slot")
    s = f"{label}: " + ", ".join(parts) + "."
    if k["critical"] == 0:
        s += " All modelled safety constraints satisfied."
    return s


def situation(ctx: Ctx, conflicts: list[dict]) -> str:
    o = ctx.ops(ctx.now)
    bits = []
    w = o.weather
    if o.lvp:
        bits.append(f"Low-visibility procedures ({o.category.replace('LVP', 'CAT ')}): RVR/visibility {w.rvr_m or w.vis_m} m")
    elif o.category == "IMC":
        bits.append(f"Instrument conditions, visibility {w.vis_m} m")
    for r in o.runways.values():
        if r.mode == "CLOSED":
            bits.append(f"Runway {r.id} closed ({'; '.join(r.reasons)})")
        elif r.mode == "MIXED":
            bits.append(f"Runway {r.id} in mixed mode")
    if not o.ramp_open:
        bits.append("Lightning: ramp operations suspended")
    if o.dep_stopped:
        bits.append("Ground stop in force" + (" for all movements" if o.arr_stopped else " for departures"))
    for k, v in o.flow.items():
        bits.append(f"ATFM {k} cap {v}/h")
    if o.closed_regions:
        bits.append(f"Airspace closed via {', '.join(o.closed_regions)}")
    n_st = len(ctx.stand_blocks)
    if n_st:
        bits.append(f"{n_st} stand{'s' if n_st != 1 else ''} unavailable")
    if ctx.closed_twy:
        bits.append(f"Taxiway {', '.join(sorted(ctx.closed_twy))} closed")
    em = [f.callsign for f in ctx.flights.values() if f.active and f.priority == "emergency"]
    if em:
        bits.append(f"Emergency: {', '.join(em)}")
    head = "; ".join(bits) if bits else "Normal operations"
    crit = sum(1 for c in conflicts if c["severity"] == "critical")
    if conflicts:
        return f"{head}. The approved plan has {len(conflicts)} open issue{'s' if len(conflicts) != 1 else ''} ({crit} critical)."
    return f"{head}. The approved plan is conflict-free."
