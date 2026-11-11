"""Data-driven airport model loaded from JSON (runways, stands, taxi network).

Routing respects closed taxiway segments, so a TAXIWAY_STATUS event changes
taxi times and paths for every affected movement.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import networkx as nx

DATA = Path(__file__).resolve().parents[1] / "data" / "airports"


@dataclass(frozen=True)
class Runway:
    id: str
    opposite: str
    heading: int
    y: float
    x1: float
    x2: float
    ils_cat: str
    default_mode: str
    length_m: int


@dataclass(frozen=True)
class Stand:
    id: str
    group: str
    max_code: str
    contact: bool
    x: float
    y: float
    lane_y: float


class Airport:
    def __init__(self, doc: dict):
        self.doc = doc
        self.icao = doc["icao"]
        self.unit_m = doc["unit_m"]
        self.taxi_speed = doc["taxi_speed_ms"]
        self.buffer = doc["stand_buffer_min"]
        self.runways = [Runway(**r) for r in doc["runways"]]
        self.runway = {r.id: r for r in self.runways}
        self.stands = [Stand(**{k: s[k] for k in ("id", "group", "max_code", "contact", "x", "y", "lane_y")})
                       for s in doc["stands"]]
        self.stand = {s.id: s for s in self.stands}
        self.exits = doc["exits"]
        self.entry_x = doc["entry_x"]
        self._edges = [(tuple(e["a"]), tuple(e["b"]), e["name"]) for e in doc["edges"]]
        self.taxiway_names = sorted({n for _, _, n in self._edges if n not in self.stand and n != "X"})

    @classmethod
    def load(cls, icao: str = "omdb") -> "Airport":
        return cls(json.loads((DATA / f"{icao.lower()}.json").read_text()))

    # ---- routing ------------------------------------------------------------
    @lru_cache(maxsize=64)
    def _graph(self, closed: frozenset[str]) -> nx.Graph:
        g = nx.Graph()
        for a, b, name in self._edges:
            if name in closed:
                continue
            g.add_edge(a, b, name=name, length=math.dist(a, b))
        return g

    @lru_cache(maxsize=8192)
    def _route(self, a: tuple, b: tuple, closed: frozenset[str]) -> tuple[tuple, float] | None:
        g = self._graph(closed)
        try:
            path = nx.shortest_path(g, a, b, weight="length")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        return tuple(path), nx.path_weight(g, path, weight="length")

    def _minutes(self, length: float, factor: float) -> int:
        return max(2, math.ceil(length * self.unit_m / self.taxi_speed / 60.0 * factor))

    def taxi_in(self, runway: str, stand: str, code: str, factor: float = 1.0,
                closed: frozenset[str] = frozenset()) -> tuple[int, list] | None:
        """Touchdown → on-block minutes (incl. 1 min roll-out) and path; None if unreachable."""
        r, s = self.runway[runway], self.stand[stand]
        route = self._route((float(self.exits.get(code, self.exits["E"])), r.y), (s.x, s.y), closed)
        if route is None:
            return None
        path, length = route
        return 1 + self._minutes(length, factor), [list(p) for p in path]

    def taxi_out(self, runway: str, stand: str, factor: float = 1.0,
                 closed: frozenset[str] = frozenset()) -> tuple[int, list] | None:
        """Off-block → take-off minutes (incl. 1 min line-up) and path; None if unreachable."""
        r, s = self.runway[runway], self.stand[stand]
        route = self._route((s.x, s.y), (float(self.entry_x), r.y), closed)
        if route is None:
            return None
        path, length = route
        return 1 + self._minutes(length, factor), [list(p) for p in path]

    def layout(self) -> dict:
        d = self.doc
        return {
            "icao": self.icao, "name": d["name"], "note": d["note"], "viewBox": d["viewBox"],
            "runways": d["runways"], "stands": d["stands"],
            "taxiways": [{"a": list(a), "b": list(b), "name": n} for a, b, n in self._edges if n not in self.stand],
            "taxiway_names": self.taxiway_names,
            "taxiway_labels": d["taxiway_labels"], "buildings": d["buildings"], "apron": d["apron"],
            "exits": self.exits, "entry_x": self.entry_x, "holding_fix": d["holding_fix"],
        }
