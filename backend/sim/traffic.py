"""Synthetic traffic generator shaped like a Gulf hub (wide-body heavy, banked waves)."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# (airline ICAO prefix, flight number range, typical fleet by code)
AIRLINES = [
    ("UAE", (1, 999), {"F": ["A388"], "E": ["B77W", "B77W", "A359"], "C": []}),
    ("FDB", (1, 1999), {"F": [], "E": [], "C": ["B38M", "B738"]}),
    ("QTR", (1000, 1099), {"F": [], "E": ["A359"], "C": ["A320"]}),
    ("BAW", (100, 199), {"F": [], "E": ["B77W", "A35K"], "C": []}),
    ("DLH", (600, 699), {"F": [], "E": ["A359"], "C": []}),
    ("AIC", (900, 999), {"F": [], "E": ["B788"], "C": ["A20N"]}),
    ("IGO", (1400, 1499), {"F": [], "E": [], "C": ["A21N", "A20N"]}),
    ("SVA", (550, 599), {"F": [], "E": ["B789"], "C": ["A320"]}),
    ("THY", (760, 769), {"F": [], "E": ["A333"], "C": ["A321"]}),
    ("QFA", (1, 9), {"F": ["A388"], "E": [], "C": []}),
    ("SIA", (490, 499), {"F": ["A388"], "E": ["A359"], "C": []}),
    ("ETD", (200, 299), {"F": [], "E": ["B789"], "C": ["A320"]}),
    ("KAC", (670, 679), {"F": [], "E": [], "C": ["A320"]}),
    ("PIA", (210, 219), {"F": [], "E": ["B77W"], "C": ["A320"]}),
]

# Destination/origin airports with the region of airspace they route through
PORTS = {
    "NORTH": ["LHR", "CDG", "FRA", "IST", "SVO", "MAN", "ZRH", "VIE", "KWI", "BAH"],
    "WEST": ["JED", "RUH", "CAI", "JFK", "ADD", "NBO", "LOS"],
    "EAST": ["BOM", "DEL", "HYD", "KHI", "SIN", "BKK", "HKG", "SYD", "MEL", "CMB", "KTM"],
    "SOUTH": ["MCT", "MLE", "SEZ", "DAR"],
}
PREFERRED = {"QFA": "EAST", "SIA": "EAST", "BAW": "NORTH", "DLH": "NORTH", "THY": "NORTH", "AIC": "EAST",
             "IGO": "EAST", "PIA": "EAST", "SVA": "WEST", "KAC": "NORTH", "QTR": "WEST", "ETD": "WEST"}


@dataclass
class TrafficParams:
    duration_h: float = 4.0
    arrivals_per_h: int = 9       # sized for the 20-stand schematic (~stand capacity / turnaround)
    mix_f: float = 0.06           # only 2 Code F stands in the schematic
    mix_e: float = 0.47           # remainder is Code C
    waves: bool = True            # hub banks
    eta_sd: float = 6.0           # ETA noise standard deviation (min)
    late_prob: float = 0.15       # P(departure has a technical/ground delay)
    initial_parked: float = 0.55  # fraction of stands occupied by departing aircraft at start
    seed: int = 11
    turn_departures: bool = True  # arrivals generate their next departure after on-block

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Generated:
    events: list[dict] = field(default_factory=list)
    eta_noise: dict[str, int] = field(default_factory=dict)
    tech: dict[str, tuple[int, int, str]] = field(default_factory=dict)  # cs -> (announce_at, tobt, reason)


TECH_REASONS = ["technical defect (IATA 41)", "late crew (IATA 63)", "catering (IATA 15)",
                "fuelling (IATA 36)", "baggage (IATA 31)", "aircraft cleaning (IATA 35)", "PRM boarding (IATA 19)"]


class Namer:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.used: set[str] = set()

    def callsign(self, prefer_code: str) -> tuple[str, str, str, str]:
        for _ in range(200):
            al, (a, b), fleet = self.rng.choice(AIRLINES)
            types = fleet.get(prefer_code) or []
            if not types:
                continue
            cs = f"{al}{self.rng.randint(a, b)}"
            if cs in self.used:
                continue
            self.used.add(cs)
            region = PREFERRED.get(al) or self.rng.choice(list(PORTS))
            if al in ("UAE", "FDB"):
                region = self.rng.choice(["NORTH", "EAST", "EAST", "WEST", "SOUTH"])
            return cs, self.rng.choice(types), self.rng.choice(PORTS[region]), region
        cs = f"FAX{len(self.used) + 100}"
        self.used.add(cs)
        return cs, {"F": "A388", "E": "B77W", "C": "A320"}[prefer_code], "MCT", "SOUTH"


def _code(rng, p: TrafficParams) -> str:
    r = rng.random()
    return "F" if r < p.mix_f else "E" if r < p.mix_f + p.mix_e else "C"


def _times(rng, start: int, hours: float, per_h: int, waves: bool) -> list[int]:
    n = round(per_h * hours)
    out = []
    for _ in range(n):
        if waves:
            # Two-hour bank cycle: arrivals peak, then departures (phase handled by caller)
            while True:
                x = rng.uniform(0, hours * 60)
                w = 0.55 + 0.45 * math.cos(2 * math.pi * x / 120)
                if rng.random() < w:
                    break
        else:
            x = rng.uniform(0, hours * 60)
        out.append(start + int(x))
    return sorted(out)


def generate(start: int, p: TrafficParams, stands: list[dict], rng: random.Random, namer: Namer) -> Generated:
    from engine.aircraft import fits, code_for  # local import keeps sim independent at import time

    g = Generated()
    # Parked departing aircraft at start (distinct stands, code fits)
    free = list(stands)
    rng.shuffle(free)
    n_parked = int(len(stands) * p.initial_parked)
    dep_times = _times(rng, start + 8, min(p.duration_h, 2.5), max(1, round(n_parked / min(p.duration_h, 2.5))), p.waves)[:n_parked]
    for etd in dep_times:
        code = _code(rng, p)
        cs, typ, port, region = namer.callsign(code)
        real_code, _ = code_for(typ)
        st = next((s for s in free if fits(real_code, s["max_code"])), None)
        if st is None:
            continue
        free.remove(st)
        g.events.append({"type": "FLIGHT_UPSERT", "callsign": cs, "kind": "DEP", "aircraft": typ, "sched": etd,
                         "origin_dest": port, "stand": st["id"], "region": region})
        if rng.random() < p.late_prob:
            mins = rng.choice([10, 15, 20, 30, 45, 60])
            g.tech[cs] = (etd - rng.randint(15, 40), etd + mins, rng.choice(TECH_REASONS))
    # Arrivals (phase-shifted so arrival banks lead departure banks)
    for sta in _times(rng, start + 5, p.duration_h, p.arrivals_per_h, p.waves):
        code = _code(rng, p)
        cs, typ, port, region = namer.callsign(code)
        noise = int(rng.gauss(0, p.eta_sd))
        g.eta_noise[cs] = noise
        g.events.append({"type": "FLIGHT_UPSERT", "callsign": cs, "kind": "ARR", "aircraft": typ, "sched": sta,
                         "origin_dest": port, "region": region})
    return g
