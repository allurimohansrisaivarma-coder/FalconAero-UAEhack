"""FalconAero API: engine endpoints (/api), simulator endpoints (/sim), live stream (SSE)."""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

from engine import llm  # noqa: E402
from engine.core import Engine  # noqa: E402
from engine.events import EVENT_TYPES, Event  # noqa: E402
from engine.feeds import LiveWeather  # noqa: E402
from sim import scenarios as SC  # noqa: E402
from sim.client import InProcessClient  # noqa: E402
from sim.runner import Simulator, compose_metar  # noqa: E402

engine = Engine("omdb")
sim = Simulator(InProcessClient(engine), engine.airport.doc["stands"])
live_wx = LiveWeather(engine)
_EV = TypeAdapter(Event)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sim.start_loop()
    sim.restart()
    yield


# No CORS middleware on purpose: the dashboard is served from this origin (or through the Vite
# dev proxy), so no other site can drive the event or approval endpoints from a browser.
app = FastAPI(title="FalconAero", version="2.0.0", lifespan=lifespan)


def _bad(ex: Exception):
    raise HTTPException(400, str(ex))


# =============================================================== ENGINE (product)
@app.get("/api/layout")
def layout():
    return engine.airport.layout()


@app.get("/api/state")
def state():
    return engine.snapshot()


class EventsIn(BaseModel):
    events: list[dict]


@app.post("/api/events")
def ingest(body: EventsIn):
    """The single ingestion endpoint for every data source (AODB, MET, NOTAM, operator, simulator)."""
    parsed, errors = [], []
    for i, e in enumerate(body.events):
        try:
            parsed.append(_EV.validate_python({"source": "api", **e}))
        except ValidationError as ex:
            errors.append({"index": i, "error": ex.errors()[0]["msg"], "loc": ex.errors()[0]["loc"]})
    if errors:
        raise HTTPException(422, {"errors": errors})
    return {"results": engine.ingest(parsed)}


@app.get("/api/events/types")
def event_types():
    return {"types": EVENT_TYPES, "schema": _EV.json_schema()}


@app.get("/api/notifications")
def notifications():
    return {"items": engine.notes.listing(), "summary": engine.notes.summary()}


@app.post("/api/notifications/{nid}/ack")
def ack(nid: int):
    with engine.lock:
        n = engine.notes.ack(nid)
    if not n:
        raise HTTPException(404, "Unknown notification")
    engine.publish({"type": "state", "version": engine.version})
    return n


@app.post("/api/notifications/ack_all")
def ack_all():
    with engine.lock:
        engine.notes.ack_all()
    engine.publish({"type": "state", "version": engine.version})
    return {"ok": True}


@app.post("/api/plans")
async def make_plans():
    return await asyncio.to_thread(engine.generate_plans, False)


@app.get("/api/plans/latest")
def latest_plans():
    return engine.options or {}


class ApproveIn(BaseModel):
    by: str = "AOC Duty Manager"


@app.post("/api/plans/{option_id}/approve")
def approve(option_id: str, body: ApproveIn | None = None):
    try:
        engine.approve(option_id, (body or ApproveIn()).by)
    except ValueError as ex:
        _bad(ex)
    return {"ok": True}


@app.post("/api/plans/reject")
def reject(body: ApproveIn | None = None):
    try:
        engine.reject_plans((body or ApproveIn()).by)
    except ValueError as ex:
        _bad(ex)
    return {"ok": True}


@app.post("/api/undo")
def undo():
    try:
        engine.undo()
    except ValueError as ex:
        _bad(ex)
    return {"ok": True}


class SettingsIn(BaseModel):
    auto_allocate: bool | None = None
    auto_recovery: bool | None = None


@app.post("/api/settings")
def settings(body: SettingsIn):
    with engine.lock:
        for k, v in body.model_dump(exclude_none=True).items():
            engine.settings[k] = v
    engine.recompute()
    return engine.settings


class TextIn(BaseModel):
    text: str


@app.post("/api/ai/parse")
async def ai_parse(body: TextIn):
    """Free text (NOTAM / operator note) → proposed events. Nothing is ingested until confirmed."""
    return await llm.parse_text(body.text, engine.state.now, engine.known())


@app.get("/api/ai/brief")
async def ai_brief():
    facts, fallback = engine.brief_facts()
    return await llm.brief(facts, fallback)


class AskIn(BaseModel):
    question: str
    history: list[dict] = []


@app.post("/api/ai/ask")
async def ai_ask(body: AskIn):
    try:
        return await llm.ask(body.question, engine.tool, body.history)
    except Exception as ex:
        return {"text": f"AI request failed: {str(ex)[:200]}", "engine": "error", "tools": []}


@app.get("/api/ai/status")
def ai_status():
    return llm.status()


class LiveIn(BaseModel):
    enabled: bool


@app.post("/api/feeds/weather")
async def feed_weather(body: LiveIn):
    live_wx.set(body.enabled)
    sim.weather_mode = "live" if body.enabled else "sim"
    if body.enabled:
        try:
            await live_wx.fetch_once()
        except Exception as ex:
            live_wx.last["error"] = str(ex)[:200]
    sim.publish()
    return live_wx.status()


