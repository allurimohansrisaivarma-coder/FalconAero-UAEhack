"""Engine tests: the engine is exercised only through events, never through simulator internals."""
import time

import pytest
from pydantic import TypeAdapter

from engine.core import Engine
from engine.events import Event
from engine.weather import parse_metar, parse_taf, taf_conditions_at
from engine.llm import heuristic_parse

EV = TypeAdapter(Event)
T0 = 29_000_000   # an arbitrary epoch minute


def evs(*items):
    return [EV.validate_python({"source": "api", **e}) for e in items]


def wait_for(cond, timeout=15):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.2)
    return False


@pytest.fixture()
def eng():
    e = Engine()
    e.ingest(evs({"type": "CLOCK", "now": T0},
                 {"type": "WEATHER_METAR", "raw": "METAR OMDB 101400Z 32008KT CAVOK 31/18 Q1011"}))
    flights = []
    stands = ["A1", "A3", "B1", "B5", "A7", "B6"]
    for i, (typ, st) in enumerate(zip(["A388", "B77W", "B77W", "A320", "B738", "A320"], stands)):
        flights.append({"type": "FLIGHT_UPSERT", "callsign": f"DEP{i}", "kind": "DEP", "aircraft": typ,
                        "sched": T0 + 15 + 6 * i, "stand": st, "origin_dest": "LHR", "region": "NORTH"})
    for i, typ in enumerate(["B77W", "A320", "A388", "B738", "A359", "B77W", "A321", "B789"]):
        flights.append({"type": "FLIGHT_UPSERT", "callsign": f"ARR{i}", "kind": "ARR", "aircraft": typ,
                        "sched": T0 + 10 + 5 * i, "origin_dest": "BOM", "region": "EAST" if i % 2 else "NORTH"})
    e.ingest(evs(*flights))
    assert wait_for(lambda: len(e.approved) == 14), "routine allocation did not complete"
    return e


def crit(e):
    return [c for c in e.last["conflicts"] if c["severity"] == "critical"]


def test_metar_and_taf_parsing():
    c, meta = parse_metar("METAR OMDB 101400Z 25028G40KT 0400 R12L/0550N SS VV010 39/07 Q1005")
    assert (c.wind_dir, c.wind_kt, c.gust_kt, c.vis_m, c.rvr_m, c.ceiling_ft) == (250, 28, 40, 400, 550, 1000)
    assert "SS" in c.wx and meta["station"] == "OMDB"
    p = parse_taf("TAF OMDB 101100Z 1012/1112 32010KT CAVOK BECMG 1018/1020 0300 FG VV001 TEMPO 1012/1016 4000 TSRA", T0)
    assert p and taf_conditions_at(p, p[0].start + 30)[1].has("TS")


def test_routine_allocation_is_conflict_free(eng):
    assert crit(eng) == []
    assert all(r["runway"] for r in eng.last["rows"].values())


def test_fog_reduces_capacity_and_recovery_resolves(eng):
    eng.ingest(evs({"type": "WEATHER_METAR", "raw": "METAR OMDB 101400Z 00000KT 0150 R12L/0250N FG VV001 21/21 Q1015"}))
    o = eng.last["ctx"].ops(T0)
    assert o.lvp and o.runways["12L"].occ_arr >= 4
    res = eng.generate_plans()
    best = next(x for x in res["options"] if x["id"] == res["recommended"])
    assert best["kpis"]["critical"] == 0
    eng.approve(best["id"])
    assert crit(eng) == []


def test_runway_closure_forces_mixed_mode(eng):
    eng.ingest(evs({"type": "RUNWAY_STATUS", "runway": "12R", "status": "CLOSED", "reason": "FOD", "until": T0 + 60}))
    assert any(c["type"] == "runway" for c in eng.last["conflicts"])
    assert eng.last["ctx"].ops(T0).runways["12L"].mode == "MIXED"
    res = eng.generate_plans()
    best = next(x for x in res["options"] if x["id"] == res["recommended"])
    assert best["kpis"]["critical"] == 0
    assert all(r["runway"] != "12R" or r["t"] >= T0 + 60 for r in best["plan"].values() if r["runway"])


def test_ils_degradation_swaps_arrival_runway(eng):
    eng.ingest(evs({"type": "WEATHER_METAR", "raw": "METAR OMDB 101400Z 00000KT 0300 R12L/0350N FG VV001 21/21 Q1015"},
                   {"type": "RUNWAY_STATUS", "runway": "12L", "status": "RESTRICTED", "ils_cat": "I", "reason": "ILS fault"}))
    o = eng.last["ctx"].ops(T0)
    assert o.runways["12R"].mode in ("ARR", "MIXED") and not o.runways["12L"].arr_ok


