# FalconAero: Design (as built)

> UAE Hackathon 2026 · Youth Track · **Pioneers** · Challenge 3 (GCAA) · Team Falconix
> Submission hard close: **11 Nov 10:00**. System structure is in [ARCHITECTURE.md](ARCHITECTURE.md); this file covers the decisions, the model and its limits.

---

## 1. Decisions

| Topic | Decision |
|---|---|
| Stack | React (Vite + TypeScript) dashboard, FastAPI (Python) engine |
| Optimiser | Google OR-Tools CP-SAT: runway slots and stands in one model |
| Airport | Schematic DXB: two parallel runways (12L/30R, 12R/30L), 20 stands (2 Code F), a taxiway graph |
| AI layer | Template explanations always on. Optional Groq LLM (`openai/gpt-oss-120b` by default; if the key cannot use the configured model the engine picks an available one) for briefings, NOTAM parsing and Q&A. The demo never depends on Wi-Fi |
| Primary user | Airport Operations Centre (AOC) duty manager, acting as the A-CDM co-ordinator |
| Data | Simulated traffic (clearly labelled). Real OMDB METAR/TAF can be switched on |
| Control | Recovery plans are never applied without approval. Routine upkeep can be automatic within limits, or switched off |

---

## 2. What the engine does

1. **Ingests events** through one endpoint (`POST /api/events`): schedule, ETA/TOBT, METAR/TAF, runway/stand/taxiway status, lightning, ground stop, ATFM cap, airspace closure, NOTAM text.
2. **Assesses** conditions: LVP and ILS category, crosswind, runway modes, runway occupancy, taxi and turnaround factors.
3. **Validates** the approved plan on every event and lists conflicts (runway usability, separation, stand size/overlap/closure, ramp suspension, ground stop, airspace, ATFM, unreachable stand).
4. **Forecasts** demand against capacity in 15-minute bins for 6 hours (METAR for the first hour, then TAF) and raises early warnings.
5. **Re-plans** with CP-SAT and offers up to three options next to a rule-based baseline.
6. **Explains** each change from the structured plan data, and stress-tests each option against the TAF worst case.

---

## 3. Operating rules (planning values, configurable)

| Rule | Value in the prototype |
|---|---|
| LVP | RVR < 550 m or ceiling < 200 ft; CAT III below 300 m RVR |
| Runway occupancy per arrival / departure | VMC 2/2 min · IMC 3/2 · LVP 4/3; +1 for thunderstorm, heavy rain, gusts ≥ 30 kt or strong crosswind; +1 behind an A380 |
| Crosswind | Runway unusable above 35 kt (gust included) |
| Take-off minima | RVR ≥ 125 m |
| Runway use | Segregated (arrivals 12L, departures 12R); mixed mode on one runway only when the other is unusable |
| Stand buffer | 10 min between occupancies |
| Lightning RED | No on-block or push-back during the suspension |
| Airborne holding limit | 60 min, then a diversion is proposed; 15 min for an emergency |
| Ground wait for a stand | Up to 40 min after landing |

These are assumptions drawn from public ICAO/SKYbrary guidance. They are not certified values and must be set with the airport and ANSP before any operational use.

---

## 4. Optimiser

**Decisions per flight**: runway, runway time, stand (arrivals), ground wait, or "divert / no slot" as a last resort.

**Hard constraints**
1. Runway usable for that movement at that time (mode, closure, minima, ground stop).
2. One movement at a time per runway, spaced by the occupancy for the conditions.
3. ATFM hourly caps and airspace closures by region.
4. Stand size, stand availability, no stand overlap (with buffer).
5. No on-block or push-back inside a lightning suspension.
6. Movements already under way are frozen.

**Emergencies come first.** The model is solved in two stages. Stage one minimises emergency delay alone; that result is then locked as a hard bound for stage two. No amount of delay to other traffic can buy an emergency a later slot. An emergency is proposed for diversion only if no runway is usable within its holding limit.

**Objective for everything else** (three presets: Minimum delay, Balanced, Minimum change):
`delay × priority + long-delay penalty + worst-delay penalty + taxi time + changes vs approved plan + remote stands + ground waits`.