@app.get("/api/feeds/weather")
def feed_weather_status():
    return live_wx.status()


@app.post("/api/admin/reset")
def admin_reset():
    engine.reset()
    return {"ok": True}


@app.get("/api/stream")
async def stream():
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    engine.subscribe(loop, q)

    async def gen():
        try:
            yield f"event: state\ndata: {json.dumps(engine.snapshot(), default=str)}\n\n"
            yield f"event: sim\ndata: {json.dumps(sim.status(), default=str)}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                batch = [msg]
                while not q.empty():
                    batch.append(q.get_nowait())
                kinds = {m["type"] for m in batch}
                if "state" in kinds or "planning" in kinds:
                    yield f"event: state\ndata: {json.dumps(engine.snapshot(), default=str)}\n\n"
                elif "clock" in kinds:
                    c = next(m for m in reversed(batch) if m["type"] == "clock")
                    yield f"event: clock\ndata: {json.dumps(c)}\n\n"
                if "sim" in kinds:
                    s = next(m for m in reversed(batch) if m["type"] == "sim")
                    yield f"event: sim\ndata: {json.dumps(s['state'], default=str)}\n\n"
        finally:
            engine.unsubscribe(loop, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# =============================================================== SIMULATOR (separate source)
@app.get("/sim/state")
def sim_state():
    return sim.status()


@app.get("/sim/scenarios")
def sim_scenarios():
    return {"categories": SC.CATEGORIES, "items": SC.catalog()}


class SpeedIn(BaseModel):
    speed: float


@app.post("/sim/play")
def sim_play():
    sim.play()
    return sim.status()


@app.post("/sim/pause")
def sim_pause():
    sim.pause()
    return sim.status()


@app.post("/sim/speed")
def sim_speed(body: SpeedIn):
    sim.set_speed(body.speed)
    return sim.status()


class RestartIn(BaseModel):
    start: int | None = None
    params: dict | None = None


@app.post("/sim/restart")
def sim_restart(body: RestartIn | None = None):
    b = body or RestartIn()
    sim.restart(b.start, b.params)
    return sim.status()


class JumpIn(BaseModel):
    minutes: float


@app.post("/sim/jump")
def sim_jump(body: JumpIn):
    sim.jump(min(body.minutes, 240))
    return sim.status()


class InjectIn(BaseModel):
    scenario: str
    delay: float = 0


@app.post("/sim/scenario")
def sim_inject(body: InjectIn):
    try:
        return sim.inject_scenario(body.scenario, body.delay)
    except KeyError:
        raise HTTPException(404, "Unknown scenario")


class EventIn(BaseModel):
    event: dict
    delay: float = 0


@app.post("/sim/event")
def sim_event(body: EventIn):
    e = dict(body.event)
    if e.get("type") != "_SIM_FEED_OUTAGE":
        try:
            _EV.validate_python({"source": "sim", **e})
        except ValidationError as ex:
            raise HTTPException(422, ex.errors()[0]["msg"])
    return sim.schedule(e, sim.now + body.delay)


class MetarIn(BaseModel):
    fields: dict | None = None
    raw: str | None = None
    delay: float = 0


@app.post("/sim/weather")
def sim_weather(body: MetarIn):
    raw = body.raw or compose_metar(sim.now + body.delay, body.fields or {})
    kind = "WEATHER_TAF" if raw.strip().upper().startswith("TAF") else "WEATHER_METAR"
    if body.fields:
        sim.base_metar = {**sim.base_metar, **body.fields}
    return sim.schedule({"type": kind, "raw": raw}, sim.now + body.delay)


@app.post("/sim/metar/preview")
def sim_metar_preview(body: MetarIn):
    from engine.rules import ops_at
    from engine.weather import parse_metar
    raw = body.raw or compose_metar(sim.now, body.fields or {})
    cond, _ = parse_metar(raw)
    with engine.lock:
        o = ops_at(engine.state, engine.state.tnow, cond, "PREVIEW")
    return {"raw": raw, "category": o.category, "lvp": o.lvp, "required_ils": o.required_ils,
            "runways": {k: {"mode": v.mode, "occ_arr": v.occ_arr, "occ_dep": v.occ_dep, "reasons": v.reasons} for k, v in o.runways.items()},
            "advisories": o.advisories, "taxi_factor": o.taxi_factor}


@app.delete("/sim/scheduled/{sid}")
def sim_cancel(sid: int):
    sim.cancel(sid)
    return sim.status()


@app.delete("/sim/group/{group}")
def sim_cancel_group(group: str):
    sim.cancel_group(group)
    return sim.status()


class ChaosIn(BaseModel):
    enabled: bool
    per_hour: float = 3
    seed: int | None = None


@app.post("/sim/chaos")
def sim_chaos(body: ChaosIn):
    sim.chaos.update(enabled=body.enabled, per_hour=body.per_hour)
    if body.seed is not None:
        sim.chaos["seed"] = body.seed
    sim.publish()
    return sim.status()


_dist = ROOT.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="dashboard")
