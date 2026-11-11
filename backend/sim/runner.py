"""Simulator runner: clock, traffic, scheduled events, scenarios, chaos, weather composer."""
from __future__ import annotations

import asyncio
import itertools
import random
import time
from datetime import datetime, timezone

from engine.aircraft import code_for

from . import scenarios as S
from .client import EngineClient
from .traffic import Namer, TrafficParams, generate
from .world import World

SPEEDS = [1, 5, 10, 30, 60, 120]
HOLD_SPEED = 1      # real time while a decision is pending
TICK = 0.25


def epoch_min(dt: datetime) -> int:
    return int(dt.timestamp() // 60)


def compose_metar(now: float, p: dict) -> str:
    """Build a METAR from composer fields."""
    d = datetime.fromtimestamp(int(now) * 60, tz=timezone.utc)
    parts = [f"METAR OMDB {d.day:02d}{d.hour:02d}{d.minute:02d}Z"]
    wd, ws, wg = p.get("wind_dir"), int(p.get("wind_kt", 0)), p.get("gust_kt")
    wdir = "VRB" if wd in (None, "", "VRB") else f"{int(wd):03d}"
    parts.append(f"{wdir}{ws:02d}" + (f"G{int(wg):02d}" if wg else "") + "KT")
    vis = int(p.get("vis_m", 9999))
    wx = [w for w in p.get("wx", []) if w]
    ceil = p.get("ceiling_ft")
    if vis >= 9999 and not wx and not ceil and p.get("cavok", True):
        parts.append("CAVOK")
    else:
        parts.append(f"{min(vis, 9999):04d}")
        rvr = p.get("rvr_m")
        if rvr:
            parts += [f"R12L/{int(rvr):04d}N", f"R12R/{int(rvr):04d}N"]
        parts += wx
        if ceil is not None and ceil != "":
            c = int(ceil) // 100
            parts.append(f"VV{c:03d}" if p.get("vv") else f"BKN{c:03d}")
        else:
            parts.append("NSC")
    t, dp = int(p.get("temp_c", 30)), int(p.get("dew_c", 18))
    fmt = lambda x: f"M{abs(x):02d}" if x < 0 else f"{x:02d}"
    parts.append(f"{fmt(t)}/{fmt(dp)}")
    parts.append(f"Q{int(p.get('qnh', 1012))}")
    return " ".join(parts)


class Simulator:
    def __init__(self, client: EngineClient, stands: list[dict]):
        self.client = client
        self.stands_list = stands
        self.params = TrafficParams()
        self.speed = 30
        self.shown_rate: float = 30       # the rate last published to the dashboard
        self.hold_released = False
        self.playing = False
        self.weather_mode = "sim"           # sim | live
        self.chaos = {"enabled": False, "per_hour": 3, "seed": 5}
        self.base_metar = {"wind_dir": 320, "wind_kt": 8, "vis_m": 9999, "wx": [], "temp_c": 31, "dew_c": 18, "qnh": 1011}
        self._ids = itertools.count(1)
        self.scheduled: list[dict] = []
        self.history: list[dict] = []
        self.sent = 0
        self.outage_until: float | None = None
        self.last_metar_t: float | None = None
        self.current_metar: str | None = None
        self.start = epoch_min(datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0))
        self.now = float(self.start)
        self._task: asyncio.Task | None = None
        self.active_scenarios: list[dict] = []
        self.reset_world()

    # ------------------------------------------------------------------ setup
    def reset_world(self) -> None:
        self.rng = random.Random(self.params.seed)
        self.chaos_rng = random.Random(self.chaos["seed"])
        self.namer = Namer(self.rng)
        self.world = World(self.rng, self.namer)
        self.world.turn_departures = self.params.turn_departures
        self.gen = None

    def restart(self, start: int | None = None, params: dict | None = None) -> None:
        if params:
            for k, v in params.items():
                if hasattr(self.params, k):
                    setattr(self.params, k, type(getattr(self.params, k))(v))
        if start is not None:
            self.start = int(start)
        self.playing = False
        self.now = float(self.start)
        self.scheduled.clear()
        self.history.clear()
        self.active_scenarios.clear()
        self.sent = 0
        self.outage_until = None
        self.reset_world()
        self.client.reset()
        self._send([{"type": "CLOCK", "now": self.now, "rate": 0}])
        if self.weather_mode == "sim":
            self._send([{"type": "WEATHER_METAR", "raw": compose_metar(self.now, self.base_metar)}])
            self._send([S.taf(self.now, f"{S._dh(self.now)}/{S._dh(self.now + 1440)} 32010KT CAVOK")])
        self.gen = generate(self.start, self.params, self.stands_list, self.rng, self.namer)
        self._send(self.gen.events)
        self.publish()

    # ------------------------------------------------------------------ transport
    def _send(self, events: list[dict]) -> list[dict]:
        if not events:
            return []
        clean = []
        for e in events:
            if e["type"] == "_SIM_FEED_OUTAGE":
                self.outage_until = self.now + e["minutes"]
                self.history.append({"t": self.now, "label": f"MET feed outage for {e['minutes']} min (sim)", "ok": True})
                continue
            if e["type"] == "WEATHER_METAR":
                if self.outage_until and self.now < self.outage_until:
                    continue
                self.current_metar = e["raw"]
                self.last_metar_t = self.now
            self.world.observe(e)
            clean.append(e)
        if not clean:
            return []
        res = self.client.ingest(clean)
        self.sent += len(clean)
        return res

    def publish(self) -> None:
        self.client.publish({"type": "sim", "state": self.status()})

    # ------------------------------------------------------------------ controls
    def play(self) -> None:
        if self.gen is None:
            self.restart()
        self.playing = True
        self.publish()

    def pause(self) -> None:
        self.playing = False
        self.client.ingest([{"type": "CLOCK", "now": self.now, "rate": 0, "source": "sim"}])
        self.publish()

    def set_speed(self, s: float) -> None:
        self.speed = max(0.5, min(600, float(s)))
        self.hold_released = True      # an explicit speed choice overrides the hold for this decision
        self.publish()

    def rate(self) -> float:
        """Speed the clock actually runs at. While the engine waits for a controller's decision the
        simulation drops to real time, so the world does not run ahead of the solver and the person:
        a plan computed in ten seconds would otherwise be minutes out of date before it could be read."""
        if not self.client.decision_pending():
            self.hold_released = False
            return self.speed
        return self.speed if self.hold_released else min(self.speed, HOLD_SPEED)

    def jump(self, minutes: float) -> None:
        """Fast-forward, executing the world minute by minute."""
        if self.gen is None:
            self.restart()
        was_playing, self.playing = self.playing, False
        target = self.now + max(0, minutes)
        while self.now < target:
            self.now = min(target, self.now + 1)
            self._tick_world()
        self.playing = was_playing
        self.publish()

    # ------------------------------------------------------------------ scheduling
    def schedule(self, event: dict, at: float | None = None, label: str | None = None, group: str | None = None) -> dict:
        item = {"id": next(self._ids), "at": float(at if at is not None else self.now), "event": event,
                "label": label or describe(event), "group": group}
        self.scheduled.append(item)
        self.scheduled.sort(key=lambda x: x["at"])
        self.publish()
        return item

    def cancel(self, sid: int) -> None:
        self.scheduled = [s for s in self.scheduled if s["id"] != sid]
        self.publish()

    def cancel_group(self, group: str) -> None:
        self.scheduled = [s for s in self.scheduled if s.get("group") != group]
        self.active_scenarios = [a for a in self.active_scenarios if a["group"] != group]
        self.publish()

    def flight_ctx(self) -> dict:
        plan = self.client.published_plan()
        flights = {}
        for cs, f in self.world.flights.items():
            r = plan.get(cs, {})
            flights[cs] = {"kind": f["kind"], "status": f["status"], "active": f["status"] not in ("DEPARTED", "CANCELLED", "DIVERTED", "TURNED"),
                           "earliest": int(max(f.get("est") or f["sched"], f.get("tobt") or 0)), "code": code_for(f["aircraft"])[0],
                           "stand": f.get("stand") or r.get("stand")}
        return {"now": self.now, "flights": flights}

    def inject_scenario(self, sid: str, delay: float = 0) -> dict:
        if self.gen is None:
            self.restart()
        at = self.now + delay
        events = S.build(sid, int(at), self.flight_ctx(), random.Random(self.rng.random()))
        meta = next(s for s in S.catalog() if s["id"] == sid)
        group = f"{sid}-{next(self._ids)}"
        for off, ev in events:
            self.schedule(ev, at + off, group=group)
        self.active_scenarios.append({"group": group, "id": sid, "name": meta["name"], "at": at, "until": at + meta["duration"]})
        self.publish()
        return {"group": group, "events": len(events)}

    # ------------------------------------------------------------------ loop
    def _tick_world(self) -> None:
        self._send([{"type": "CLOCK", "now": self.now, "rate": self.rate() / 60 if self.playing else 0}])
        due = [s for s in self.scheduled if s["at"] <= self.now]
        if due:
            self.scheduled = [s for s in self.scheduled if s["at"] > self.now]
            for s in due:
                res = self._send([s["event"]])
                ok = not res or all(r.get("ok", True) for r in res)
                self.history.append({"t": self.now, "label": s["label"], "ok": ok,
                                     "error": None if ok else res[0].get("error")})
            self.history = self.history[-60:]
            self.publish()
        if self.gen is not None:
            evs = self.world.step(self.now, self.client.published_plan(), self.gen.eta_noise, self.gen.tech,
                                  bool(self.outage_until and self.now < self.outage_until))
            if evs:
                self._send(evs)
        # Routine METAR every 30 min (sim weather only)
        if self.weather_mode == "sim" and (self.last_metar_t is None or self.now - self.last_metar_t >= 30):
            if self.current_metar and not (self.outage_until and self.now < self.outage_until):
                raw = self.current_metar
                d = datetime.fromtimestamp(int(self.now) * 60, tz=timezone.utc)
                parts = raw.split()
                parts[2] = f"{d.day:02d}{d.hour:02d}{d.minute:02d}Z"
                self._send([{"type": "WEATHER_METAR", "raw": " ".join(parts)}])
        if self.outage_until and self.now >= self.outage_until:
            self.outage_until = None
            self.history.append({"t": self.now, "label": "MET feed restored (sim)", "ok": True})
        # Chaos
        if self.chaos["enabled"] and self.chaos_rng.random() < self.chaos["per_hour"] / 60 * (1 if self.playing else 0):
            sid = self.chaos_rng.choice(S.CHAOS_POOL)
            try:
                self.inject_scenario(sid)
                self.history.append({"t": self.now, "label": f"Chaos: {sid}", "ok": True})
            except Exception:
                pass

    async def run(self) -> None:
        last = time.perf_counter()
        last_minute = None
        while True:
            await asyncio.sleep(TICK)
            nowp = time.perf_counter()
            dt, last = nowp - last, nowp
            if not self.playing:
                continue
            prev_min = int(self.now)
            rate = self.rate()
            self.now += dt * rate / 60
            if rate != self.shown_rate:
                self.shown_rate = rate
                self.publish()
            if int(self.now) != prev_min or last_minute is None:
                last_minute = int(self.now)
                # World logic runs on whole sim minutes (cheap and deterministic)
                self._tick_world()
            else:
                self.client.ingest([{"type": "CLOCK", "now": self.now, "rate": rate / 60, "source": "sim"}])

    def start_loop(self) -> None:
        if self._task is None:
            self._task = asyncio.get_event_loop().create_task(self.run())

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        return {
            "playing": self.playing, "speed": self.speed, "rate": self.shown_rate, "speeds": SPEEDS, "now": self.now, "start": self.start,
            "end": self.start + int(self.params.duration_h * 60) + 60, "params": self.params.to_dict(),
            "weather_mode": self.weather_mode, "chaos": self.chaos, "base_metar": self.base_metar,
            "current_metar": self.current_metar, "outage_until": self.outage_until,
            "scheduled": [{k: v for k, v in s.items() if k != "event"} | {"type": s["event"]["type"]} for s in self.scheduled[:80]],
            "history": self.history[-30:][::-1], "active_scenarios": self.active_scenarios[-10:],
            "sent": self.sent, "started": self.gen is not None,
        }