**Search.** The solver starts from the rule-based plan and improves it for 3 seconds per option. It is an anytime search: the answer is the best plan found, usually not a proven optimum, and two runs on the same state can differ in detail.

**Forecast stress test.** Each option is re-validated under the TAF's TEMPO/PROB/BECMG conditions. The review screen shows how many movements would need re-planning if that weather arrives.

---

## 5. Baseline and how the comparison is made

The baseline is first-come-first-served: each movement takes the next free runway slot, keeps its stand if free, else the first compatible free stand, else waits for one. It has no look-ahead.

It obeys the **same operating rules** as the optimiser (runway modes, separation, ATFM caps, ramp suspension, holding limit). The comparison therefore measures joint planning and look-ahead, not rule-breaking.

Delay averages cover served flights only. A plan could flatter them by diverting its hardest flights, so every screen shows **diverted / no slot** next to the delay, and options are ranked on critical issues first, then unserved flights, then a cost in delay-minutes: total delay, plus 10 for each flight delayed 15 minutes or more, 4 for each changed flight, and 2 for each movement exposed to the TAF worst case. The worst single delay breaks ties. These weights are planning values to set with the AOC.

The baseline is a simple policy, not a model of how DXB works today with A-CDM and AMAN/DMAN. We say so in the pitch. A fair test against real practice needs historical data (see §10).

---

## 6. Who decides what

| Change | Owner | FalconAero's role |
|---|---|---|
| Runway sequence and times | ATC tower (ANSP) | Proposes target times; ATC issues clearances |
| Stand allocation | Airport stand planning | Proposes; the AOC approves and co-ordinates |
| Diversion | Airline OCC with ATC | Flags that no slot exists within the holding limit |

- A recovery plan needs an explicit **Approve** (or **Reject**, which is logged). Approval is blocked while the plan has a critical issue or is stale. **Undo** restores the previous plan.
- **Routine upkeep** (allocating a new flight, re-sequencing for ETA/TOBT drift) is applied without approval only when it is small: no new conflict, no diversion, at most 3 stand changes, at most 6 flights re-timed, no flight pushed back more than 10 minutes. The controller can switch upkeep off; then nothing changes without approval.
- **A new operating condition always goes to the controller.** The engine keeps the set of conditions the controller has signed off (runway modes and occupancy, stops, caps, closures). When fog, a closure or a cap brings a condition that is not in that set and the plan conflicts with it, upkeep stands down and a recovery plan is offered. Approving the plan signs the new conditions off.
- **An offered plan stays approvable.** It is re-checked on every update against what has actually happened since it was computed; movements that have become committed keep what they are doing. It is replaced only when none of its options is still valid.
- The LLM never creates assignments. It turns free text into *proposed* events that a person confirms, and answers questions through read-only tools.

---

## 7. Displays and the simulator

- **Airspace view (default).** A 40 NM radar-style picture: range rings, extended centreline, arrival and departure routes for the runways in use, holding stacks, and each aircraft with a data block (callsign, landing sequence, altitude, speed, type, runway, time, delay). Arrivals are blue circles, departures magenta diamonds, holding amber, emergencies and conflicts red, proposals cyan. The engine plans runway times, not airspace: positions are projected from those times onto illustrative routes, and the fix names are invented. It is labelled "schematic, not for navigation".
- **Surface view** (`M`): the airfield with taxi routes, stands and proposed stand changes.
- **Fog is physical in the simulator.** Simulated aircraft keep the low-visibility runway spacing and do not take off below 125 m RVR, whatever the plan says. A plan made for clear weather therefore backs up in fog until a new one is approved. Take-offs in fog above 125 m RVR are normal low-visibility operations, at the reduced rate; the map says so while LVP is in force.
- **The simulator waits for decisions.** While the engine is waiting for the controller, the simulation drops to real time. A plan takes about ten seconds to compute and a person longer to read; at 30× the world would be minutes ahead by then. Choosing a speed overrides the hold.

## 8. Known limits

