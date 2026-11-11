"""Operational event schema: the only way data enters the engine.

Times are epoch minutes (UTC). Every event carries its source so the engine
can tell a simulated feed from live weather or an operator entry.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

Source = Literal["sim", "live", "operator", "ai", "aodb", "api"]


class _Base(BaseModel):
    source: Source = "api"
    t: float | None = None          # event time; defaults to engine 'now'


class Clock(_Base):
    type: Literal["CLOCK"] = "CLOCK"
    now: float                       # epoch minutes
    rate: float = 0.0                # sim-minutes per real second (for UI interpolation)


class FlightUpsert(_Base):
    type: Literal["FLIGHT_UPSERT"] = "FLIGHT_UPSERT"
    callsign: str
    kind: Literal["ARR", "DEP"]
    aircraft: str                    # ICAO type designator
    sched: int                       # STA / STD
    origin_dest: str = ""
    est: int | None = None           # ETA / ETD (defaults to sched)
    stand: str | None = None         # DEP: parking stand; ARR: requested/allocated stand
    priority: Literal["normal", "high", "emergency"] = "normal"
    turnaround: int | None = None    # ARR minimum ground time
    region: str | None = None        # FIR / region the flight routes through (for airspace closures)
    linked_arrival: str | None = None  # DEP: inbound flight of the same aircraft (turnaround)


class FlightUpdate(_Base):
    type: Literal["FLIGHT_UPDATE"] = "FLIGHT_UPDATE"
    callsign: str
    est: int | None = None           # new ETA/ETD estimate
    tobt: int | None = None          # DEP: target off-block (ready) time
    priority: Literal["normal", "high", "emergency"] | None = None
    status: Literal["SCHEDULED", "AIRBORNE", "LANDED", "ONBLOCK", "OFFBLOCK", "DEPARTED",
                    "CANCELLED", "DIVERTED"] | None = None
    aldt: int | None = None          # actual landing
    aibt: int | None = None          # actual in-block
    aobt: int | None = None          # actual off-block
    atot: int | None = None          # actual take-off
    runway: str | None = None
    stand: str | None = None
    reason: str | None = None


class RunwayStatus(_Base):
    type: Literal["RUNWAY_STATUS"] = "RUNWAY_STATUS"
    runway: str
    status: Literal["OPEN", "CLOSED", "RESTRICTED"]
    rate_factor: float = 1.0         # RESTRICTED: fraction of normal capacity
    ils_cat: Literal["I", "II", "III", "NONE"] | None = None  # degraded ILS
    reason: str = ""
    until: int | None = None


class StandStatus(_Base):
    type: Literal["STAND_STATUS"] = "STAND_STATUS"
    stand: str
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    reason: str = ""
    until: int | None = None


class TaxiwayStatus(_Base):
    type: Literal["TAXIWAY_STATUS"] = "TAXIWAY_STATUS"
    taxiway: str
    status: Literal["OPEN", "CLOSED"]
    reason: str = ""
    until: int | None = None


class WeatherMetar(_Base):
    type: Literal["WEATHER_METAR"] = "WEATHER_METAR"
    raw: str


class WeatherTaf(_Base):
    type: Literal["WEATHER_TAF"] = "WEATHER_TAF"
    raw: str


class LightningAlert(_Base):
    type: Literal["LIGHTNING_ALERT"] = "LIGHTNING_ALERT"
    level: Literal["CLEAR", "AMBER", "RED"]
    until: int | None = None
    reason: str = ""


class GroundStop(_Base):
    type: Literal["GROUND_STOP"] = "GROUND_STOP"
    active: bool
    scope: Literal["DEP", "ALL"] = "DEP"   # ALL = arrivals and departures (e.g. drone)
    until: int | None = None
    reason: str = ""


class FlowRestriction(_Base):
    type: Literal["FLOW_RESTRICTION"] = "FLOW_RESTRICTION"
    kind: Literal["ARR", "DEP"]
    max_per_hour: int | None          # None = lift the restriction
    until: int | None = None
    reason: str = ""


class AirspaceClosure(_Base):
    type: Literal["AIRSPACE_CLOSURE"] = "AIRSPACE_CLOSURE"
    regions: list[str]                # e.g. ["NORTH", "WEST"] or ["ALL"]
    active: bool = True
    until: int | None = None
    reason: str = ""


class OperatorNote(_Base):
    type: Literal["OPERATOR_NOTE"] = "OPERATOR_NOTE"
    text: str


class Notam(_Base):
    type: Literal["NOTAM"] = "NOTAM"
    text: str


Event = Annotated[
    Union[Clock, FlightUpsert, FlightUpdate, RunwayStatus, StandStatus, TaxiwayStatus,
          WeatherMetar, WeatherTaf, LightningAlert, GroundStop, FlowRestriction,
          AirspaceClosure, OperatorNote, Notam],
    Field(discriminator="type"),
]


EVENT_MODELS = (Clock, FlightUpsert, FlightUpdate, RunwayStatus, StandStatus, TaxiwayStatus,
                WeatherMetar, WeatherTaf, LightningAlert, GroundStop, FlowRestriction,
                AirspaceClosure, OperatorNote, Notam)
EVENT_TYPES = [m.model_fields["type"].default for m in EVENT_MODELS]
