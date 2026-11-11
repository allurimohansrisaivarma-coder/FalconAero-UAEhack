"""Engine orchestrator: ingest → assess → validate → forecast → notify → (re)plan."""
from __future__ import annotations

import copy
import hashlib
import itertools
import threading
import time

from . import llm
from .airport import Airport
from .baseline import plan_baseline
from .explain import clock, explain_changes, situation, summarize
from .forecast import forecast
from .kpis import kpis
from .notify import Notifications
from .optimizer import PRESET_BY_ID, PRESETS, solve
from .planning import build_ctx, committed, mark_committed, materialize, validate
from .state import WorldState

RESTRICTIVE = {"RUNWAY_STATUS", "STAND_STATUS", "TAXIWAY_STATUS", "LIGHTNING_ALERT", "GROUND_STOP",
               "FLOW_RESTRICTION", "AIRSPACE_CLOSURE"}


def _h(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()[:8]


REOFFER_AFTER_MIN = 15  # after a rejection, wait this long before offering a recovery plan again unasked

# Ranking weights, in minutes of total delay. Planning values: set them with the AOC.
LATE_FLIGHT_MIN = 10    # extra cost of each flight delayed 15 min or more (the industry on-time threshold)
CHANGE_MIN = 4          # one flight whose runway, time or stand changes
EXPOSED_MIN = 2         # half a change: a TEMPO/PROB group is expected for less than half its period


def _rank(option: dict) -> tuple:
    """Sort key for choosing the recommended option; the lowest key wins."""
    k = option["kpis"]
    exposed = option["risk"]["flights"] if option["risk"] else 0
    # Safety first, then flights left unserved. After that, one score in delay-minutes:
    cost = (k["total_delay"]
            + LATE_FLIGHT_MIN * k["delayed_15"]          # a flight past the 15-minute on-time threshold
            + CHANGE_MIN * len(option["changes"])        # co-ordination work for ATC, stands and handlers
            + EXPOSED_MIN * exposed)                     # would need re-planning if the TAF worst case arrives
    return (k["critical"], k["unserved"], cost, k["max_delay"])


def _conditions(ctx) -> frozenset:
    """The distinct operating states in the planning horizon, described by their effect on capacity.
    A state that was not there when the controller last signed off the plan is a new condition."""
    states: set = set()
    for o in ctx.ops_bins.values():
        states.add((tuple(sorted((r.id, r.mode, r.occ_arr, r.occ_dep) for r in o.runways.values())),
                    o.ramp_open, o.dep_stopped, o.arr_stopped, tuple(sorted(o.flow.items())),
                    tuple(sorted(o.closed_regions))))
    states.update(("stand", sid) for sid in ctx.stand_blocks)
    states.update(("taxiway", name) for name in ctx.closed_twy)
    return frozenset(states)


def _forecast_risk(ctx, risk_ctx) -> dict | None:
    """First point in the horizon where the TAF's worst case would cut capacity below the plan's assumptions."""
    for b in sorted(ctx.ops_bins):
        o, w = ctx.ops_bins[b], risk_ctx.ops_bins[b]
        if w.category != o.category or any(w.runways[r].mode != v.mode or w.runways[r].occ_arr > v.occ_arr
                                           or w.runways[r].occ_dep > v.occ_dep for r, v in o.runways.items()):
            return {"t": b, "category": w.category, "basis": w.basis}
    return None


class Engine:
    def __init__(self, airport: str = "omdb") -> None:
        self.airport = Airport.load(airport)
        self.lock = threading.RLock()
        # Limits on what routine upkeep may change without approval
        self.settings = {"auto_allocate": True, "auto_recovery": True, "max_auto_stand_changes": 3,
                         "max_auto_retimed": 6, "max_auto_delay": 10}
        self._subs: set = set()
        self._pending: set[str] = set()
        self._active: set[str] = set()      # planner jobs queued or running
        self._job = threading.Event()
        self._gen = itertools.count(1)
        self.reset()
        threading.Thread(target=self._work, daemon=True, name="planner").start()

    # ------------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        with self.lock:
            self.epoch = getattr(self, "epoch", 0) + 1   # invalidates in-flight planner results
            self.state = WorldState(self.airport)
            self.approved: dict = {}
            self.history: list[dict] = []
            self.options: dict | None = None
            self._option_cores: dict[str, dict] = {}
            self.notes = Notifications()
            self.log: list[dict] = []
            self._last_minute = None
            self._accepted: frozenset = frozenset()   # operating conditions the controller has signed off
            self._escalated = False                   # upkeep found a change too large to apply on its own
            self._hold_until = 0.0                    # after a rejection: no automatic re-offer before this time
            self.decision: str | None = None          # why a recovery plan is needed, if one is
            self.last: dict = {}
            self.version = 0
            self.planning_busy = False
            self._log("system", "info", f"FalconAero engine started for {self.airport.icao}.")
            self.recompute()

    def _log(self, source: str, kind: str, text: str) -> None:
        self.log.append({"t": self.state.now, "source": source, "kind": kind, "text": text})
        self.log = self.log[-300:]

    # ------------------------------------------------------------------ ingestion
    def ingest(self, events: list) -> list[dict]:
        results = []
        with self.lock:
            material = False
            for ev in events:
                try:
                    msgs = self.state.apply(ev)
                    results.append({"ok": True, "type": ev.type, "messages": msgs})
                    kind = "disruption" if ev.type in RESTRICTIVE and getattr(ev, "status", None) not in ("OPEN", "AVAILABLE") \
                        and getattr(ev, "level", "X") != "CLEAR" and getattr(ev, "active", True) is not False else "info"
                    if ev.type == "FLIGHT_UPDATE" and (ev.priority in ("emergency", "high") or ev.status in ("CANCELLED", "DIVERTED")):
                        kind = "disruption"
                    for msg in msgs:
                        self._log(ev.source, kind, msg)
                    if ev.type != "CLOCK":
                        material = True
                except ValueError as ex:
                    results.append({"ok": False, "type": ev.type, "error": str(ex)})
            minute = int(self.state.now)
            if material or minute != self._last_minute:
                self._last_minute = minute
                self.recompute()
            else:
                self.publish({"type": "clock", "now": self.state.now, "rate": self.state.clock_rate})
        return results

    # ------------------------------------------------------------------ assessment
    def recompute(self) -> None:
        with self.lock:
            for msg in self.state.expire():
                self._log("engine", "info", msg)
            ctx = build_ctx(self.state)
            rows = materialize(self.approved, ctx)
            frozen = committed(ctx, rows)
            conf = mark_committed(validate(rows, ctx), frozen)
            k = kpis(rows, conf, ctx)
            fc = forecast(self.state, ctx, rows)
            self.last = {"ctx": ctx, "rows": rows, "frozen": frozen, "conflicts": conf, "kpis": k, "forecast": fc}
            self._notify(ctx, conf, fc)
            self._triggers(ctx, conf)
            self.version += 1
            self.publish({"type": "state", "version": self.version})

    ROUTINE = {"unplanned", "timing", "overdue"}
    SEQUENCING = ROUTINE | {"separation", "flow"}      # issues that moving a flight in time can settle

    def _triggers(self, ctx, conf: list[dict]) -> None:
        """Decide who acts on the open issues: routine upkeep, or the controller through a recovery plan.

        Upkeep is for small corrections under conditions the controller has already accepted. Anything that
        comes from a new operating condition (fog, a closure, a cap), or that upkeep found too large, waits
        for an approved recovery plan."""
        crit = [c for c in conf if c["severity"] == "critical"]
        routine = any((c["type"] in self.ROUTINE and c["severity"] != "critical") or c["type"] in ("separation", "flow")
                      for c in conf)
        states = _conditions(ctx)
        if not crit:
            self._accepted = states          # no conflict under these conditions: nothing to sign off
        if not crit and not routine:
            self._escalated = False
        novel = not states <= self._accepted
        upkeep = (self.settings["auto_allocate"] and not novel and not self._escalated
                  and all(c["type"] in self.SEQUENCING for c in crit))
        # The controller decides when there is a conflict upkeep may not touch, or routine work that
        # upkeep is switched off for or has handed back as too large.
        for_controller = (bool(crit) and not upkeep) or (routine and not upkeep)
        self.decision = None
        if not for_controller:
            if self.options:
                # Nothing left to decide: withdraw the proposal instead of leaving it pending
                self.options, self._option_cores = None, {}
                self.notes.resolve("plan:ready", self.state.now, "No longer needed: approved plan is conflict-free")
            if routine and "maintain" not in self._active:
                self._schedule("maintain")
            return
        self.decision = ("New operating conditions" if novel else "Change too large for routine upkeep" if self._escalated
                         else "Conflict needs a decision" if crit else "Routine upkeep is switched off")
        if self.options:
            valid = [oid for oid, core in self._option_cores.items() if self._still_valid(core, ctx)]
            self.options["valid"] = valid
            self.options["stale"] = not valid
            if valid and self.options["recommended"] not in valid:
                self.options["recommended"] = min((o for o in self.options["options"] if o["id"] in valid), key=_rank)["id"]
        if ((self.options is None or self.options["stale"]) and self.settings["auto_recovery"]
                and "recover" not in self._active and self.state.now >= self._hold_until):
            self._schedule("recover")

    def _merge(self, core: dict, ctx) -> dict:
        """An option laid over the approved plan. Movements that have become committed since the option
        was computed keep what they are actually doing."""
        frozen = committed(ctx, materialize(self.approved, ctx))
        return {**self.approved, **{cs: copy.deepcopy(d) for cs, d in core.items()
                                    if cs not in frozen or cs not in self.approved}}

    def _still_valid(self, core: dict, ctx) -> bool:
        rows = materialize(self._merge(core, ctx), ctx)
        conf = mark_committed(validate(rows, ctx), committed(ctx, rows))
        return not any(c["severity"] == "critical" and c["type"] != "unplanned" for c in conf)

    def _notify(self, ctx, conf: list[dict], fc: dict) -> None:
        n, t, st = self.notes, self.state.now, self.state
        # Conflicts
        relevant = [c for c in conf if c["type"] != "unplanned" or c["severity"] == "critical"]
        if relevant:
            crit = sum(1 for c in relevant if c["severity"] == "critical")
            n.raise_("conflicts", "critical" if crit else "warning", "conflict",
                     f"{len(relevant)} issue{'s' if len(relevant) != 1 else ''} in the approved plan" + (f" ({crit} critical)" if crit else ""),
                     " · ".join(c["message"] for c in relevant[:3]) + (" …" if len(relevant) > 3 else ""), t,
                     {"type": "review_plan"})
        else:
            n.resolve("conflicts", t, "Approved plan conflict-free")
        # Forecast
        keys = set()
        for a in fc["alerts"]:
            n.raise_(a["key"], a["severity"], a["category"], a["title"], a["body"], t, {"type": "forecast"})
            keys.add(a["key"])
        n.sync("forecast:", keys, t)
        # Weather now
        o = ctx.ops(ctx.now)
        keys = set()
        w = o.weather
        if o.lvp:
            n.raise_("wx:lvp", "warning", "weather", f"Low-visibility procedures in force (CAT {o.required_ils})",
                     f"{w.raw} · Arrival capacity reduced; taxi speeds reduced.", t)
            keys.add("wx:lvp")
        elif o.category == "IMC":
            n.raise_("wx:imc", "info", "weather", "Instrument meteorological conditions", w.raw, t)
            keys.add("wx:imc")
        for adv in o.advisories:
            k = "wx:adv:" + _h(adv[:40])
            n.raise_(k, "info", "weather", adv.split(":")[0], adv, t)
            keys.add(k)
        n.sync("wx:", keys, t)
        # Resources
        keys = set()

        def until(r):
            return f" until {clock(r.until)}" if r.until else " (no end time)"
        for rid, r in st.runways.items():
            k = f"res:rwy:{rid}"
            n.raise_(k, "critical" if r.status == "CLOSED" else "warning", "resource",
                     f"Runway {rid} {r.status.lower()}" + (f", ILS CAT {r.extra['ils_cat']}" if r.extra.get("ils_cat") else ""),
                     (r.reason or "No reason given") + until(r), t)
            keys.add(k)
        for rid, ro in o.runways.items():
            if not ro.open and rid not in st.runways:
                k = f"res:rwy:{rid}:wx"
                n.raise_(k, "critical", "resource", f"Runway {rid} unusable", "; ".join(ro.reasons), t)
                keys.add(k)
        for sid, r in st.stands.items():
            k = f"res:stand:{sid}"
            n.raise_(k, "warning", "resource", f"Stand {sid} unavailable", (r.reason or "") + until(r), t)
            keys.add(k)
        for tw, r in st.taxiways.items():
            k = f"res:twy:{tw}"
            n.raise_(k, "warning", "resource", f"Taxiway {tw} closed", (r.reason or "") + until(r), t)
            keys.add(k)
        if st.lightning.status != "CLEAR":
            n.raise_("res:lightning", "critical" if st.lightning.status == "RED" else "warning", "resource",
                     f"Lightning {st.lightning.status}" + (": ramp operations suspended" if st.lightning.status == "RED" else ": prepare to suspend ramp"),
                     (st.lightning.reason or "Thunderstorm in vicinity") + until(st.lightning), t)
            keys.add("res:lightning")
        if st.ground_stop.status == "ACTIVE":
            n.raise_("res:gs", "critical", "resource", "Ground stop in force",
                     (st.ground_stop.reason or "") + until(st.ground_stop), t)
            keys.add("res:gs")
        for kk, r in st.flow.items():
            n.raise_(f"res:flow:{kk}", "warning", "resource", f"ATFM: {kk} capped at {r.extra['max_per_hour']}/h",
                     (r.reason or "Flow regulation") + until(r), t)
            keys.add(f"res:flow:{kk}")
        if st.airspace.status == "CLOSED":
            n.raise_("res:airspace", "critical", "resource",
                     f"Airspace closed via {', '.join(st.airspace.extra.get('regions', []))}",
                     (st.airspace.reason or "") + until(st.airspace), t)
            keys.add("res:airspace")
        n.sync("res:", keys, t)
        # Flights
        keys = set()
        for f in ctx.flights.values():
            if not f.active:
                continue
            if f.priority in ("emergency", "high"):
                k = f"flt:prio:{f.callsign}"
                row = self.last.get("rows", {}).get(f.callsign) if self.last else None
                when = f" · planned {clock(row['t'])} {row['runway']} stand {row['stand']}" if row and row.get("runway") else " · not yet planned"
                n.raise_(k, "critical" if f.priority == "emergency" else "warning", "flight",
                         f"{f.callsign} {'EMERGENCY' if f.priority == 'emergency' else 'priority'} ({f.kind} {f.aircraft})",
                         f"{f.origin_dest}, ETA/ETD {clock(f.earliest)}{when}", t, {"type": "focus_flight", "ref": f.callsign})
                keys.add(k)
            if not f.code_known:
                k = f"flt:type:{f.callsign}"
                n.raise_(k, "info", "data", f"Unknown aircraft type {f.aircraft} ({f.callsign})",
                         f"Assumed Code {f.code} (conservative). Check the type designator in the source feed.", t)
                keys.add(k)
        n.sync("flt:", keys, t)
        # Data quality + assumptions
        keys = set()
        if st.flights and st.metar_t is None:
            n.raise_("data:metar", "warning", "data", "No METAR received", "Planning assumes CAVOK until weather data arrives.", t)
            keys.add("data:metar")
        elif st.metar_t is not None and t - st.metar_t > 75:
            n.raise_("data:metar", "warning", "data", "Weather data stale",
                     f"Last METAR {round(t - st.metar_t)} min ago ({st.metar_source}). Check the MET feed.", t)
            keys.add("data:metar")
        n.sync("data:", keys, t)
        keys = set()
        for a in ctx.assumptions:
            k = "assume:" + _h(a)
            n.raise_(k, "info", "data", "Planning assumption", a, t)
            keys.add(k)
        n.sync("assume:", keys, t)

    # ------------------------------------------------------------------ planning
    def _schedule(self, job: str) -> None:
        self._pending.add(job)
        self._active.add(job)
        self._job.set()

    def _work(self) -> None:
        while True:
            self._job.wait()
            time.sleep(0.4)
            self._job.clear()
            jobs, self._pending = self._pending, set()
            try:
                if "maintain" in jobs:
                    self._auto_maintain()
                if "recover" in jobs:
                    self.generate_plans(auto=True)
            except Exception as ex:  # never kill the worker
                with self.lock:
                    self._log("engine", "error", f"Planner error: {ex}")
            finally:
                with self.lock:
                    self._active -= jobs

    def _auto_maintain(self) -> None:
        """Routine upkeep without a human: allocate new flights and re-sequence flights whose
        ETA/TOBT drifted. Only the touched flights may move; everything else stays frozen.
        Applied automatically only if it is small: no new critical issue, no diversion, and within
        the `max_auto_*` limits on stand changes, re-timed flights and added delay. Anything bigger
        is escalated to the controller as a recovery plan."""
        with self.lock:
            ctx = build_ctx(self.state)
            if self._escalated or not self.settings["auto_allocate"] or not _conditions(ctx) <= self._accepted:
                return
            ref = copy.deepcopy(self.approved)
            rows = materialize(ref, ctx)
            conf = validate(rows, ctx)
            touched = {cs for c in conf if c["type"] in self.ROUTINE for cs in c["flights"]}
            # Pure time-sequencing issues (spacing/ATFM) on a usable runway: move the later flight
            for c in conf:
                if c["type"] in ("separation", "flow") and len(c["flights"]) == 2:
                    touched.add(c["flights"][1])
            touched = {cs for cs in touched if cs in ctx.flights and ctx.flights[cs].active}
            if not touched:
                return
            frozen = ({cs for cs in ref if cs in ctx.flights} - touched) | committed(ctx, rows)
            frozen -= {cs for cs in touched if cs not in committed(ctx, rows)}
            self.planning_busy = True
            epoch = self.epoch
        # Start from the rule-based placement of the touched flights, so even a short or starved search
        # (a busy machine, a first allocation of the whole schedule) begins from a sensible plan.
        core, info = solve(ctx, PRESET_BY_ID["min_change"], ref, frozen, hint=plan_baseline(ctx, ref, frozen),
                           time_limit=3.0 if len(touched) > 10 else 1.5)
        with self.lock:
            self.planning_busy = False
            if epoch != self.epoch or not core:
                if not core:
                    self._log("engine", "error", f"Routine re-planning failed ({info['status']}).")
                return
            upd = {cs: core[cs] for cs in touched if cs in core}
            merged = {**self.approved, **upd}
            ctx2 = build_ctx(self.state)
            r_before = materialize(self.approved, ctx2)
            before = sum(1 for c in mark_committed(validate(r_before, ctx2), committed(ctx2, r_before)) if c["severity"] == "critical")
            r_after = materialize(merged, ctx2)
            after = sum(1 for c in mark_committed(validate(r_after, ctx2), committed(ctx2, r_after)) if c["severity"] == "critical")
            diverted = [cs for cs, d in upd.items() if d.get("diverted")]
            stand_changes = sum(1 for cs, d in upd.items() if cs in self.approved and self.approved[cs].get("stand") != d.get("stand"))
            new = [cs for cs in upd if cs not in self.approved]
            changed = [cs for cs in upd if self.approved.get(cs) != upd[cs]]
            retimed = [cs for cs in changed if cs in self.approved]
            pushed = max((upd[cs]["t"] - self.approved[cs]["t"] for cs in retimed), default=0)
            s = self.settings
            why = ("a diversion" if diverted else "new conflicts" if after > before
                   else f"{stand_changes} stand changes" if stand_changes > s["max_auto_stand_changes"]
                   else f"{len(retimed)} flights re-timed" if len(retimed) > s["max_auto_retimed"]
                   else f"a flight pushed back {pushed} min" if pushed > s["max_auto_delay"]
                   else "conflicts it cannot settle" if not changed and after else None)
            if why:
                self._escalated = True
                self._log("engine", "info", f"Routine upkeep would mean {why}: left for your approval as a recovery plan.")
                self.recompute()
                return
            if not changed:
                return
            self.approved = merged
            parts = []
            if new:
                parts.append(f"{len(new)} new flight{'s' if len(new) != 1 else ''} allocated")
            if len(changed) - len(new):
                parts.append(f"{len(changed) - len(new)} re-sequenced for ETA/TOBT changes")
            self._log("engine", "auto", "Routine update (auto): " + ", ".join(parts) + f" · {info['ms']} ms.")
            self.recompute()

    def generate_plans(self, auto: bool = False) -> dict:
        with self.lock:
            ctx = build_ctx(self.state)
            ref = copy.deepcopy(self.approved)
            rows = materialize(ref, ctx)
            frozen = committed(ctx, rows)
            cur_conf = mark_committed(validate(rows, ctx), frozen)
            cur_k = kpis(rows, cur_conf, ctx)
            risk_ctx = build_ctx(self.state, worst_case=True)
            epoch = self.epoch
            self.planning_busy = True
            self.publish({"type": "planning", "busy": True})
        try:
            b_core = plan_baseline(ctx, ref, frozen)
            b_rows = materialize(b_core, ctx)
            b_conf = mark_committed(validate(b_rows, ctx), frozen)
            b_k = kpis(b_rows, b_conf, ctx)
            exposure = _forecast_risk(ctx, risk_ctx)
            options, seen, cores = [], {}, {}
            for p in PRESETS:
                core, info = solve(ctx, p, ref, frozen, hint=b_core)
                if not core:
                    continue
                key = tuple(sorted((cs, d.get("runway"), d["t"], d.get("stand")) for cs, d in core.items()))
                if key in seen:
                    seen[key]["also"].append(p.label)
                    continue
                m = materialize(core, ctx)
                conf = mark_committed(validate(m, ctx), frozen)
                k = kpis(m, conf, ctx)
                changes = explain_changes(m, rows, ctx)
                risk = None
                if exposure:
                    # Stress test: the same plan under the TAF's worst case (TEMPO / PROB / BECMG)
                    hit = {cs for c in validate(materialize(core, risk_ctx), risk_ctx)
                           if c["severity"] == "critical" for cs in c["flights"] if cs not in frozen}
                    risk = {**exposure, "flights": len(hit)}
                opt = {"id": p.id, "label": p.label, "tagline": p.tagline, "also": [], "kpis": k, "conflicts": conf,
                       "changes": changes, "plan": m, "solver": info, "risk": risk,
                       "summary": summarize(p.label, k, len(changes))}
                seen[key] = opt
                options.append(opt)
                cores[p.id] = core
            rec = min(options, key=_rank)["id"] if options else None
            result = {"id": next(self._gen), "t": ctx.now, "auto": auto, "valid": list(cores), "stale": False,
                      "situation": situation(ctx, cur_conf),
                      "current": {"kpis": cur_k, "conflicts": cur_conf},
                      "baseline": {"kpis": b_k, "conflicts": b_conf, "plan": b_rows, "changes": explain_changes(b_rows, rows, ctx)},
                      "options": options, "recommended": rec, "frozen": sorted(frozen), "assumptions": ctx.assumptions}
        finally:
            with self.lock:
                self.planning_busy = False
        with self.lock:
            if epoch != self.epoch or (auto and self.decision is None):
                return result        # the engine was reset, or the issue was settled while this was computing
            self.options = result
            self._option_cores = cores
            if options:
                best = next(o for o in options if o["id"] == rec)
                db = round(b_k["avg_delay"] - best["kpis"]["avg_delay"], 1)
                self.notes.raise_("plan:ready", "warning" if cur_k["critical"] else "info", "plan",
                                  f"Recovery plan ready: {best['label']} ({len(best['changes'])} changes)",
                                  f"{best['summary']} vs rule-based: {'-' if db >= 0 else '+'}{abs(db)} min avg delay, "
                                  f"{best['kpis']['unserved']} vs {b_k['unserved']} flights diverted or without a slot.",
                                  self.state.now, {"type": "review_plan"})
                self._log("engine", "info", f"Recovery options generated{' automatically' if auto else ''}: "
                          + ", ".join(f"{o['label']} ({o['kpis']['avg_delay']} min avg)" for o in options) + ".")
            else:
                self._hold_until = self.state.now + 5     # do not hammer the solver with the same infeasible state
                self.notes.raise_("plan:ready", "critical", "plan", "No feasible recovery plan",
                                  "The solver found no feasible plan. Check runway availability and minima.", self.state.now)
            self.publish({"type": "planning", "busy": False})
            self.publish({"type": "state", "version": self.version})
        return result

    def approve(self, option_id: str, by: str = "AOC Duty Manager") -> None:
        with self.lock:
            if not self.options or option_id not in self._option_cores:
                raise ValueError("Plan expired. Generate a new recovery plan.")
            ctx = build_ctx(self.state)
            merged = self._merge(self._option_cores[option_id], ctx)
            rows = materialize(merged, ctx)
            conf = mark_committed(validate(rows, ctx), committed(ctx, rows))
            crit = [c for c in conf if c["severity"] == "critical" and c["type"] != "unplanned"]
            if crit:
                self.options["stale"] = True
                self._schedule("recover")
                raise ValueError(f"Conditions changed since this plan was generated ({len(crit)} critical issues). "
                                 "A fresh recovery plan is being computed.")
            self.history.append(copy.deepcopy(self.approved))
            self.approved = merged
            label = next((o["label"] for o in self.options["options"] if o["id"] == option_id), option_id)
            self.options, self._option_cores = None, {}
            self._accepted, self._escalated = _conditions(ctx), False     # the controller has signed off these conditions
            self.notes.resolve("plan:ready", self.state.now, f"Approved: {label}")
            self._log("operator", "approved", f"Recovery plan '{label}' approved by {by}.")
            self.recompute()

    def undo(self) -> None:
        with self.lock:
            if not self.history:
                raise ValueError("Nothing to undo")
            self.approved = self.history.pop()
            self._log("operator", "info", "Last approval undone; previous plan restored.")
            self.recompute()

    def reject_plans(self, by: str = "AOC Duty Manager") -> None:
        with self.lock:
            if not self.options:
                raise ValueError("No recovery plan is pending")
            self.options, self._option_cores = None, {}
            self._hold_until = self.state.now + REOFFER_AFTER_MIN
            self.notes.resolve("plan:ready", self.state.now, f"Rejected by {by}")
            self._log("operator", "info", f"Recovery plan rejected by {by}; the approved plan stays in force.")
            self.recompute()

    # ------------------------------------------------------------------ outputs
    def snapshot(self) -> dict:
        with self.lock:
            L = self.last
            ctx = L["ctx"]
            o = ctx.ops(ctx.now)
            opts = None
            if self.options:
                opts = {"id": self.options["id"], "t": self.options["t"], "auto": self.options["auto"],
                        "recommended": self.options["recommended"],
                        "labels": [x["label"] for x in self.options["options"]],
                        "valid": self.options["valid"], "stale": self.options["stale"]}
            return {
                "version": self.version, "now": self.state.now, "clock_rate": self.state.clock_rate,
                "clock_source": self.state.clock_source,
                "ops": o.to_dict(), "world": self.state.snapshot(),
                "flights": {cs: f.to_dict() for cs, f in self.state.flights.items()},
                "plan": L["rows"], "frozen": sorted(L["frozen"]), "conflicts": L["conflicts"], "kpis": L["kpis"],
                "situation": situation(ctx, L["conflicts"]),
                "forecast": L["forecast"], "notifications": self.notes.summary(),
                "log": self.log[-80:], "options": opts, "planning": self.planning_busy,
                "decision": self.decision, "can_undo": bool(self.history), "settings": self.settings, "ai": llm.status(),
                "assumptions": ctx.assumptions,
            }

    def known(self) -> dict:
        with self.lock:
            rows = self.last.get("rows", {})
            return {
                "runways": [r.id for r in self.airport.runways],
                "runway_alias": {r.opposite: r.id for r in self.airport.runways},
                "stands": [s.id for s in self.airport.stands],
                "taxiways": self.airport.taxiway_names,
                "flights": {cs: {"kind": f.kind, "earliest": f.earliest} for cs, f in self.state.flights.items() if f.active},
                "rows": len(rows),
            }

    def tool(self, name: str, args: dict) -> dict:
        snap = self.snapshot()
        if name == "get_situation":
            return {"now": clock(snap["now"]), "situation": snap["situation"], "ops": {k: snap["ops"][k] for k in ("category", "lvp", "ramp_open", "flow", "closed_regions", "advisories")},
                    "runways": {k: {"mode": v["mode"], "reasons": v["reasons"], "occ_arr": v["occ_arr"], "occ_dep": v["occ_dep"]} for k, v in snap["ops"]["runways"].items()},
                    "metar": snap["world"]["metar"]["raw"] if snap["world"]["metar"] else None,
                    "kpis": snap["kpis"], "open_issues": [c["message"] for c in snap["conflicts"][:10]],
                    "notifications": [n["title"] for n in snap["notifications"]["latest"]]}
        if name == "get_flight":
            cs = str(args.get("callsign", "")).upper()
            r = snap["plan"].get(cs)
            f = snap["flights"].get(cs)
            if not f:
                return {"error": f"Unknown flight {cs}"}
            return {"flight": f, "plan": {k: r[k] for k in ("runway", "t", "stand", "delay", "added", "onblock", "offblock", "diverted", "ground_wait") if k in r} if r else None,
                    "times_hhmmz": {k: clock(v) for k, v in (r or {}).items() if k in ("t", "onblock", "offblock") and v}}
        if name == "list_flights":
            rows = [r for r in snap["plan"].values() if r["t"] >= snap["now"] - 5 and (not args.get("kind") or r["kind"] == args["kind"])]
            if args.get("delayed_only"):
                rows = [r for r in rows if r["added"] > 0]
            rows.sort(key=lambda r: r["t"])
            return {"flights": [{"callsign": r["callsign"], "kind": r["kind"], "time": clock(r["t"]), "runway": r["runway"],
                                 "stand": r["stand"], "delay_min": r["added"]} for r in rows[:40]]}
        if name == "get_forecast":
            fc = snap["forecast"]
            return {"alerts": fc["alerts"], "bins": [{"t": clock(b["t"]), "arr_demand": b["arr_demand"], "arr_cap_per_h": b["arr_cap"],
                                                      "arr_delay": b["arr_delay"], "dep_delay": b["dep_delay"], "category": b["category"],
                                                      "risk": b["risk_category"]} for b in fc["bins"][:16]]}
        if name == "get_recovery_options":
            if not self.options:
                return {"options": None, "note": "No recovery plan generated yet."}
            return {"recommended": self.options["recommended"], "baseline": self.options["baseline"]["kpis"],
                    "options": [{"id": o["id"], "label": o["label"], "kpis": o["kpis"], "summary": o["summary"],
                                 "changes": [{"callsign": c["callsign"], "reasons": c["reasons"]} for c in o["changes"][:12]]}
                                for o in self.options["options"]]}
        return {"error": f"Unknown tool {name}"}

    def brief_facts(self) -> tuple[dict, str]:
        snap = self.snapshot()
        fc = snap["forecast"]
        facts = {"now": clock(snap["now"]), "situation": snap["situation"],
                 "metar": snap["world"]["metar"]["raw"] if snap["world"]["metar"] else None, "taf": snap["world"]["taf_raw"],
                 "runways": {k: v["mode"] for k, v in snap["ops"]["runways"].items()},
                 "kpis": {k: snap["kpis"][k] for k in ("avg_delay", "holding", "max_delay", "conflicts", "critical", "unserved")},
                 "top_issues": [c["message"] for c in snap["conflicts"][:5]],
                 "forecast_alerts": [a["title"] + ": " + a["body"] for a in fc["alerts"]],
                 "recovery_plan": snap["options"], "advisories": snap["ops"]["advisories"]}
        fb = snap["situation"]
        if fc["alerts"]:
            fb += " Outlook: " + " ".join(a["title"] + "." for a in fc["alerts"][:3])
        if snap["options"]:
            fb += " A recovery plan is ready for review."
        return facts, fb

    # ------------------------------------------------------------------ pub/sub
    def subscribe(self, loop, queue) -> None:
        self._subs.add((loop, queue))

    def unsubscribe(self, loop, queue) -> None:
        self._subs.discard((loop, queue))

    def publish(self, msg: dict) -> None:
        for loop, q in list(self._subs):
            try:
                loop.call_soon_threadsafe(_offer, q, msg)
            except RuntimeError:
                self._subs.discard((loop, q))


def _offer(q, msg):
    try:
        q.put_nowait(msg)
    except Exception:
        pass
