# FalconAero: Intelligent Airport Disruption Recovery

UAE Hackathon 2026 · Youth Track (Pioneers) · Challenge 3 (GCAA) · Team **Falconix**

FalconAero is an **event-driven decision-support engine** for an Airport Operations Centre. It ingests
operational events (schedules, ETAs/TOBTs, METAR/TAF, runway/stand/taxiway status, lightning, ground
stops, ATFM caps, airspace closures, NOTAMs), then:

- **assesses** the current conditions: LVP / CAT II/III, crosswind, runway modes, capacity;
- **validates** the approved plan continuously and raises conflicts;
- **forecasts** demand against capacity for 6 h (METAR nowcast, then TAF, plus a worst-case band) and gives early warnings;
- **re-plans** with OR-Tools CP-SAT (runway slots and stands together, under hard safety constraints,
  emergencies first), next to a rule-based baseline that obeys the same rules;
- **stress-tests** each option against the TAF worst case;
- **notifies** the admin in the Notifications tab;
- **explains** every change, with an optional Groq LLM for briefings, NOTAM parsing and Q&A.

Recovery plans are never applied without approval. Routine upkeep can run automatically within limits
or be switched off in the Alerts panel.

The **simulator is a separate event source**: it only sends events, exactly like a production feed would.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the structure and [docs/DESIGN.md](docs/DESIGN.md)
for the model, its known limits and the questions we expect.

Traffic in the demo is **simulated**. Runway, stand and weather rules are planning assumptions from
public guidance, not certified values.

## Run it (Windows)

```bash
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python -m uvicorn api.main:app --port 8000
```

Put your Groq key in `backend/.env` (`GROQ_API_KEY=...`). Without it, the AI layer falls back to rules and templates.
`GROQ_MODEL` is optional: if the key cannot use the configured model, the engine picks one it can.
`backend/.env` is in `.gitignore`: leave it out of any zip or upload.

Dashboard, in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. For a single-server demo, run `npm run build` and open http://localhost:8000.

Tests (engine is tested only through events):

```bash
cd backend
.venv\Scripts\python -m pytest -q tests
```

## Feeding real data

Every source uses the same endpoint: `POST /api/events` with `{"events": [...]}`.
`GET /api/events/types` returns the JSON schema. Live OMDB weather (NOAA aviationweather.gov) can be
switched on in Scenario Studio → Weather composer.

## Using the simulator

The floating player (bottom right) minimises to a circle and maximises into the **Scenario Studio**:
playback, 23 researched scenarios (fog, shamal, thunderstorm/lightning, Apr-2024-style flooding,
heat, crosswind, FOD, bird strike, disabled aircraft, ILS failure, jet-bridge faults, taxiway works,
Jun-2025-style airspace closure, ATFM, drone, security, medical/full emergency, A380 AOG, bunching,
cancellations, MET feed outage, compound), a METAR composer with live engine preview, an event
builder for every event type, a traffic generator, chaos mode and the event queue.

Shortcuts: `Space` play/pause · `R` review plan · `1/2/3` pick · `C` compare · `A` approve (only when the plan is
current and has no critical issue) · `M` airspace / surface map · `N` notifications · `Esc` close.

The map opens on the **airspace view**: a 40 NM radar-style picture with arrival and departure routes and a data
block per aircraft. Positions are projected from the engine's runway times onto illustrative routes; it is a
schematic, not surveillance data. `M` switches to the airport surface.

While the engine is waiting for your decision the simulation drops to real time, so the plan you are reading stays
current. Pick a speed to override.

## Layout

```
backend/engine/   the product: events, state, rules, planning, optimizer, forecast, notify, llm, feeds
backend/sim/      the simulator: traffic, world executor, scenarios, runner (talks via EngineClient)
backend/api/      FastAPI: /api (engine), /sim (simulator), /api/stream (SSE)
backend/data/airports/omdb.json   data-driven airport definition
frontend/src/     React dashboard (Operations + Notifications tabs, floating sim player)
```
