"""Operational rules: weather + restrictions → what the airfield can do at time t.

The values are planning assumptions for a decision-support prototype, based on
public guidance (ICAO/SKYbrary LVP definitions: LVP when RVR < 550 m or decision
height < 200 ft; CAT II RVR ≥ 300 m, CAT III below). They are configurable
and must be validated with the airport/ANSP before operational use.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .state import WorldState
from .weather import Conditions, taf_conditions_at

ILS_RANK = {"NONE": 0, "I": 1, "II": 2, "III": 3}

# Runway occupancy per movement (minutes): planning values
ARR_OCC = {"VMC": 2, "IMC": 3, "LVP2": 4, "LVP3": 4}
DEP_OCC = {"VMC": 2, "IMC": 2, "LVP2": 3, "LVP3": 3}
XWIND_LIMIT_KT = 35
XWIND_CAUTION_KT = 25
HEAT_C = 45
NOWCAST_MIN = 60     # METAR persistence before switching to the TAF


@dataclass
class RunwayOps:
    id: str
    open: bool
    arr_ok: bool
    dep_ok: bool
    mode: str                  # ARR | DEP | MIXED | CLOSED | STANDBY
    occ_arr: int
    occ_dep: int
    reasons: list[str] = field(default_factory=list)
    direction: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Ops:
    t: int
    category: str              # VMC | IMC | LVP2 | LVP3
    lvp: bool
    required_ils: str
    taxi_factor: float
    turn_factor: float
    ramp_open: bool
    lightning: str
    dep_stopped: bool
    arr_stopped: bool
    flow: dict[str, int]
    closed_regions: list[str]
    runways: dict[str, RunwayOps]
    weather: Conditions
    basis: str                 # METAR | TAF | TAF-TEMPO ...
    advisories: list[str] = field(default_factory=list)

    def runways_for(self, kind: str) -> list[str]:
        return [r.id for r in self.runways.values() if r.mode in (kind, "MIXED")]

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("runways", "weather")}
        d["runways"] = {k: v.to_dict() for k, v in self.runways.items()}
        d["weather"] = self.weather.to_dict()
        return d


def category(c: Conditions) -> tuple[str, str]:
    """(flight category, required ILS category for arrivals)."""
    rvr = c.rvr_m if c.rvr_m is not None else c.vis_m
    ceil = c.ceiling_ft if c.ceiling_ft is not None else 99999
    if rvr < 300:
        return "LVP3", "III"
    if rvr < 550 or ceil < 200:
        return "LVP2", "II"
    if c.vis_m < 5000 or ceil < 1000:
        return "IMC", "I"
    return "VMC", "NONE"


def crosswind(c: Conditions, heading: int) -> tuple[float, float]:
    """(crosswind, headwind) components in kt using the gust if reported."""
    if c.wind_dir is None:
        return 0.0, 0.0
    spd = max(c.wind_kt, c.gust_kt or 0)
    diff = math.radians(c.wind_dir - heading)
    return abs(spd * math.sin(diff)), spd * math.cos(diff)


def weather_for(state: WorldState, t: int) -> tuple[Conditions, str, Conditions | None]:
    """Planning weather at time t: METAR nowcast, then the TAF (prevailing, worst)."""
    metar = state.metar or Conditions(cavok=True, raw="(no METAR received: assuming CAVOK)")
    if t <= state.tnow + NOWCAST_MIN or not state.taf:
        return metar, "METAR", None
    prev, worst, label = taf_conditions_at(state.taf, t)
    if prev is None:
        return metar, "METAR", None
    return prev, "TAF" + (f" {label}" if label else ""), worst


def ops_at(state: WorldState, t: int, cond: Conditions | None = None, basis: str | None = None) -> Ops:
    if cond is None:
        cond, basis, _ = weather_for(state, t)
    cat, req = category(cond)
    lvp = cat.startswith("LVP")
    adv: list[str] = []

    heavy_precip = cond.has("+RA", "+TSRA", "+SHRA", "+DZ")
    thunder = cond.has("TS", "CB")
    dust = cond.has("DU", "SA", "SS", "DS")
    gusty = (cond.gust_kt or 0) >= 30

    taxi_factor = 1.0
    if lvp:
        taxi_factor = 1.4
    elif cond.vis_m < 1500:
        taxi_factor = 1.2
    if heavy_precip:
        taxi_factor = max(taxi_factor, 1.15)
    turn_factor = 1.0
    if cond.temp_c is not None and cond.temp_c >= HEAT_C:
        turn_factor = 1.1
        adv.append(f"Extreme heat {cond.temp_c}°C: expect take-off weight restrictions and slower turnarounds.")
    if dust:
        adv.append("Dust/sand: reduced visibility and engine-ingestion risk; expect slower ground operations.")
    if thunder:
        adv.append("Thunderstorm activity: lightning ramp suspension and wind shear possible.")

    # Active restrictions at time t
    def active(r) -> bool:
        return r is not None and r.since <= t and (r.until is None or t < r.until)

    lightning = state.lightning.status if active(state.lightning) else "CLEAR"
    ramp_open = lightning != "RED"
    gs = state.ground_stop
    gs_on = gs.status == "ACTIVE" and active(gs)
    dep_stopped = gs_on
    arr_stopped = gs_on and gs.extra.get("scope") == "ALL"
    flow = {k: r.extra["max_per_hour"] for k, r in state.flow.items() if active(r)}
    regions = state.airspace.extra.get("regions", []) if state.airspace.status == "CLOSED" and active(state.airspace) else []

    runways: dict[str, RunwayOps] = {}
    for rw in state.airport.runways:
        reasons = []
        res = state.runways.get(rw.id)
        is_open = True
        rate = 1.0
        ils = rw.ils_cat
        if res is not None and active(res):
            if res.status == "CLOSED":
                is_open = False
                reasons.append(res.reason or "closed")
            elif res.status == "RESTRICTED":
                rate = max(0.1, float(res.extra.get("rate_factor") or 1.0))
                reasons.append(f"restricted {round(rate * 100)}%" + (f": {res.reason}" if res.reason else ""))
            if res.extra.get("ils_cat"):
                ils = res.extra["ils_cat"]
                reasons.append(f"ILS CAT {ils}")
        xw, hw = crosswind(cond, rw.heading)
        direction = rw.id if hw >= -5 else rw.opposite
        if xw > XWIND_LIMIT_KT:
            is_open = False
            reasons.append(f"crosswind {round(xw)} kt above limit")
        arr_ok = is_open and ILS_RANK.get(ils, 0) >= ILS_RANK[req]
        if is_open and not arr_ok:
            reasons.append(f"ILS CAT {ils} below required CAT {req}")
        rvr = cond.rvr_m if cond.rvr_m is not None else cond.vis_m
        dep_ok = is_open and rvr >= 125
        if is_open and not dep_ok:
            reasons.append("RVR below take-off minima")
        oa, od = ARR_OCC[cat], DEP_OCC[cat]
        if thunder:
            oa += 1
        if heavy_precip:
            oa += 1
            od += 1
        if gusty or xw > XWIND_CAUTION_KT:
            oa += 1
        oa, od = math.ceil(oa / rate), math.ceil(od / rate)
        runways[rw.id] = RunwayOps(rw.id, is_open, arr_ok, dep_ok, "CLOSED" if not is_open else "STANDBY",
                                   oa, od, reasons, direction)

    # Mode assignment: keep the default segregated mode when possible
    arr_cands = [r for r in state.airport.runways if runways[r.id].arr_ok]
    dep_cands = [r for r in state.airport.runways if runways[r.id].dep_ok]
    arr_pick = next((r.id for r in arr_cands if r.default_mode == "ARR"), arr_cands[0].id if arr_cands else None)
    dep_pick = next((r.id for r in dep_cands if r.default_mode == "DEP" and r.id != arr_pick), None)
    if dep_pick is None:
        dep_pick = next((r.id for r in dep_cands if r.id != arr_pick), arr_pick if arr_pick in [d.id for d in dep_cands] else None)
    if arr_pick:
        runways[arr_pick].mode = "ARR"
    if dep_pick:
        runways[dep_pick].mode = "MIXED" if dep_pick == arr_pick else "DEP"
    if arr_pick is None:
        adv.append("No runway meets arrival minima: arrivals must hold or divert.")
    if dep_pick is None:
        adv.append("No runway available for departures.")

    return Ops(t, cat, lvp, req, taxi_factor, turn_factor, ramp_open, lightning, dep_stopped, arr_stopped,
               flow, regions, runways, cond, basis or "METAR", adv)
