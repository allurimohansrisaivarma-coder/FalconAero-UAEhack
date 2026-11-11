"""Repeat the README / slide 7 fog measurement N times and report each run and the spread.

Same moment as screens.py: 04:00Z paused, fog injected, one simulated minute, then recovery options.
The Balanced plan is compared with the rule-based (first-come, first-served) plan under the same rules.
"""
import json
import statistics
import sys
import time
import urllib.request

from record import URL, start_engine

KEYS = ("critical", "unserved", "avg_delay", "total_delay", "max_delay", "delayed_15")


def call(method, path, body=None):
    req = urllib.request.Request(URL + path, method=method, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def idle():
    for _ in range(100):
        if not call("GET", "/api/state")["planning"]:
            return
        time.sleep(0.3)


def main(n: int) -> None:
    engine = start_engine()
    rows = []
    try:
        for i in range(n):
            call("POST", "/sim/restart")
            call("POST", "/api/settings", {"auto_recovery": False})
            call("POST", "/sim/pause")
            time.sleep(4); idle()                      # routine allocation of the day's traffic
            call("POST", "/sim/scenario", {"scenario": "fog", "delay": 0})
            call("POST", "/sim/jump", {"minutes": 1})
            time.sleep(1); idle()
            p = call("POST", "/api/plans")
            bal = next(o for o in p["options"] if o["label"] == "Balanced")
            row = {"before_crit": p["current"]["kpis"]["critical"],
                   "rb": {k: p["baseline"]["kpis"][k] for k in KEYS}, "fa": {k: bal["kpis"][k] for k in KEYS},
                   "ms": bal["solver"]["ms"], "all_ms": [o["solver"]["ms"] for o in p["options"]]}
            rows.append(row)
            print(f"run {i + 1}: unrevised crit {row['before_crit']} | rule-based {row['rb']} | balanced {row['fa']} | {row['ms']} ms (all {row['all_ms']})", flush=True)
    finally:
        engine.kill()
    cut = [100 * (r["rb"]["avg_delay"] - r["fa"]["avg_delay"]) / r["rb"]["avg_delay"] for r in rows if r["rb"]["avg_delay"]]
    print("avg-delay cut %: min", round(min(cut)), "median", round(statistics.median(cut)), "max", round(max(cut)))
    print("balanced avg delay values:", sorted({r["fa"]["avg_delay"] for r in rows}))
    print("max solve ms (all options):", max(max(r["all_ms"]) for r in rows))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 6)