| Limit | Why it matters | What a pilot would add |
|---|---|---|
| Taxiways are routed, not de-conflicted | Taxi times and closures are modelled; two aircraft meeting on a segment are not | Segment occupancy in the model, or a link to A-SMGCS routing |
| Runways are spaced by occupancy time | No wake-turbulence pairs beyond the A380; the two runways are not staggered against each other | Pair-wise separation matrix and dependent-runway rules from the ANSP |
| Rotations link only after on-block | A late inbound delays its outbound only once the aircraft is on stand | Registration-based rotation links from the AODB |
| No airline, terminal or passenger preferences | Stand choice ignores terminals, connections, towing and crew | Preference and connection data from airlines and the airport |
| Plans use the prevailing forecast | The worst case is stress-tested and shown, not optimised | Robust or scenario-based optimisation |
| Open-ended events get assumed durations | Lightning 30 min, ground stop 45, airspace 180 if no end time is given | Operator-entered or learned durations |
| No learning from data | The "AI" is optimisation, rule-based forecasting and an optional LLM | Models for ETA, turnaround and fog-clearance uncertainty trained on history |
| No authentication or roles | Anyone who can reach the API can post events and approve | SSO, roles and an audit trail |
| Not bit-reproducible | Parallel search under a time limit | Deterministic search mode with a longer budget |

---

## 9. Data

- Traffic is **synthetic**, shaped like a Gulf hub: banked waves, wide-body-heavy fleet, IATA delay codes. It is labelled SIMULATION in the dashboard.
- Weather can be **real**: live OMDB METAR/TAF from NOAA aviationweather.gov, parsed by the engine.
- The 23 scenarios are modelled on public reports of real events (fog, shamal, the April 2024 flooding, the June 2025 airspace closures, drone sightings). Parameters are illustrative.
- Sources to cite in the submission: Bayanat (UAE aviation statistics), Dubai Pulse, OpenSky Network (ADS-B traffic patterns), aviationweather.gov, GCAA publications.
- Not public, so assumed: stand occupancy, A-CDM milestones, runway occupancy times. These are the first things to request in a pilot.

---

## 10. Pioneers deliverables

**90-day pilot**
- Weeks 1–4: agree data access and security with the data owners (the airport operator and the ANSP), with GCAA as sponsor. Replay 2–3 historical disruption days offline.
- Weeks 5–9: shadow mode on those replays and, if a feed is agreed, a read-only live feed. Compare proposals with what the AOC actually did.
- Weeks 10–12: controllers score the proposals; KPI report (delay, diversions, stand changes, acceptance rate).
- Live feeds are a stretch goal, not an assumption: accreditation usually takes longer than 90 days.

**Market**: a decision-support module that sits on top of existing AODB / A-CDM systems at GCC airports and ANSPs. It complements them; it does not replace them.

**Pitch**: 3 min + 2 min Q&A. Demo = fog scenario: warning → options → compare → approve.

---

## 11. Questions to expect

| Question | Answer |
|---|---|
| Better than what? | Than a first-come-first-served policy under the same rules, on simulated traffic. Not yet measured against DXB's real tools; that is the pilot's first job |
| Where is the AI? | Constraint optimisation for the plan, a forecast model for early warning, an LLM for NOTAM parsing and explanations. Nothing learns from data yet |
| Who presses Approve? | The AOC duty manager, as co-ordinator. Sequence stays with ATC, stands with stand planning; the screen shows how many changes go to each |
| What about taxiway conflicts? | Routing and closures are modelled. Segment conflicts are not; that needs A-SMGCS data |
| DXB's runways are dependent | We run them segregated, which is how closely spaced parallels are used. Staggering between them is not modelled |
| What if the fog lasts longer than forecast? | Each option is stress-tested against the TAF worst case, and the plan is re-validated on every new METAR |
| Does it ever act alone? | Only routine upkeep within limits, and that can be switched off. Recovery plans always wait for approval, and any new operating condition is routed to the controller |
| Why are aircraft taking off in fog? | Low-visibility take-offs are permitted down to 125 m RVR at a reduced rate. Below that the simulator stops them and the map says "no departures" |
| Is the radar picture real? | The times are the engine's; the positions are a projection onto illustrative routes. It shows where traffic would be, not surveillance data |
| Is the comparison rigged? | Both plans obey the same rules, the optimiser starts from the baseline plan, and diversions are shown next to delay |