def describe(ev: dict) -> str:
    t = ev["type"]
    if t == "WEATHER_METAR":
        return "METAR " + " ".join(ev["raw"].split()[3:9])
    if t == "WEATHER_TAF":
        return "TAF issued"
    if t == "RUNWAY_STATUS":
        return f"Runway {ev['runway']} {ev['status'].lower()}" + (f" (ILS {ev['ils_cat']})" if ev.get("ils_cat") else "")
    if t == "STAND_STATUS":
        return f"Stand {ev['stand']} {ev['status'].lower()}"
    if t == "TAXIWAY_STATUS":
        return f"Taxiway {ev['taxiway']} {ev['status'].lower()}"
    if t == "LIGHTNING_ALERT":
        return f"Lightning {ev['level']}"
    if t == "GROUND_STOP":
        return "Ground stop " + ("ON" if ev["active"] else "lifted")
    if t == "FLOW_RESTRICTION":
        return f"ATFM {ev['kind']} {ev.get('max_per_hour') or 'lifted'}/h"
    if t == "AIRSPACE_CLOSURE":
        return f"Airspace {'closed' if ev.get('active', True) else 'reopened'}: {', '.join(ev['regions'])}"
    if t == "FLIGHT_UPSERT":
        return f"New flight {ev['callsign']} ({ev['kind']} {ev['aircraft']}{', ' + ev['priority'].upper() if ev.get('priority') not in (None, 'normal') else ''})"
    if t == "FLIGHT_UPDATE":
        bits = [k for k in ("est", "tobt", "priority", "status") if ev.get(k) is not None]
        return f"{ev['callsign']} update: {', '.join(bits)}" + (f" ({ev['reason']})" if ev.get("reason") else "")
    if t in ("OPERATOR_NOTE", "NOTAM"):
        return f"{t.replace('_', ' ').title()}: {ev['text'][:50]}"
    if t == "_SIM_FEED_OUTAGE":
        return f"MET feed outage {ev['minutes']} min"
    return t
