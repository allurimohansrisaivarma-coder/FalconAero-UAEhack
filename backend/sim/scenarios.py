"""Scenario library: timed event scripts modelled on real airport disruptions.

Each scenario is a list of (offset_minutes, event) relative to injection time.
References are to public reporting of comparable real events; parameters are
illustrative, not reconstructions.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Callable


def _dt(t: float) -> datetime:
    return datetime.fromtimestamp(int(t) * 60, tz=timezone.utc)


def metar(now: float, body: str) -> dict:
    d = _dt(now)
    return {"type": "WEATHER_METAR", "raw": f"METAR OMDB {d.day:02d}{d.hour:02d}{d.minute:02d}Z {body}"}


def taf(now: float, body: str) -> dict:
    d = _dt(now)
    return {"type": "WEATHER_TAF", "raw": f"TAF OMDB {d.day:02d}{d.hour:02d}{d.minute:02d}Z {body}"}


def _dh(t: float) -> str:
    """ddhh group for TAF validity/change groups (rounded down to the hour)."""
    d = _dt(t)
    return f"{d.day:02d}{d.hour:02d}"


Builder = Callable[[int, dict, random.Random], list[tuple[int, dict]]]
CATALOG: list[dict] = []


def scenario(id: str, name: str, category: str, severity: str, duration: int, description: str, reference: str):
    def deco(fn: Builder):
        CATALOG.append({"id": id, "name": name, "category": category, "severity": severity, "duration": duration,
                        "description": description, "reference": reference, "build": fn})
        return fn
    return deco


def _pick_dep(ctx: dict, code: str | None = None, after: int = 15) -> str | None:
    cands = [cs for cs, f in ctx["flights"].items()
             if f["kind"] == "DEP" and f["active"] and f["status"] == "SCHEDULED" and f["earliest"] > ctx["now"] + after
             and (code is None or f["code"] == code)]
    cands.sort(key=lambda cs: ctx["flights"][cs]["earliest"])
    return cands[0] if cands else None


# ----------------------------------------------------------------------------- Weather
@scenario("fog", "Radiation fog (dawn)", "Weather", "high", 180,
          "Visibility collapses from mist to dense fog with vertical visibility 100 ft, forcing CAT III LVP, then lifts.",
          "Gulf winter radiation fog regularly triggers LVP at OMDB/OMAA (NCM fog advisories).")
def _fog(now, ctx, rng):
    return [
        (0, taf(now, f"{_dh(now)}/{_dh(now + 600)} 00000KT 3000 BR BECMG {_dh(now + 20)}/{_dh(now + 60)} 0150 FG VV001 "
                     f"BECMG {_dh(now + 150)}/{_dh(now + 180)} 6000 NSW")),
        (0, metar(now, "00002KT 3000 BR NSC 22/20 Q1015 BECMG 0800 FG")),
        (10, metar(now + 10, "00000KT 1200 BR BKN003 21/20 Q1015")),
        (20, metar(now + 20, "00000KT 0400 R12L/0550N R12R/0500N FG VV002 21/21 Q1015")),
        (30, metar(now + 30, "00000KT 0150 R12L/0250N R12R/0225N FG VV001 21/21 Q1015")),
        (100, metar(now + 100, "00000KT 0600 R12L/0800U FG VV003 22/21 Q1016")),
        (130, metar(now + 130, "02003KT 2500 BR SCT005 24/21 Q1016")),
        (170, metar(now + 170, "32006KT 6000 NSC 27/20 Q1016")),
    ]


@scenario("sandstorm", "Shamal sandstorm", "Weather", "high", 150,
          "Strong north-westerly shamal lifts sand: visibility 400 m in SS, gusts 40 kt with a significant crosswind.",
          "Dust/sand storms have diverted DXB arrivals (e.g. Aug 2022: visibility < 500 m, 122 diversions over two days).")
def _sand(now, ctx, rng):
    return [
        (0, metar(now, "29018G28KT 3000 DU NSC 41/08 Q1004")),
        (10, metar(now + 10, "27024G36KT 1200 SA NSC 40/07 Q1004")),
        (20, metar(now + 20, "25028G40KT 0400 SS VV010 39/07 Q1005")),
        (80, metar(now + 80, "27022G32KT 1500 SA NSC 38/07 Q1005")),
        (130, metar(now + 130, "30014KT 5000 DU NSC 37/08 Q1006")),
    ]


@scenario("thunderstorm", "Thunderstorm & lightning", "Weather", "high", 90,
          "Cumulonimbus overhead: heavy rain, wind shear risk and a lightning RED ramp suspension (no loading, refuelling, pushback).",
          "Airports suspend ramp handling within ~5 km of lightning (e.g. Swiss/Zurich procedure); Perth 2014 shows post-landing stand delays.")
def _ts(now, ctx, rng):
    return [
        (0, metar(now, "14012KT 6000 VCTS FEW025CB 31/24 Q1008")),
        (0, {"type": "LIGHTNING_ALERT", "level": "AMBER", "reason": "CB cell 15 km SW, moving NE"}),
        (10, {"type": "LIGHTNING_ALERT", "level": "RED", "until": now + 45, "reason": "Cloud-to-ground strikes within 5 km"}),
        (12, metar(now + 12, "18018G35KT 2000 +TSRA BKN012CB 25/23 Q1009")),
        (45, metar(now + 45, "20010KT 5000 -TSRA SCT020CB 26/23 Q1009")),
        (46, {"type": "LIGHTNING_ALERT", "level": "CLEAR", "reason": "All-clear: no strikes within 5 km"}),
        (75, metar(now + 75, "22008KT 9999 FEW030 27/22 Q1009")),
    ]


@scenario("flood", "Flash flooding (Apr 2024 style)", "Weather", "critical", 240,
          "Extreme rainfall floods runway 12R and part of the apron; arrivals are capped while drainage works.",
          "16 Apr 2024: heaviest UAE rainfall in 75 years; DXB restricted arrivals and diverted flights.")
def _flood(now, ctx, rng):
    return [
        (0, metar(now, "12022G38KT 1500 +TSRA BKN008CB OVC020 24/23 Q1002")),
        (15, {"type": "RUNWAY_STATUS", "runway": "12R", "status": "CLOSED", "reason": "Standing water / flooding", "until": now + 195}),
        (25, {"type": "STAND_STATUS", "stand": "A5", "status": "UNAVAILABLE", "reason": "Apron flooding", "until": now + 200}),
        (25, {"type": "STAND_STATUS", "stand": "A6", "status": "UNAVAILABLE", "reason": "Apron flooding", "until": now + 200}),
        (25, {"type": "STAND_STATUS", "stand": "A7", "status": "UNAVAILABLE", "reason": "Apron flooding", "until": now + 200}),
        (30, {"type": "TAXIWAY_STATUS", "taxiway": "L2", "status": "CLOSED", "reason": "Flooded taxilane", "until": now + 180}),
        (30, {"type": "FLOW_RESTRICTION", "kind": "ARR", "max_per_hour": 14, "until": now + 240, "reason": "Airport arrival restriction (flooding)"}),
        (120, metar(now + 120, "14010KT 6000 -RA BKN015 25/23 Q1005")),
    ]


@scenario("heat", "Extreme heat (48°C)", "Weather", "medium", 180,
          "Afternoon heat reduces take-off performance; several departures need weight/fuel re-planning, turnarounds slow.",
          "Weight restrictions already occur in hot airports such as Dubai and Phoenix; long-haul Gulf departures favour cooler hours.")
def _heat(now, ctx, rng):
    evs = [(0, metar(now, "33010KT 9999 NSC 48/06 Q1001"))]
    deps = [cs for cs, f in ctx["flights"].items() if f["kind"] == "DEP" and f["active"] and f["status"] == "SCHEDULED"
            and f["code"] in ("E", "F") and f["earliest"] > now + 20][:3]
    for i, cs in enumerate(deps):
        evs.append((5 + 3 * i, {"type": "FLIGHT_UPDATE", "callsign": cs, "tobt": ctx["flights"][cs]["earliest"] + 25,
                                "reason": "Take-off performance (high temperature): payload re-plan"}))
    return evs


@scenario("crosswind", "Strong crosswind gusts", "Weather", "medium", 90,
          "Southerly gusts give a crosswind near limits on 12/30, increasing runway occupancy and go-around risk.",
          "Crosswind limits vary by type (typically 30-38 kt); ANSPs reduce arrival rates in gusty conditions.")
def _xw(now, ctx, rng):
    return [(0, metar(now, "21022G33KT 9999 BLDU NSC 35/10 Q1007")),
            (60, metar(now + 60, "23014KT 9999 NSC 34/11 Q1008"))]


# ----------------------------------------------------------------------------- Infrastructure
@scenario("fod", "FOD on runway 12L", "Infrastructure", "medium", 30,
          "Debris reported by a landing crew; 12L closed for inspection and sweeping.",
          "FOD inspections close runways for minutes to an hour; automated FOD detection is used at some hubs.")
def _fod(now, ctx, rng):
    return [(0, {"type": "RUNWAY_STATUS", "runway": "12L", "status": "CLOSED", "reason": "FOD inspection", "until": now + 30})]


@scenario("birdstrike", "Bird strike on departure", "Infrastructure", "medium", 25,
          "A departing aircraft reports a bird strike; 12R closed for a runway inspection.",
          "ICAO/ACI: over 50% of surveyed airports close the runway immediately after bird strikes for inspection.")
def _bird(now, ctx, rng):
    return [(0, {"type": "RUNWAY_STATUS", "runway": "12R", "status": "CLOSED", "reason": "Bird strike: runway inspection", "until": now + 25})]


@scenario("disabled", "Disabled aircraft on 12L", "Infrastructure", "critical", 120,
          "Burst tyre on landing: aircraft immobilised on 12L, runway closed until recovery. Single-runway mixed operations.",
          "Disabled-aircraft recovery typically closes a runway for 1-4 h.")
def _disabled(now, ctx, rng):
    return [(0, {"type": "RUNWAY_STATUS", "runway": "12L", "status": "CLOSED", "reason": "Disabled aircraft (burst tyre)", "until": now + 120})]


@scenario("ils_fail", "ILS failure in low visibility", "Infrastructure", "high", 120,
          "Fog with LVP while the 12L ILS degrades to CAT I: arrivals must move to 12R and departures swap.",
          "LVP requires CAT II/III-capable ILS and protected areas; equipment failures force runway swaps.")
def _ils(now, ctx, rng):
    return [(0, metar(now, "00000KT 0300 R12L/0350N R12R/0350N FG VV001 21/21 Q1015")),
            (5, {"type": "RUNWAY_STATUS", "runway": "12L", "status": "RESTRICTED", "rate_factor": 1.0, "ils_cat": "I",
                 "reason": "ILS localiser monitor alarm", "until": now + 120}),
            (110, metar(now + 110, "02004KT 4000 BR NSC 24/20 Q1015"))]


@scenario("pbb", "Jet bridge / VDGS failures", "Infrastructure", "medium", 90,
          "Passenger boarding bridges and docking guidance fail on two contact stands.",
          "Stand equipment faults (PBB, VDGS, 400 Hz) routinely force stand re-allocation.")
def _pbb(now, ctx, rng):
    return [(0, {"type": "STAND_STATUS", "stand": "A3", "status": "UNAVAILABLE", "reason": "Jet bridge hydraulic fault", "until": now + 90}),
            (5, {"type": "STAND_STATUS", "stand": "B2", "status": "UNAVAILABLE", "reason": "VDGS failure", "until": now + 75})]


@scenario("taxiway", "Taxiway K2 works", "Infrastructure", "low", 120,
          "Emergency pavement repair closes the central cross-taxiway K2; aircraft re-route around the apron.",
          "Short-notice taxiway closures are published by NOTAM and change taxi routes and times.")
def _twy(now, ctx, rng):
    return [(0, {"type": "TAXIWAY_STATUS", "taxiway": "K2", "status": "CLOSED", "reason": "Pavement repair", "until": now + 120})]


# ----------------------------------------------------------------------------- Airspace / ATC
@scenario("airspace", "Regional airspace closure (Jun 2025 style)", "Airspace & ATC", "critical", 180,
          "Airspace to the north and west closes at short notice; affected inbound flights are held and departures delayed.",
          "23-24 Jun 2025: Qatar, UAE, Bahrain and Kuwait briefly closed airspace; Dubai-bound flights diverted or turned back.")
def _air(now, ctx, rng):
    return [(0, {"type": "AIRSPACE_CLOSURE", "regions": ["NORTH", "WEST"], "active": True, "until": now + 180,
                 "reason": "Regional security situation"})]


@scenario("atfm", "ATFM regulation (staffing)", "Airspace & ATC", "medium", 120,
          "ATC staffing shortage: network manager caps arrivals at 20 per hour (IATA delay code 83).",
          "ATFM regulations cap acceptance rates; IATA AHM 730 codes 81-89 cover ATC restrictions.")
def _atfm(now, ctx, rng):
    return [(0, {"type": "FLOW_RESTRICTION", "kind": "ARR", "max_per_hour": 20, "until": now + 120, "reason": "ATC staffing"})]


@scenario("drone", "Drone sighting", "Security", "critical", 40,
          "Unauthorised drone near the approach: all movements suspended until the area is confirmed clear.",
          "Gatwick 2018 (115 sightings), Munich Oct 2025: drone sightings suspended operations.")
def _drone(now, ctx, rng):
    return [(0, {"type": "GROUND_STOP", "active": True, "scope": "ALL", "until": now + 35, "reason": "Drone sighting on final approach"})]


@scenario("security", "Security alert at terminal", "Security", "medium", 60,
          "Unattended item triggers a cordon at Concourse B; two stands closed and departures from B held.",
          "Terminal security cordons commonly close gates and hold boarding.")
def _sec(now, ctx, rng):
    evs = [(0, {"type": "STAND_STATUS", "stand": "B1", "status": "UNAVAILABLE", "reason": "Security cordon", "until": now + 60}),
           (0, {"type": "STAND_STATUS", "stand": "B5", "status": "UNAVAILABLE", "reason": "Security cordon", "until": now + 60})]
    for cs, f in ctx["flights"].items():
        if f["kind"] == "DEP" and f["active"] and f["status"] == "SCHEDULED" and (f.get("stand") or "").startswith("B"):
            evs.append((2, {"type": "FLIGHT_UPDATE", "callsign": cs, "tobt": max(f["earliest"], now + 45), "reason": "Boarding held (security cordon)"}))
    return evs


# ----------------------------------------------------------------------------- Flights
@scenario("medical", "Medical emergency inbound", "Flight", "high", 30,
          "An inbound wide-body declares a medical emergency and requests priority landing.",
          "Priority handling for medical emergencies is standard ATC practice (PAN-PAN / MAYDAY).")
def _med(now, ctx, rng):
    cs = f"UAE{rng.randint(2000, 2999)}"
    return [(0, {"type": "FLIGHT_UPSERT", "callsign": cs, "kind": "ARR", "aircraft": "B77W", "sched": now + 9, "origin_dest": "KHI",
                 "region": "EAST", "priority": "emergency"}),
            (0, {"type": "OPERATOR_NOTE", "text": f"{cs} MAYDAY medical emergency, ETA +9 min, ambulance requested"})]


@scenario("full_emergency", "Full emergency (engine fire)", "Flight", "critical", 40,
          "Inbound reports an engine fire: priority landing, then the runway is closed for inspection and foam clean-up.",
          "After a full emergency landing, runways are typically inspected before reopening.")
def _full(now, ctx, rng):
    cs = f"FDB{rng.randint(3000, 3999)}"
    return [(0, {"type": "FLIGHT_UPSERT", "callsign": cs, "kind": "ARR", "aircraft": "B38M", "sched": now + 10, "origin_dest": "BAH",
                 "region": "NORTH", "priority": "emergency"}),
            (18, {"type": "RUNWAY_STATUS", "runway": "12L", "status": "CLOSED", "reason": "Post-emergency runway inspection", "until": now + 40})]


@scenario("a380_aog", "A380 technical delay (AOG)", "Flight", "medium", 90,
          "A departing A380 goes AOG with a technical defect, holding one of only two Code F stands.",
          "Technical delays (IATA 41) are among the most common departure delay causes.")
def _aog(now, ctx, rng):
    cs = _pick_dep(ctx, "F") or _pick_dep(ctx)
    if not cs:
        return []
    return [(0, {"type": "FLIGHT_UPDATE", "callsign": cs, "tobt": ctx["flights"][cs]["earliest"] + 60, "reason": "AOG technical defect (IATA 41)"})]


@scenario("bunching", "Arrival bunching", "Flight", "medium", 60,
          "Upstream tailwinds and holding release bring eight arrivals into the same 20-minute window.",
          "Uneven ETA streams (bunching) are a key driver of AMAN sequencing delay.")
def _bunch(now, ctx, rng):
    arrs = sorted([cs for cs, f in ctx["flights"].items() if f["kind"] == "ARR" and f["active"] and f["status"] in ("SCHEDULED", "AIRBORNE")
                   and now + 20 < f["earliest"] < now + 90], key=lambda c: ctx["flights"][c]["earliest"])[:8]
    return [(0, {"type": "FLIGHT_UPDATE", "callsign": cs, "est": now + 25 + rng.randint(0, 18), "reason": "ETA revised (tailwind)"}) for cs in arrs]


@scenario("cancellations", "Wave of cancellations", "Flight", "low", 30,
          "An outstation disruption cancels four rotations at short notice.",
          "Network disruptions elsewhere cascade into hub cancellations.")
def _cxl(now, ctx, rng):
    cands = [cs for cs, f in ctx["flights"].items() if f["active"] and f["status"] == "SCHEDULED" and f["earliest"] > now + 30]
    rng.shuffle(cands)
    return [(i * 2, {"type": "FLIGHT_UPDATE", "callsign": cs, "status": "CANCELLED", "reason": "Outstation disruption"}) for i, cs in enumerate(cands[:4])]


# ----------------------------------------------------------------------------- Data
@scenario("data_outage", "MET data feed outage", "Data", "medium", 90,
          "The MET feed stops sending METARs. FalconAero should detect stale data and warn.",
          "IT/feed outages (e.g. July 2024 global IT outage) degrade situational awareness.")
def _outage(now, ctx, rng):
    return [(0, {"type": "_SIM_FEED_OUTAGE", "minutes": 90})]


# ----------------------------------------------------------------------------- Compound
@scenario("compound", "Compound: fog + A380 AOG + 12R closure", "Compound", "critical", 150,
          "Fog with LVP, a departing A380 held on its Code F stand, and runway 12R closed: single-runway mixed mode in LVP.",
          "Combines the fog, AOG and runway-closure scenarios to stress coordinated recovery.")
def _compound(now, ctx, rng):
    evs = [(0, metar(now, "00000KT 0200 R12L/0275N R12R/0250N FG VV001 22/22 Q1012")),
           (2, {"type": "RUNWAY_STATUS", "runway": "12R", "status": "CLOSED", "reason": "Disabled aircraft on taxiway exit", "until": now + 100})]
    evs += [(1, e) for _, e in _aog(now, ctx, rng)]
    evs.append((130, metar(now + 130, "02004KT 3000 BR NSC 24/21 Q1013")))
    return evs


CATEGORIES = ["Weather", "Infrastructure", "Airspace & ATC", "Security", "Flight", "Data", "Compound"]


def catalog() -> list[dict]:
    return [{k: v for k, v in s.items() if k != "build"} for s in CATALOG]


def build(sid: str, now: int, ctx: dict, rng: random.Random) -> list[tuple[int, dict]]:
    s = next((s for s in CATALOG if s["id"] == sid), None)
    if s is None:
        raise KeyError(sid)
    return s["build"](now, ctx, rng)


CHAOS_POOL = ["fod", "birdstrike", "pbb", "a380_aog", "bunching", "atfm", "medical", "thunderstorm", "cancellations", "taxiway"]
