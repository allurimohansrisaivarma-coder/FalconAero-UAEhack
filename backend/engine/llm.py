"""AI layer on Groq (OpenAI-compatible chat completions) with deterministic fallbacks.

The model never creates assignments. It (1) turns free text such as NOTAMs and
operator notes into *proposed* structured events that a human confirms, (2) writes
situation briefings from verified structured data, and (3) answers questions
through read-only tools over the live engine state.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import httpx
from pydantic import TypeAdapter, ValidationError

from .events import Event

GROQ_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
_EVENT = TypeAdapter(Event)


DEFAULT_MODEL = "openai/gpt-oss-120b"
# Tried in this order when the configured model is not available to the key
PREFERRED = (DEFAULT_MODEL, "openai/gpt-oss-20b", "llama-3.3-70b-versatile")
NOT_CHAT = ("whisper", "guard", "orpheus", "tts", "safeguard")
_replacement: dict[str, str] = {}      # configured model -> the one actually used, once it had to be replaced


def _cfg() -> tuple[str | None, str]:
    model = os.getenv("GROQ_MODEL") or DEFAULT_MODEL
    return os.getenv("GROQ_API_KEY"), _replacement.get(model, model)


def status() -> dict:
    key, model = _cfg()
    return {"enabled": bool(key), "provider": "groq", "model": model if key else None}


async def _available_model(client: httpx.AsyncClient, key: str) -> str | None:
    """A chat model this key can use: a preferred one if listed, else the one with the largest context."""
    r = await client.get(f"{GROQ_URL}/models", headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        return None
    models = [m for m in r.json().get("data", []) if m.get("active", True) and not any(x in m["id"] for x in NOT_CHAT)]
    ids = {m["id"] for m in models}
    for name in PREFERRED:
        if name in ids:
            return name
    return max(models, key=lambda m: m.get("context_window") or 0)["id"] if models else None


async def chat(messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False,
               temperature: float = 0.2, max_tokens: int = 1500) -> dict:
    key, model = _cfg()
    if not key:
        raise RuntimeError("GROQ_API_KEY not set")
    body: dict = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=30) as c:
        headers = {"Authorization": f"Bearer {key}"}
        for _ in range(2):
            if body["model"].startswith("openai/gpt-oss"):
                body["reasoning_effort"] = "low"     # reasoning tokens count against max_tokens
            r = await c.post(f"{GROQ_URL}/chat/completions", headers=headers, json=body)
            gone = r.status_code in (400, 404) and ("does not exist" in r.text or "decommissioned" in r.text)
            other = await _available_model(c, key) if gone else None
            if not other or other == body["model"]:
                break
            _replacement[os.getenv("GROQ_MODEL") or DEFAULT_MODEL] = body["model"] = other
        if r.status_code >= 400:
            raise RuntimeError(f"Groq {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]


# ---------------------------------------------------------------- time helpers
def _clock_to_epoch(hhmm: str, now: float) -> int:
    h, mi = int(hhmm[:2]), int(hhmm[2:])
    day = int(now // 1440) * 1440
    t = day + h * 60 + mi
    if t < now - 120:
        t += 1440
    return t


def _until(text: str, now: float) -> int | None:
    m = re.search(r"(?:until|till|til|to|-)\s*(\d{4})\s*z?", text, re.I)
    if m:
        return _clock_to_epoch(m.group(1), now)
    m = re.search(r"for\s+(\d+)\s*(min|mins|minutes|h|hr|hrs|hours)", text, re.I)
    if m:
        n = int(m.group(1))
        return int(now + (n * 60 if m.group(2).lower().startswith("h") else n))
    return None


# ---------------------------------------------------------------- fallback parser
def heuristic_parse(text: str, now: float, known: dict) -> list[dict]:
    """Deterministic parser for common NOTAM / operator phrasing."""
    out: list[dict] = []
    for line in [l.strip() for l in re.split(r"[\n;]+", text) if l.strip()]:
        up = line.upper()
        until = _until(line, now)
        if up.startswith(("METAR", "SPECI")) or re.match(r"^[A-Z]{4} \d{6}Z ", up) and "TAF" not in up:
            out.append({"type": "WEATHER_METAR", "raw": line})
            continue
        if up.startswith("TAF"):
            out.append({"type": "WEATHER_TAF", "raw": line})
            continue
        rw = re.search(r"\b(?:RWY|RUNWAY)\s*(\d{2}[LRC]?)", up)
        if rw:
            rid = rw.group(1)
            rid = known["runway_alias"].get(rid, rid)
            if re.search(r"\b(CLSD|CLOSED|CLOSE|CLOSURE|U/S|BLOCKED|DISABLED)\b", up):
                out.append({"type": "RUNWAY_STATUS", "runway": rid, "status": "CLOSED", "reason": line[:80], "until": until})
            elif re.search(r"\b(OPEN|REOPEN|REOPENED|AVBL)\b", up):
                out.append({"type": "RUNWAY_STATUS", "runway": rid, "status": "OPEN", "reason": line[:80]})
            elif m := re.search(r"ILS\s*(?:CAT\s*)?(I{1,3}|NONE|U/S|FAIL)", up):
                cat = m.group(1)
                cat = "NONE" if cat in ("U/S", "FAIL") else cat
                out.append({"type": "RUNWAY_STATUS", "runway": rid, "status": "RESTRICTED", "rate_factor": 1.0,
                            "ils_cat": cat, "reason": line[:80], "until": until})
            elif m := re.search(r"(\d{2})\s*%", up):
                out.append({"type": "RUNWAY_STATUS", "runway": rid, "status": "RESTRICTED",
                            "rate_factor": int(m.group(1)) / 100, "reason": line[:80], "until": until})
            continue
        st = re.search(r"\b(?:STAND|STD|GATE|BAY)\s*([A-Z]\d{1,2})\b", up)
        if st and st.group(1) in known["stands"]:
            avail = re.search(r"\b(AVAILABLE|AVBL|SERVICEABLE|RESTORED|FIXED)\b", up)
            out.append({"type": "STAND_STATUS", "stand": st.group(1), "status": "AVAILABLE" if avail else "UNAVAILABLE",
                        "reason": line[:80], "until": until})
            continue
        tw = re.search(r"\b(?:TWY|TAXIWAY)\s*([A-Z]\d?)\b", up)
        if tw and tw.group(1) in known["taxiways"]:
            out.append({"type": "TAXIWAY_STATUS", "taxiway": tw.group(1),
                        "status": "OPEN" if re.search(r"\b(OPEN|REOPEN)", up) else "CLOSED", "reason": line[:80], "until": until})
            continue
        if "LIGHTNING" in up or "THUNDERSTORM WARNING" in up:
            lvl = "CLEAR" if re.search(r"CLEAR|ALL[- ]CLEAR|LIFTED", up) else "AMBER" if "AMBER" in up else "RED"
            out.append({"type": "LIGHTNING_ALERT", "level": lvl, "until": until, "reason": line[:80]})
            continue
        if "GROUND STOP" in up or "DRONE" in up or "UAS" in up:
            lifted = bool(re.search(r"LIFT|CANCEL|RESUME", up))
            scope = "ALL" if ("DRONE" in up or "UAS" in up or "ALL" in up) else "DEP"
            out.append({"type": "GROUND_STOP", "active": not lifted, "scope": scope, "until": until or (None if lifted else int(now + 30)), "reason": line[:80]})
            continue
        m = re.search(r"\b(ARR|ARRIVALS?|DEP|DEPARTURES?)\b.*?(\d{1,2})\s*(?:/H|PER\s*H|PER\s*HOUR|PH)", up)
        if m:
            out.append({"type": "FLOW_RESTRICTION", "kind": "ARR" if m.group(1).startswith("ARR") else "DEP",
                        "max_per_hour": int(m.group(2)), "until": until, "reason": line[:80]})
            continue
        if "AIRSPACE" in up:
            regions = [r for r in ("NORTH", "SOUTH", "EAST", "WEST") if r in up] or ["ALL"]
            reopened = bool(re.search(r"REOPEN|OPENED|RESUME", up))
            out.append({"type": "AIRSPACE_CLOSURE", "regions": regions, "active": not reopened, "until": until, "reason": line[:80]})
            continue
        cs = next((c for c in known["flights"] if re.search(rf"\b{re.escape(c)}\b", up)), None)
        if cs:
            if re.search(r"MAYDAY|EMERGENCY|MEDICAL|PAN|LOW FUEL|FUEL", up):
                out.append({"type": "FLIGHT_UPDATE", "callsign": cs, "priority": "emergency" if re.search(r"MAYDAY|EMERGENCY|MEDICAL", up) else "high", "reason": line[:80]})
            elif re.search(r"CANCEL", up):
                out.append({"type": "FLIGHT_UPDATE", "callsign": cs, "status": "CANCELLED", "reason": line[:80]})
            elif m := re.search(r"TOBT\s*(\d{4})", up):
                out.append({"type": "FLIGHT_UPDATE", "callsign": cs, "tobt": _clock_to_epoch(m.group(1), now), "reason": line[:80]})
            elif m := re.search(r"ETA\s*(\d{4})", up):
                out.append({"type": "FLIGHT_UPDATE", "callsign": cs, "est": _clock_to_epoch(m.group(1), now), "reason": line[:80]})
            elif m := re.search(r"(?:DELAY(?:ED)?|LATE)\D*(\d{1,3})\s*MIN", up):
                f = known["flights"][cs]
                key = "tobt" if f["kind"] == "DEP" else "est"
                out.append({"type": "FLIGHT_UPDATE", "callsign": cs, key: int(max(f["earliest"], now) + int(m.group(1))), "reason": line[:80]})
    return out


def _validate(raw: list[dict]) -> tuple[list[dict], list[str]]:
    ok, errors = [], []
    for e in raw:
        e = {k: v for k, v in e.items() if v is not None}
        e.setdefault("source", "ai")
        try:
            ev = _EVENT.validate_python(e)
            ok.append(ev.model_dump(exclude_none=True))
        except ValidationError as ex:
            errors.append(f"{e.get('type', '?')}: {ex.errors()[0]['msg']}")
    return ok, errors


SCHEMA_HINT = """Event types (JSON objects, times are epoch MINUTES UTC as integers):
RUNWAY_STATUS {runway, status: OPEN|CLOSED|RESTRICTED, rate_factor 0-1, ils_cat: I|II|III|NONE, reason, until}
STAND_STATUS {stand, status: AVAILABLE|UNAVAILABLE, reason, until}
TAXIWAY_STATUS {taxiway, status: OPEN|CLOSED, reason, until}
WEATHER_METAR {raw}  WEATHER_TAF {raw}
LIGHTNING_ALERT {level: CLEAR|AMBER|RED, until, reason}
GROUND_STOP {active: bool, scope: DEP|ALL, until, reason}
FLOW_RESTRICTION {kind: ARR|DEP, max_per_hour (int or null to lift), until, reason}
AIRSPACE_CLOSURE {regions: [NORTH|SOUTH|EAST|WEST|ALL], active, until, reason}
FLIGHT_UPDATE {callsign, est, tobt, priority: normal|high|emergency, status: CANCELLED|DIVERTED, reason}
Only use runways/stands/taxiways/callsigns that exist. Omit unknown fields."""


async def parse_text(text: str, now: float, known: dict) -> dict:
    """Return {events, errors, summary, engine: 'llm'|'rules'}."""
    if status()["enabled"]:
        try:
            dt = datetime.fromtimestamp(now * 60, tz=timezone.utc)
            sys = ("You convert airport NOTAMs and operator notes into structured events for an airport "
                   "operations engine. Reply with JSON {\"events\": [...], \"summary\": \"one sentence\"}. "
                   + SCHEMA_HINT +
                   f"\nNow: {dt:%Y-%m-%d %H:%M}Z = epoch minute {int(now)}. "
                   f"Runways: {', '.join(known['runways'])} (aliases {json.dumps(known['runway_alias'])}). "
                   f"Stands: {', '.join(known['stands'])}. Taxiways: {', '.join(known['taxiways'])}. "
                   f"Flights: {', '.join(list(known['flights'])[:80])}.")
            msg = await chat([{"role": "system", "content": sys}, {"role": "user", "content": text}], json_mode=True)
            data = json.loads(msg.get("content") or "{}")
            ok, errors = _validate(data.get("events", []))
            if ok or not errors:
                return {"events": ok, "errors": errors, "summary": data.get("summary", ""), "engine": "llm"}
        except Exception as ex:  # fall back to rules
            fallback_reason = str(ex)[:160]
        else:
            fallback_reason = "LLM returned no valid events"
    else:
        fallback_reason = "GROQ_API_KEY not set"
    ok, errors = _validate(heuristic_parse(text, now, known))
    return {"events": ok, "errors": errors, "engine": "rules",
            "summary": f"Rule-based parser ({fallback_reason}).", }


async def brief(facts: dict, fallback: str) -> dict:
    if not status()["enabled"]:
        return {"text": fallback, "engine": "template"}
    try:
        msg = await chat([
            {"role": "system", "content": "You are the duty assistant in an Airport Operations Centre. Write a concise "
             "situation briefing (max 6 short sentences) for the AOC duty manager from the JSON facts. Use only the facts given, "
             "cite times in HHMMZ, lead with safety-critical items, then capacity outlook, then recommended next action. "
             "Never invent flights or numbers. Plain text only: no markdown, no asterisks, no lists."},
            {"role": "user", "content": json.dumps(facts, default=str)[:12000]},
        ], temperature=0.2, max_tokens=900)
        return {"text": (msg.get("content") or fallback).strip(), "engine": "llm"}
    except Exception as ex:
        return {"text": fallback, "engine": f"template ({str(ex)[:80]})"}


TOOLS = [
    {"type": "function", "function": {"name": "get_situation", "description": "Current conditions, runway modes, restrictions, KPIs and open issues.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_flight", "description": "Plan and status for one flight by callsign.", "parameters": {"type": "object", "properties": {"callsign": {"type": "string"}}, "required": ["callsign"]}}},
    {"type": "function", "function": {"name": "list_flights", "description": "Upcoming flights, optionally only delayed ones.", "parameters": {"type": "object", "properties": {"delayed_only": {"type": "boolean"}, "kind": {"type": "string", "enum": ["ARR", "DEP"]}}}}},
    {"type": "function", "function": {"name": "get_forecast", "description": "Demand vs capacity forecast per 15 min and predictive alerts.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_recovery_options", "description": "Latest generated recovery plans with KPIs and changes.", "parameters": {"type": "object", "properties": {}}}},
]


async def ask(question: str, tool_impl, history: list[dict] | None = None) -> dict:
    if not status()["enabled"]:
        return {"text": "The AI assistant needs a Groq API key. Add GROQ_API_KEY to backend/.env and restart the API.",
                "engine": "disabled", "tools": []}
    msgs = [{"role": "system", "content": "You are FalconAero's operations assistant for an Airport Operations Centre (simulated OMDB). "
             "Answer using the tools; never guess flights, times or numbers. Times as HHMMZ. Be brief and operational: "
             "at most six short lines of plain text. No markdown, no tables, no asterisks or headings. "
             "You cannot change the plan: recommend the controller reviews/approves a recovery plan when relevant."}]
    msgs += (history or [])[-6:]
    msgs.append({"role": "user", "content": question})
    used = []
    for _ in range(5):
        msg = await chat(msgs, tools=TOOLS, temperature=0.1)
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"text": (msg.get("content") or "").strip(), "engine": "llm", "tools": used}
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            used.append(name)
            result = tool_impl(name, args)
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, default=str)[:9000]})
    return {"text": "I could not complete the analysis within the tool-call limit.", "engine": "llm", "tools": used}
