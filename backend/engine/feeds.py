"""Live data adapters. Each one only produces events for `Engine.ingest`.

LiveWeather polls the real OMDB METAR/TAF from aviationweather.gov (NOAA AWC,
free, no key) and ingests them with source="live".
"""
from __future__ import annotations

import asyncio

import httpx

from .events import WeatherMetar, WeatherTaf

AWC = "https://aviationweather.gov/api/data"


class LiveWeather:
    def __init__(self, engine, icao: str = "OMDB", interval_s: int = 600):
        self.engine = engine
        self.icao = icao
        self.interval = interval_s
        self.enabled = False
        self.last: dict = {"metar": None, "taf": None, "error": None, "fetched": None}
        self._task: asyncio.Task | None = None

    async def fetch_once(self) -> dict:
        async with httpx.AsyncClient(timeout=15) as c:
            m = await c.get(f"{AWC}/metar", params={"ids": self.icao, "format": "raw"})
            t = await c.get(f"{AWC}/taf", params={"ids": self.icao, "format": "raw"})
        metar = m.text.strip().splitlines()[0] if m.status_code == 200 and m.text.strip() else None
        taf = " ".join(t.text.split()) if t.status_code == 200 and t.text.strip() else None
        evs = []
        if metar:
            evs.append(WeatherMetar(raw=metar if metar.startswith(("METAR", "SPECI")) else f"METAR {metar}", source="live"))
        if taf:
            evs.append(WeatherTaf(raw=taf, source="live"))
        if evs:
            self.engine.ingest(evs)
        self.last = {"metar": metar, "taf": taf, "error": None if evs else "empty response",
                     "fetched": self.engine.state.now}
        return self.last

    async def _loop(self) -> None:
        while self.enabled:
            try:
                await self.fetch_once()
            except Exception as ex:
                self.last["error"] = str(ex)[:200]
            await asyncio.sleep(self.interval)

    def set(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled and (self._task is None or self._task.done()):
            self._task = asyncio.get_event_loop().create_task(self._loop())

    def status(self) -> dict:
        return {"enabled": self.enabled, "icao": self.icao, "interval_s": self.interval, **self.last}
