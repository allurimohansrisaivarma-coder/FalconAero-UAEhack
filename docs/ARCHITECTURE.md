# FalconAero v2: Production Architecture

## Principle
The **FalconAero Engine** is the product. It knows nothing about the simulator.
It only receives **operational events** through one ingestion interface, the same way
it would receive data from an airport's AODB / A-CDM, the MET office, NOTAM feeds or
operators in a real deployment. The **Simulator** is only one event source among several.

```
 ┌──────────── Event sources ────────────┐        ┌──────────────── FalconAero Engine ────────────────┐
 │ Simulator (traffic, scenarios, chaos) │──┐     │ Ingest ─▶ World state (event-sourced)              │
 │ Live weather adapter (OMDB METAR/TAF) │──┼──▶  │   ├─ Rules: conditions ─▶ capacity profile (+TAF)  │
 │ Operator notes / NOTAM text (via AI)  │──┤     │   ├─ Forecast: demand vs capacity, holding, stands │
 │ Manual events (API / UI)              │──┘     │   ├─ Validator: conflicts in the approved plan     │
 └───────────────────────────────────────┘        │   ├─ Optimizer: CP-SAT recovery (3 strategies)     │
                                                  │   ├─ Explainer + LLM (Groq): briefings, Q&A, parse │
                                                  │   └─ Notification engine ─▶ admin Notifications tab│
                                                  └────────────────────────────────────────────────────┘
```

## Event types (ingestion API: `POST /api/events`)
| Type | Purpose |
|---|---|
| `CLOCK` | Time source when an external clock (simulator / replay) drives the engine |
| `FLIGHT_UPSERT` | New or updated scheduled flight (schedule feed) |
| `FLIGHT_UPDATE` | ETA/ETD/TOBT estimate, priority, status, actual milestones (ALDT, AIBT, AOBT, ATOT) |
| `RUNWAY_STATUS` | Open / closed / restricted (rate factor, ILS category), reason, until |
| `STAND_STATUS` | Available / unavailable (jet bridge, VDGS, flooding), until |
| `TAXIWAY_STATUS` | Taxiway segment closures that change routing |
| `WEATHER_METAR` / `WEATHER_TAF` | Raw METAR/TAF, parsed by the engine |
| `LIGHTNING_ALERT` | CLEAR / AMBER / RED (RED = ramp operations suspended) |
| `GROUND_STOP` | No departures until a time (e.g. drone sighting, security) |
| `FLOW_RESTRICTION` | ATFM/ATC hourly cap for arrivals or departures |
| `AIRSPACE_CLOSURE` | Inbound/outbound traffic via affected regions held (e.g. June 2025 Gulf closures) |
| `OPERATOR_NOTE` / `NOTAM` | Free text; the AI turns it into structured events for confirmation |

## Assess → Predict → Forecast
- **Assess**: current METAR → flight category, LVP (RVR < 550 m / ceiling < 200 ft), CAT II/III,
  crosswind on 12/30, TS/heavy rain/dust effects → runway occupancy, taxi speed, ramp status.
- **Predict**: the approved plan is re-validated on every event, which yields live conflicts.
- **Forecast**: TAF timeline + known restriction end times give a capacity profile in 15-min bins
  over 6 h. Demand (current ETAs/ETDs) is compared with it through a queue model to predict holding
  and delay, plus a stand-demand forecast per aircraft size class.

## Plan → Approve
- **Baseline**: a first-come-first-served plan under the same operating rules. It is the comparison
  point and the optimiser's starting solution.
- **Optimiser**: CP-SAT, two stages. Emergency delay is minimised first and locked; the rest of the
  objective is then improved for a fixed time budget (anytime search, not a proven optimum).
- **Stress test**: each option is re-validated under the TAF worst case (TEMPO / PROB / BECMG).
- **KPIs**: delay covers served flights; diverted and unallocated flights are reported as *unserved*
  and ranked before delay, so a plan cannot look better by dropping flights.
- **Authority**: recovery plans need an explicit approve or reject (both logged, approve can be
  undone). Routine upkeep (new flights, ETA/TOBT drift) is automatic only within limits and can be
  switched off in the dashboard. A new operating condition (fog, closure, cap) is never absorbed by
  upkeep: it is routed to the controller as a recovery plan.
- **Simulator honesty**: simulated aircraft obey low-visibility spacing and take-off minima whatever
  the plan says, and the simulation drops to real time while a decision is pending.

Model, rules, limits and expected questions are in [DESIGN.md](DESIGN.md).

## Notifications
A rule engine raises, updates and auto-resolves notifications (deduplicated by key) for conflicts,
forecast shortfalls, weather deterioration, resource changes, emergencies, stale data, and
"recovery plan ready". The admin acknowledges them in the Notifications tab.

## AI layer (Groq, OpenAI-compatible)
- `.env`: `GROQ_API_KEY`, `GROQ_MODEL` (configurable).
- Parses NOTAM / operator text into structured events (the user confirms before they are ingested).
- Writes situation briefings and answers questions through read-only tools over live state.
- Never creates assignments. Those come only from the CP-SAT optimizer under hard safety constraints.
- Without a key or connectivity, it falls back to rule-based parsing and templates.

## Simulator (separate package, talks only through the ingestion interface)
Clock (play/pause/speed/restart/jump), traffic generator (rates, fleet mix, waves, punctuality,
seed), world executor (aircraft follow the approved plan; physical blocks such as closed runways
and occupied stands cause real knock-on delays), a researched scenario library, event builder,
METAR composer, chaos mode and a scheduled-event queue. UI: one floating player that minimises
to a circle and maximises into the Scenario Studio.
