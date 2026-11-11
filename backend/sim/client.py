"""Engine clients: the simulator's only connection to FalconAero."""
from __future__ import annotations

from typing import Protocol

from pydantic import TypeAdapter

from engine.events import Event

_EV = TypeAdapter(Event)


class EngineClient(Protocol):
    def ingest(self, events: list[dict]) -> list[dict]: ...
    def published_plan(self) -> dict: ...
    def decision_pending(self) -> bool: ...
    def reset(self) -> None: ...
    def publish(self, msg: dict) -> None: ...


class InProcessClient:
    """Same contract as the HTTP API, without the network hop."""

    def __init__(self, engine):
        self.engine = engine

    def ingest(self, events: list[dict]) -> list[dict]:
        parsed = []
        for e in events:
            e = {**e, "source": e.get("source", "sim")}
            parsed.append(_EV.validate_python(e))
        return self.engine.ingest(parsed)

    def published_plan(self) -> dict:
        with self.engine.lock:
            return self.engine.last.get("rows", {})

    def decision_pending(self) -> bool:
        return self.engine.decision is not None

    def reset(self) -> None:
        self.engine.reset()

    def publish(self, msg: dict) -> None:
        self.engine.publish(msg)
