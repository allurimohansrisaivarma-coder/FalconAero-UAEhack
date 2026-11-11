"""World executor: simulated aircraft and ground handlers follow the published plan.

The simulator keeps its own 'physical truth' (what it has injected) and only
moves an aircraft when the world allows it: a closed runway, a stand still
occupied or a ramp suspension physically delays the movement, and the engine
learns about it through updated ETAs and actual milestones, as with a real feed.
"""
from __future__ import annotations

import random

from engine.aircraft import DEFAULT_TURNAROUND, code_for
from engine.weather import parse_metar

TURN_EXTRA = (5, 25)
TAKEOFF_MIN_RVR = 125      # metres: below this nobody takes off, whatever the plan says


class World:
    def __init__(self, rng: random.Random, namer):
        self.rng = rng
        self.namer = namer
        self.closed_rwy: dict[str, float | None] = {}
        self.ramp_red_until: float | None = None
        self.ramp_red = False
        self.stop: tuple[str, float | None] | None = None
        self.rvr = 9999                           # metres, from the last METAR the simulator sent
        self.rwy_free: dict[str, float] = {}      # runway -> time the previous movement has cleared it
        self.stand_busy: dict[str, str] = {}      # stand -> callsign physically there
        self.flights: dict[str, dict] = {}        # sim-side flight records
        self.landed: dict[str, float] = {}
        self.last_hold_update: dict[str, float] = {}
        self.eta_sent: set[str] = set()
        self.turn_departures = True

    # Truth tracking from events the simulator emits
    def observe(self, ev: dict) -> None:
        t = ev["type"]
        if t == "RUNWAY_STATUS":
            if ev["status"] == "CLOSED":
                self.closed_rwy[ev["runway"]] = ev.get("until")
            else:
                self.closed_rwy.pop(ev["runway"], None)
        elif t == "LIGHTNING_ALERT":
            self.ramp_red = ev["level"] == "RED"
            self.ramp_red_until = ev.get("until") if self.ramp_red else None
        elif t == "GROUND_STOP":
            self.stop = (ev.get("scope", "DEP"), ev.get("until")) if ev["active"] else None
        elif t == "WEATHER_METAR":
            cond, _ = parse_metar(ev["raw"])
            self.rvr = cond.rvr_m if cond.rvr_m is not None else cond.vis_m
        elif t == "FLIGHT_UPSERT":
            self.flights[ev["callsign"]] = {**ev, "status": "SCHEDULED", "est": ev.get("est") or ev["sched"]}
            if ev["kind"] == "DEP" and ev.get("stand"):
                self.stand_busy[ev["stand"]] = ev["callsign"]
        elif t == "FLIGHT_UPDATE":
            f = self.flights.get(ev["callsign"])
            if f:
                for k in ("est", "tobt", "status", "priority"):
                    if ev.get(k) is not None:
                        f[k] = ev[k]

    def _expire(self, now: float) -> None:
        for r, u in list(self.closed_rwy.items()):
            if u is not None and now >= u:
                del self.closed_rwy[r]
        if self.ramp_red and self.ramp_red_until is not None and now >= self.ramp_red_until:
            self.ramp_red = False
        if self.stop and self.stop[1] is not None and now >= self.stop[1]:
            self.stop = None

    def _runway_clear(self, runway: str, now: float) -> bool:
        """Physical spacing: the previous movement must have cleared the runway. The spacing is the
        occupancy for the conditions at the time, so a plan made for clear weather backs up in fog."""
        return now >= self.rwy_free.get(runway, 0)

    def step(self, now: float, plan: dict, eta_noise: dict[str, int], tech: dict, outage: bool) -> list[dict]:
        self._expire(now)
        out: list[dict] = []
        for cs, f in list(self.flights.items()):
            st = f["status"]
            if st in ("DEPARTED", "CANCELLED", "DIVERTED", "TURNED"):
                continue
            r = plan.get(cs)
            if f["kind"] == "ARR":
                out += self._arrival(cs, f, r, now, eta_noise, outage)
            else:
                out += self._departure(cs, f, r, now, tech)
        return out

    def _arrival(self, cs, f, r, now, eta_noise, outage) -> list[dict]:
        ev: list[dict] = []
        st = f["status"]
        # ETA update ~90 min before (airborne / ADS-B derived), unless the data feed is down
        if cs not in self.eta_sent and now >= f["sched"] - 90 and not outage:
            self.eta_sent.add(cs)
            est = int(f["sched"] + eta_noise.get(cs, 0))
            f["est"] = max(est, int(now) + 3) if est < now + 3 else est
            ev.append({"type": "FLIGHT_UPDATE", "callsign": cs, "est": f["est"], "status": "AIRBORNE"})
        if st in ("SCHEDULED", "AIRBORNE"):
            if r and r.get("diverted") and now >= r["t"]:
                f["status"] = "DIVERTED"
                return ev + [{"type": "FLIGHT_UPDATE", "callsign": cs, "status": "DIVERTED", "reason": "Diverted per AOC plan (OMDW)"}]
            can_land = (r and r.get("runway") and now >= r["t"] and now >= f["est"] - 1
                        and r["runway"] not in self.closed_rwy and not (self.stop and self.stop[0] == "ALL")
                        and self._runway_clear(r["runway"], now))
            if can_land:
                f["status"] = "LANDED"
                self.landed[cs] = now
                self.rwy_free[r["runway"]] = now + (r.get("rwy_occ") or 2)
                f["runway"], f["stand_plan"] = r["runway"], r["stand"]
                f["taxi"] = max(2, (r["onblock"] or r["t"] + 6) - r["t"]) if r.get("onblock") else 6
                return ev + [{"type": "FLIGHT_UPDATE", "callsign": cs, "status": "LANDED", "aldt": int(now), "runway": r["runway"]}]
            # Airborne holding: ETA slips while the aircraft cannot land
            due = r["t"] if r and r.get("runway") else f["est"]
            if now >= max(due, f["est"]) + 2 and now - self.last_hold_update.get(cs, -99) >= 3 and not outage:
                self.last_hold_update[cs] = now
                f["est"] = int(now) + 3
                ev.append({"type": "FLIGHT_UPDATE", "callsign": cs, "est": f["est"], "reason": "holding"})
            return ev
        if st == "LANDED":
            stand = f.get("stand_plan") or (r or {}).get("stand")
            if r and r.get("stand") and r["stand"] != stand and r["stand"] not in self.stand_busy:
                stand = f["stand_plan"] = r["stand"]           # re-allocated while taxiing
            ready = now >= self.landed[cs] + f.get("taxi", 6) - 1
            target_onb = (r or {}).get("onblock")
            if target_onb is not None:
                ready = ready and now >= target_onb - 0.5
            if ready and stand and stand not in self.stand_busy and not self.ramp_red:
                f["status"] = "ONBLOCK"
                self.stand_busy[stand] = cs
                ev.append({"type": "FLIGHT_UPDATE", "callsign": cs, "status": "ONBLOCK", "aibt": int(now), "stand": stand})
                if self.turn_departures:
                    ev += self._next_leg(cs, f, stand, now)
        return ev

    def _next_leg(self, cs, f, stand, now) -> list[dict]:
        code, _ = code_for(f["aircraft"])
        ta = DEFAULT_TURNAROUND[code]
        ncs, _, port, region = self.namer.callsign(code)
        ncs = cs[:3] + ncs[3:] if cs[:3].isalpha() else ncs
        while ncs in self.flights:
            ncs = ncs + "A"
        etd = int(now + ta + self.rng.randint(*TURN_EXTRA))
        dep = {"type": "FLIGHT_UPSERT", "callsign": ncs, "kind": "DEP", "aircraft": f["aircraft"], "sched": etd,
               "origin_dest": port, "stand": stand, "region": region, "linked_arrival": cs}
        f["status"] = "TURNED"
        self.flights[ncs] = {**dep, "status": "SCHEDULED", "est": etd}
        return [dep]

    def _departure(self, cs, f, r, now, tech) -> list[dict]:
        ev: list[dict] = []
        if cs in tech and now >= tech[cs][0] and not f.get("tech_sent"):
            f["tech_sent"] = True
            f["tobt"] = tech[cs][1]
            ev.append({"type": "FLIGHT_UPDATE", "callsign": cs, "tobt": tech[cs][1], "reason": tech[cs][2]})
        st = f["status"]
        ready_t = max(f.get("est") or f["sched"], f.get("tobt") or 0)
        if st == "SCHEDULED":
            if not r or not r.get("runway"):
                return ev
            offb = r.get("offblock")
            if offb is None:
                return ev
            if now >= offb and now >= ready_t - r.get("taxi_min", 6) and not self.ramp_red and not self.stop:
                f["status"] = "OFFBLOCK"
                stand = f.get("stand")
                if stand and self.stand_busy.get(stand) == cs:
                    del self.stand_busy[stand]
                ev.append({"type": "FLIGHT_UPDATE", "callsign": cs, "status": "OFFBLOCK", "aobt": int(now)})
            return ev
        if st == "OFFBLOCK":
            rw = (r or {}).get("runway")
            if (r and rw and now >= r["t"] and rw not in self.closed_rwy and not self.stop
                    and self.rvr >= TAKEOFF_MIN_RVR and self._runway_clear(rw, now)):
                f["status"] = "DEPARTED"
                self.rwy_free[rw] = now + (r.get("rwy_occ") or 2)
                ev.append({"type": "FLIGHT_UPDATE", "callsign": cs, "status": "DEPARTED", "atot": int(now), "runway": rw})
        return ev
