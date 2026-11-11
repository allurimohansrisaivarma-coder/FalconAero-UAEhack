"""Event-sourced world state: everything the engine believes about the airport."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .aircraft import DEFAULT_TURNAROUND, code_for
from .airport import Airport
from .weather import Conditions, TafPeriod, parse_metar, parse_taf
from . import events as E

DONE = ("DEPARTED", "CANCELLED", "DIVERTED", "TURNED")


@dataclass
class FlightState:
    callsign: str
    kind: str
    aircraft: str
    code: str
    code_known: bool
    sched: int
    est: int
    origin_dest: str = ""
    priority: str = "normal"
    turnaround: int = 60
    stand: str | None = None
    region: str | None = None
    tobt: int | None = None
    status: str = "SCHEDULED"
    aldt: int | None = None
    aibt: int | None = None
    aobt: int | None = None
    atot: int | None = None
    act_runway: str | None = None
    act_stand: str | None = None

    @property
    def active(self) -> bool:
        return self.status not in DONE

    @property
    def earliest(self) -> int:
        """Earliest runway time the flight can physically use."""
        if self.kind == "DEP":
            return max(self.est, (self.tobt or self.est))
        return self.est

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__} | {"active": self.active}


@dataclass
class Restriction:
    status: str
    reason: str = ""
    until: int | None = None
    since: int = 0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason, "until": self.until, "since": self.since, **self.extra}


class WorldState:
    def __init__(self, airport: Airport):
        self.airport = airport
        self.now: float = time.time() / 60
        self.clock_rate: float = 0.0
        self.clock_source: str = "wall"
        self.flights: dict[str, FlightState] = {}
        self.runways: dict[str, Restriction] = {}
        self.stands: dict[str, Restriction] = {}
        self.taxiways: dict[str, Restriction] = {}
        self.lightning = Restriction("CLEAR")
        self.ground_stop = Restriction("INACTIVE", extra={"scope": "DEP"})
        self.flow: dict[str, Restriction] = {}
        self.airspace = Restriction("OPEN", extra={"regions": []})
        self.metar: Conditions | None = None
        self.metar_t: int | None = None
        self.metar_source: str | None = None
        self.taf: list[TafPeriod] = []
        self.taf_raw: str | None = None
        self.taf_t: int | None = None
        self.notes: list[dict] = []

    # ------------------------------------------------------------------
    @property
    def tnow(self) -> int:
        return int(self.now)

    def closed_taxiways(self) -> frozenset[str]:
        return frozenset(n for n, r in self.taxiways.items() if r.status == "CLOSED")

    def expire(self) -> list[str]:
        """Lift restrictions whose `until` has passed. Returns human messages."""
        msgs = []
        t = self.tnow
        for name, coll in (("Runway", self.runways), ("Stand", self.stands), ("Taxiway", self.taxiways)):
            for k, r in list(coll.items()):
                if r.until is not None and t >= r.until:
                    del coll[k]
                    msgs.append(f"{name} {k} restriction expired ({r.reason or r.status}).")
        if self.lightning.status != "CLEAR" and self.lightning.until is not None and t >= self.lightning.until:
            self.lightning = Restriction("CLEAR")
            msgs.append("Lightning alert cleared (all-clear time reached).")
        if self.ground_stop.status == "ACTIVE" and self.ground_stop.until is not None and t >= self.ground_stop.until:
            self.ground_stop = Restriction("INACTIVE", extra={"scope": "DEP"})
            msgs.append("Ground stop lifted (end time reached).")
        for k, r in list(self.flow.items()):
            if r.until is not None and t >= r.until:
                del self.flow[k]
                msgs.append(f"{k} flow restriction ended.")
        if self.airspace.status == "CLOSED" and self.airspace.until is not None and t >= self.airspace.until:
            self.airspace = Restriction("OPEN", extra={"regions": []})
            msgs.append("Airspace reopened (end time reached).")
        return msgs

    # ------------------------------------------------------------------
    def apply(self, ev) -> list[str]:
        """Apply one event; returns log messages. Raises ValueError on invalid references."""
        if isinstance(ev, E.Clock):
            self.now, self.clock_rate, self.clock_source = ev.now, ev.rate, ev.source
            return []

        t = int(ev.t) if ev.t is not None else self.tnow
        ap = self.airport

        if isinstance(ev, E.FlightUpsert):
            code, known = code_for(ev.aircraft)
            if ev.stand and ev.stand not in ap.stand:
                raise ValueError(f"Unknown stand {ev.stand}")
            f = self.flights.get(ev.callsign)
            ta = ev.turnaround or DEFAULT_TURNAROUND[code]
            if ev.linked_arrival and ev.linked_arrival in self.flights:
                # Same aircraft: the inbound flight hands its stand over to this departure
                self.flights[ev.linked_arrival].status = "TURNED"
            if f is None:
                self.flights[ev.callsign] = FlightState(
                    ev.callsign, ev.kind, ev.aircraft.upper(), code, known, ev.sched, ev.est or ev.sched,
                    ev.origin_dest, ev.priority, ta, ev.stand, ev.region)
                return [f"{ev.callsign} added ({ev.kind} {ev.aircraft} {ev.origin_dest})."] if ev.source != "sim" else []
            f.aircraft, f.code, f.code_known = ev.aircraft.upper(), code, known
            f.sched, f.est = ev.sched, ev.est or f.est
            f.origin_dest, f.priority, f.turnaround = ev.origin_dest, ev.priority, ta
            f.stand, f.region = ev.stand or f.stand, ev.region or f.region
            return []

        if isinstance(ev, E.FlightUpdate):
            f = self.flights.get(ev.callsign)
            if f is None:
                raise ValueError(f"Unknown flight {ev.callsign}")
            msgs = []
            for k in ("est", "tobt", "aldt", "aibt", "aobt", "atot"):
                v = getattr(ev, k)
                if v is not None:
                    setattr(f, k, v)
            if ev.priority and ev.priority != f.priority:
                f.priority = ev.priority
                msgs.append(f"{f.callsign} priority now {ev.priority.upper()}" + (f" ({ev.reason})" if ev.reason else "") + ".")
            if ev.status:
                f.status = ev.status
                if ev.status in ("CANCELLED", "DIVERTED"):
                    msgs.append(f"{f.callsign} {ev.status.lower()}" + (f": {ev.reason}" if ev.reason else "") + ".")
            if ev.runway:
                f.act_runway = ev.runway
            if ev.stand:
                if ev.stand not in ap.stand:
                    raise ValueError(f"Unknown stand {ev.stand}")
                f.act_stand = ev.stand
            if ev.tobt is not None and f.kind == "DEP" and ev.reason:
                msgs.append(f"{f.callsign} ready time (TOBT) revised: {ev.reason}.")
            return msgs

        if isinstance(ev, E.RunwayStatus):
            if ev.runway not in ap.runway:
                raise ValueError(f"Unknown runway {ev.runway}")
            if ev.status == "OPEN" and ev.ils_cat is None:
                self.runways.pop(ev.runway, None)
                return [f"Runway {ev.runway} open" + (f" ({ev.reason})" if ev.reason else "") + "."]
            self.runways[ev.runway] = Restriction(ev.status, ev.reason, ev.until, t,
                                                  {"rate_factor": ev.rate_factor, "ils_cat": ev.ils_cat})
            what = {"CLOSED": "closed", "RESTRICTED": f"restricted to {round(ev.rate_factor * 100)}%", "OPEN": "open"}[ev.status]
            if ev.ils_cat:
                what += f", ILS CAT {ev.ils_cat}"
            return [f"Runway {ev.runway} {what}" + (f": {ev.reason}" if ev.reason else "") + "."]

        if isinstance(ev, E.StandStatus):
            if ev.stand not in ap.stand:
                raise ValueError(f"Unknown stand {ev.stand}")
            if ev.status == "AVAILABLE":
                self.stands.pop(ev.stand, None)
                return [f"Stand {ev.stand} available."]
            self.stands[ev.stand] = Restriction("UNAVAILABLE", ev.reason, ev.until, t)
            return [f"Stand {ev.stand} unavailable" + (f": {ev.reason}" if ev.reason else "") + "."]

        if isinstance(ev, E.TaxiwayStatus):
            if ev.taxiway not in ap.taxiway_names:
                raise ValueError(f"Unknown taxiway {ev.taxiway}. Known: {', '.join(ap.taxiway_names)}")
            if ev.status == "OPEN":
                self.taxiways.pop(ev.taxiway, None)
                return [f"Taxiway {ev.taxiway} reopened."]
            self.taxiways[ev.taxiway] = Restriction("CLOSED", ev.reason, ev.until, t)
            return [f"Taxiway {ev.taxiway} closed" + (f": {ev.reason}" if ev.reason else "") + "."]

        if isinstance(ev, E.WeatherMetar):
            cond, _ = parse_metar(ev.raw)
            self.metar, self.metar_t, self.metar_source = cond, t, ev.source
            return []

        if isinstance(ev, E.WeatherTaf):
            self.taf, self.taf_raw, self.taf_t = parse_taf(ev.raw, t), ev.raw, t
            return [f"TAF received ({ev.source})."]

        if isinstance(ev, E.LightningAlert):
            self.lightning = Restriction(ev.level, ev.reason, ev.until, t)
            return [{"RED": "Lightning RED: ramp operations suspended (no loading, refuelling, docking or pushback).",
                     "AMBER": "Lightning AMBER: thunderstorm approaching, prepare to suspend ramp operations.",
                     "CLEAR": "Lightning all-clear: ramp operations resumed."}[ev.level]]

        if isinstance(ev, E.GroundStop):
            if ev.active:
                self.ground_stop = Restriction("ACTIVE", ev.reason, ev.until, t, {"scope": ev.scope})
                scope = "all movements" if ev.scope == "ALL" else "departures"
                return [f"Ground stop for {scope}" + (f": {ev.reason}" if ev.reason else "") + "."]
            self.ground_stop = Restriction("INACTIVE", extra={"scope": "DEP"})
            return ["Ground stop lifted."]

        if isinstance(ev, E.FlowRestriction):
            if ev.max_per_hour is None:
                self.flow.pop(ev.kind, None)
                return [f"{ev.kind} flow restriction lifted."]
            self.flow[ev.kind] = Restriction("ACTIVE", ev.reason, ev.until, t, {"max_per_hour": ev.max_per_hour})
            return [f"ATFM: {'arrivals' if ev.kind == 'ARR' else 'departures'} capped at {ev.max_per_hour}/h" +
                    (f" ({ev.reason})" if ev.reason else "") + "."]

        if isinstance(ev, E.AirspaceClosure):
            if ev.active:
                self.airspace = Restriction("CLOSED", ev.reason, ev.until, t, {"regions": [r.upper() for r in ev.regions]})
                return [f"Airspace closed for traffic via {', '.join(ev.regions)}" + (f": {ev.reason}" if ev.reason else "") + "."]
            self.airspace = Restriction("OPEN", extra={"regions": []})
            return ["Airspace reopened."]

        if isinstance(ev, (E.OperatorNote, E.Notam)):
            self.notes.append({"t": t, "kind": ev.type, "text": ev.text, "source": ev.source})
            return [f"{'NOTAM' if isinstance(ev, E.Notam) else 'Operator note'}: {ev.text[:140]}"]

        return []

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "now": self.now, "clock_rate": self.clock_rate, "clock_source": self.clock_source,
            "runways": {k: v.to_dict() for k, v in self.runways.items()},
            "stands": {k: v.to_dict() for k, v in self.stands.items()},
            "taxiways": {k: v.to_dict() for k, v in self.taxiways.items()},
            "lightning": self.lightning.to_dict(),
            "ground_stop": self.ground_stop.to_dict(),
            "flow": {k: v.to_dict() for k, v in self.flow.items()},
            "airspace": self.airspace.to_dict(),
            "metar": self.metar.to_dict() if self.metar else None,
            "metar_t": self.metar_t, "metar_source": self.metar_source,
            "taf_raw": self.taf_raw, "taf_t": self.taf_t,
            "notes": self.notes[-20:],
        }