def test_lightning_blocks_ramp_and_plan_respects_it(eng):
    eng.ingest(evs({"type": "LIGHTNING_ALERT", "level": "RED", "until": T0 + 30}))
    res = eng.generate_plans()
    best = next(x for x in res["options"] if x["id"] == res["recommended"])
    for r in best["plan"].values():
        p = r["onblock"] if r["kind"] == "ARR" else r["offblock"]
        if p is not None and not r["actual"]:
            assert not (T0 <= p < T0 + 30), r["callsign"]


def test_airspace_closure_holds_affected_flights(eng):
    eng.ingest(evs({"type": "AIRSPACE_CLOSURE", "regions": ["NORTH"], "until": T0 + 90}))
    res = eng.generate_plans()
    best = next(x for x in res["options"] if x["id"] == res["recommended"])
    for cs, r in best["plan"].items():
        f = eng.state.flights[cs]
        if f.region == "NORTH" and not r["diverted"] and r["runway"]:
            assert r["t"] >= T0 + 90, cs


def test_stand_unavailable_and_unknown_type(eng):
    eng.ingest(evs({"type": "STAND_STATUS", "stand": "A2", "status": "UNAVAILABLE", "reason": "PBB fault"},
                   {"type": "FLIGHT_UPSERT", "callsign": "XYZ1", "kind": "ARR", "aircraft": "ZZ99", "sched": T0 + 50}))
    assert eng.state.flights["XYZ1"].code == "E" and not eng.state.flights["XYZ1"].code_known
    assert any(n["key"] == "flt:type:XYZ1" for n in eng.notes.listing())
    res = eng.generate_plans()
    best = next(x for x in res["options"] if x["id"] == res["recommended"])
    assert all(r["stand"] != "A2" for r in best["plan"].values() if r["kind"] == "ARR" and not r["actual"])


def test_forecast_warns_before_tafs_fog(eng):
    from datetime import datetime, timezone

    def dh(t):
        d = datetime.fromtimestamp(t * 60, tz=timezone.utc)
        return f"{d.day:02d}{d.hour:02d}"
    raw = f"TAF OMDB {dh(T0)}00Z {dh(T0)}/{dh(T0 + 1440)} 32008KT CAVOK BECMG {dh(T0 + 120)}/{dh(T0 + 180)} 0200 FG VV001"
    eng.ingest(evs({"type": "WEATHER_TAF", "raw": raw}))
    fc = eng.last["forecast"]
    assert any(b["lvp"] for b in fc["bins"])
    assert any(a["key"] == "forecast:lvp" for a in fc["alerts"])


def best_of(res):
    return next(x for x in res["options"] if x["id"] == res["recommended"])


def test_emergency_takes_the_first_slot_whatever_it_costs_others(eng):
    eng.ingest(evs({"type": "WEATHER_METAR", "raw": "METAR OMDB 101400Z 00000KT 0150 R12L/0250N FG VV001 21/21 Q1015"},
                   {"type": "FLIGHT_UPSERT", "callsign": "MAYDAY1", "kind": "ARR", "aircraft": "B77W", "sched": T0 + 20,
                    "priority": "emergency"}))
    res = eng.generate_plans()
    for opt in res["options"]:
        row = opt["plan"]["MAYDAY1"]
        assert not row["diverted"] and row["added"] == 0, (opt["id"], row["added"])


def test_emergency_with_no_usable_runway_gets_a_diversion_not_an_empty_plan(eng):
    eng.ingest(evs({"type": "RUNWAY_STATUS", "runway": "12L", "status": "CLOSED", "reason": "test", "until": T0 + 180},
                   {"type": "RUNWAY_STATUS", "runway": "12R", "status": "CLOSED", "reason": "test", "until": T0 + 180},
                   {"type": "FLIGHT_UPSERT", "callsign": "MAYDAY2", "kind": "ARR", "aircraft": "A320", "sched": T0 + 20,
                    "priority": "emergency"}))
    res = eng.generate_plans()
    assert res["options"], "the solver must still return a plan"
    assert best_of(res)["plan"]["MAYDAY2"]["diverted"]


def test_unserved_flights_are_reported_not_hidden(eng):
    eng.ingest(evs({"type": "RUNWAY_STATUS", "runway": "12L", "status": "CLOSED", "reason": "test", "until": T0 + 300},
                   {"type": "RUNWAY_STATUS", "runway": "12R", "status": "CLOSED", "reason": "test", "until": T0 + 300}))
    res = eng.generate_plans()
    for plan in (res["baseline"], best_of(res)):
        k = plan["kpis"]
        diverted = sum(1 for r in plan["plan"].values() if r["diverted"])
        assert diverted > 0 and k["diverted"] == diverted and k["unserved"] >= diverted
        assert k["served"] == sum(1 for r in plan["plan"].values() if not r["diverted"] and r["runway"] and not r["actual"])


