"""Plan KPIs (upcoming movements only).

Delay figures cover the movements a plan actually serves. A plan could flatter those
figures by diverting or dropping its hardest flights, so `unserved` (diversions plus
movements left without a slot) is reported next to them and is compared first.
"""
from __future__ import annotations

from .planning import Ctx


def kpis(rows: dict, conflicts: list[dict], ctx: Ctx) -> dict:
    now = ctx.now
    up = [r for r in rows.values() if ctx.flights[r["callsign"]].active and not r["actual"]
          and (r["t"] >= now - 1 or r["kind"] == "ARR")]
    live = [r for r in up if not r["diverted"] and r["runway"]]
    arr = [r for r in live if r["kind"] == "ARR"]
    added = [r["added"] for r in live]

    def avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else 0.0

    remote = {s.id for s in ctx.airport.stands if not s.contact}
    diverted = sum(1 for r in up if r["diverted"])
    unplanned = sum(1 for c in conflicts if c["type"] == "unplanned")
    return {
        "served": len(live),
        "unserved": diverted + unplanned,
        "diverted": diverted,
        "avg_delay": avg(added),
        "avg_arr_delay": avg([r["added"] for r in arr]),
        "max_delay": max(added, default=0),
        "delayed_15": sum(1 for d in added if d >= 15),
        "total_delay": sum(added),
        "holding": sum(1 for r in arr if r["added"] >= 3),   # ≥ 3 min beyond ETA counts as holding
        "ground_waits": sum(1 for r in arr if r.get("ground_wait")),
        "taxi_total": sum(r["taxi_min"] for r in live),
        "remote_stands": sum(1 for r in arr if r["stand"] in remote),
        "conflicts": len(conflicts),
        "critical": sum(1 for c in conflicts if c["severity"] == "critical"),
        "stand_count": len(ctx.airport.stands),
    }