def test_baseline_obeys_the_same_rules_as_the_optimiser(eng):
    eng.ingest(evs({"type": "LIGHTNING_ALERT", "level": "RED", "until": T0 + 30},
                   {"type": "FLOW_RESTRICTION", "kind": "ARR", "max_per_hour": 10, "until": T0 + 120}))
    base = eng.generate_plans()["baseline"]
    assert not [c for c in base["conflicts"] if c["type"] in ("ramp", "flow", "separation", "runway")], base["conflicts"]


def test_plan_is_never_worse_than_the_rule_based_one(eng):
    eng.ingest(evs({"type": "WEATHER_METAR", "raw": "METAR OMDB 101400Z 00000KT 0150 R12L/0250N FG VV001 21/21 Q1015"},
                   {"type": "RUNWAY_STATUS", "runway": "12R", "status": "CLOSED", "reason": "test", "until": T0 + 90}))
    res = eng.generate_plans()
    b, k = res["baseline"]["kpis"], best_of(res)["kpis"]
    assert (k["critical"], k["unserved"], k["total_delay"]) <= (b["critical"], b["unserved"], b["total_delay"])


def test_recommendation_ranks_safety_then_unserved_then_cost():
    from engine.core import _rank

    def opt(critical=0, unserved=0, total=100, late=0, max_delay=10, changes=0, exposed=None):
        return {"kpis": {"critical": critical, "unserved": unserved, "total_delay": total, "delayed_15": late, "max_delay": max_delay},
                "changes": [None] * changes, "risk": None if exposed is None else {"flights": exposed}}

    assert _rank(opt(total=900)) < _rank(opt(critical=1, total=0))            # no delay saving buys a conflict
    assert _rank(opt(total=900)) < _rank(opt(unserved=1, total=0))            # nor a dropped flight
    assert _rank(opt(total=100, changes=5)) < _rank(opt(total=95, changes=10))  # 5 min is not worth 5 more changes
    assert _rank(opt(total=100, late=0)) < _rank(opt(total=95, late=1))       # spreading delay beats one late flight
    assert _rank(opt(exposed=0)) < _rank(opt(exposed=6))                      # the plan that survives the forecast wins ties
    assert _rank(opt(max_delay=10)) < _rank(opt(max_delay=40))                # last tie-break: worst single delay


def test_reject_keeps_the_approved_plan_and_is_logged(eng):
    eng.ingest(evs({"type": "RUNWAY_STATUS", "runway": "12R", "status": "CLOSED", "reason": "FOD", "until": T0 + 60}))
    before = dict(eng.approved)
    eng.generate_plans()
    eng.reject_plans("Test Controller")
    assert eng.approved == before and eng.options is None
    assert any("rejected by Test Controller" in l["text"] for l in eng.log)
    with pytest.raises(ValueError):
        eng.reject_plans()


def test_nothing_changes_without_approval_when_upkeep_is_off(eng):
    eng.settings["auto_allocate"] = False
    before = dict(eng.approved)
    eng.ingest(evs({"type": "FLIGHT_UPSERT", "callsign": "NEW1", "kind": "ARR", "aircraft": "A320", "sched": T0 + 60}))
    time.sleep(3)
    assert eng.approved == before
    res = eng.generate_plans()
    eng.approve(res["recommended"])
    assert "NEW1" in eng.approved


FOG = "METAR OMDB 101400Z 00000KT 0150 R12L/0250N FG VV001 21/21 Q1015"


def test_a_new_operating_condition_is_never_absorbed_by_routine_upkeep(eng):
    """Fog changes runway spacing for everyone: the engine must ask, not quietly re-sequence."""
    eng.ingest(evs(*[{"type": "FLIGHT_UPSERT", "callsign": f"TIGHT{i}", "kind": "ARR", "aircraft": "A320", "sched": T0 + 70 + 2 * i}
                     for i in range(6)]))
    assert wait_for(lambda: len(eng.approved) == 20), "the bunched arrivals were not allocated"
    before = {cs: dict(d) for cs, d in eng.approved.items()}
    eng.ingest(evs({"type": "WEATHER_METAR", "raw": FOG}))
    assert crit(eng), "fog should leave the approved plan with spacing conflicts"
    assert wait_for(lambda: eng.options is not None and not eng.planning_busy), "no recovery plan was offered"
    assert eng.approved == before, "the plan changed without approval"
    assert eng.decision == "New operating conditions" and eng.options["valid"]
    assert not any(l["kind"] == "auto" and "re-sequenced" in l["text"] and l["t"] >= T0 for l in eng.log[-3:])
    eng.approve(eng.options["recommended"])
    assert crit(eng) == [] and eng.decision is None


def test_small_drift_under_accepted_conditions_is_still_handled_automatically(eng):
    eng.ingest(evs({"type": "FLIGHT_UPDATE", "callsign": "ARR3", "est": T0 + 28}))
    assert wait_for(lambda: eng.approved["ARR3"]["t"] >= T0 + 28), "routine upkeep did not follow the new ETA"
    assert eng.decision is None and eng.options is None


def test_upkeep_hands_back_changes_beyond_its_limits(eng):
    eng.settings["max_auto_delay"] = 2
    eng.ingest(evs({"type": "FLIGHT_UPDATE", "callsign": "DEP1", "tobt": T0 + 60, "reason": "technical"}))
    assert wait_for(lambda: eng.decision is not None), "a 40-minute push should not be applied on upkeep's own authority"
    assert eng.decision == "Change too large for routine upkeep"
    assert eng.approved["DEP1"]["t"] < T0 + 60


def test_an_offered_plan_survives_the_clock_moving_on(eng):
    eng.ingest(evs({"type": "RUNWAY_STATUS", "runway": "12R", "status": "CLOSED", "reason": "FOD", "until": T0 + 60}))
    assert wait_for(lambda: eng.options is not None and not eng.planning_busy)
    offered = eng.options["id"]
    eng.ingest(evs({"type": "CLOCK", "now": T0 + 2}))
    assert eng.options["id"] == offered and not eng.options["stale"]
    eng.approve(eng.options["recommended"])
    assert crit(eng) == []


def test_simulated_aircraft_obey_fog_whatever_the_plan_says():
    import random

    from sim.world import World

    w = World(random.Random(1), namer=None)
    for cs in ("D1", "D2"):
        w.observe({"type": "FLIGHT_UPSERT", "callsign": cs, "kind": "DEP", "aircraft": "A320", "sched": 100, "stand": "A1"})
        w.flights[cs]["status"] = "OFFBLOCK"
    plan = {cs: {"runway": "12R", "t": 100, "offblock": 90, "taxi_min": 8, "rwy_occ": 3} for cs in ("D1", "D2")}

    w.observe({"type": "WEATHER_METAR", "raw": "METAR OMDB 101400Z 00000KT 0050 R12R/0100N FG VV001 21/21 Q1015"})
    assert w.step(100, plan, {}, {}, False) == [], "nobody takes off below 125 m RVR"

    w.observe({"type": "WEATHER_METAR", "raw": FOG})
    first = w.step(101, plan, {}, {}, False)
    assert [e["callsign"] for e in first if e.get("status") == "DEPARTED"] == ["D1"], "one runway, one movement at a time"
    assert w.step(102, plan, {}, {}, False) == [], "the second waits for the low-visibility spacing"
    assert [e["callsign"] for e in w.step(104, plan, {}, {}, False)] == ["D2"]


def test_forecast_stress_test_flags_plans_exposed_to_the_taf(eng):
    from datetime import datetime, timezone

    def dh(t):
        d = datetime.fromtimestamp(t * 60, tz=timezone.utc)
        return f"{d.day:02d}{d.hour:02d}"
    raw = f"TAF OMDB {dh(T0)}00Z {dh(T0)}/{dh(T0 + 1440)} 32008KT CAVOK TEMPO {dh(T0 + 60)}/{dh(T0 + 240)} 0200 FG VV001"
    flights = [{"type": "FLIGHT_UPSERT", "callsign": f"LATE{i}", "kind": "ARR", "aircraft": "A320", "sched": T0 + 130 + 2 * i}
               for i in range(8)]
    eng.ingest(evs({"type": "WEATHER_TAF", "raw": raw}, *flights))
    res = eng.generate_plans()
    risk = best_of(res)["risk"]
    assert risk and risk["category"].startswith("LVP") and "TEMPO" in risk["basis"]
    assert risk["flights"] > 0     # packed at 2-minute spacing, which fog would not allow


def test_invalid_reference_is_rejected_not_crashing(eng):
    r = eng.ingest(evs({"type": "STAND_STATUS", "stand": "Z9", "status": "UNAVAILABLE"}))
    assert r[0]["ok"] is False


def test_heuristic_notam_parser():
    known = {"runways": ["12L", "12R"], "runway_alias": {"30R": "12L", "30L": "12R"}, "stands": ["B3"],
             "taxiways": ["K2"], "flights": {"UAE5": {"kind": "DEP", "earliest": T0 + 30}}}
    out = heuristic_parse("RWY 30L CLSD DUE WIP until 1600\nStand B3 jet bridge u/s\nUAE5 delayed 40 min", T0, known)
    types = [o["type"] for o in out]
    assert types == ["RUNWAY_STATUS", "STAND_STATUS", "FLIGHT_UPDATE"]
    assert out[0]["runway"] == "12R" and out[2]["tobt"] == T0 + 70
