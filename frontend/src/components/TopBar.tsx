import type { Snapshot, SimStatus } from "../types";
import { clock } from "../sim";
import { Mark } from "./icons";

export type Tab = "ops" | "notifications";

interface Props {
  snap: Snapshot;
  sim: SimStatus | null;
  T: number;
  tab: Tab;
  onTab: (t: Tab) => void;
  connected: boolean;
  standsInUse: number;
}

export function TopBar({ snap, sim, T, tab, onTab, connected, standsInUse }: Props) {
  const { ops, kpis, conflicts, world } = snap;
  const wx = ops.weather;
  const crit = conflicts.filter((c) => c.severity === "critical").length;
  const n = snap.notifications;
  const srcLive = world.metar_source === "live";

  return (
    <header className="top">
      <div className="cell brand fixed" style={{ width: 220 }}>
        <Mark />
        <div>
          <div className="brand-name">FALCONAERO</div>
          <div className="brand-sub">AOC · RECOVERY</div>
          {snap.clock_source === "sim" && <span className="tag sim" title="Traffic and events come from the built-in simulator, not from a live airport feed">SIMULATION</span>}
        </div>
      </div>
      <div className="cell fixed" style={{ padding: "0 10px" }}>
        <nav className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "ops"} className={tab === "ops" ? "on" : ""} onClick={() => onTab("ops")}>Operations</button>
          <button role="tab" aria-selected={tab === "notifications"} className={tab === "notifications" ? "on" : ""} onClick={() => onTab("notifications")}>
            Notifications
            {n.unread > 0 && <span className={`badge ${n.critical ? "crit" : ""}`}>{n.unread}</span>}
          </button>
        </nav>
      </div>

      <div className="cell fixed" style={{ minWidth: 150 }}>
        <div className="cap">
          OMDB · UTC
          <span className={`dot ${connected ? "ok" : "crit"}`} title={connected ? "Live stream connected" : "Reconnecting to engine…"} />
        </div>
        <div className="clock">{clock(T, true)}<small>Z</small></div>
        <div className="src mono">
          {snap.clock_source === "sim" ? `SIM CLOCK ×${sim?.rate ?? "—"}${sim?.playing ? (sim.rate < sim.speed ? " · HOLDING" : "") : " · PAUSED"}` : "WALL CLOCK"}
        </div>
      </div>

      <div className="cell wx" style={{ flex: "1 1 0", maxWidth: 470 }}>
        <div className="metar" title={wx.raw}>{wx.raw || "No METAR received"}</div>
        <div className="wx-row">
          <span className="tag">VIS {wx.vis_m >= 9999 ? "10KM+" : `${wx.vis_m}M`}</span>
          {wx.rvr_m != null && <span className="tag">RVR {wx.rvr_m}M</span>}
          {wx.wind_kt > 0 && <span className="tag">{wx.wind_dir ?? "VRB"}°/{wx.wind_kt}{wx.gust_kt ? `G${wx.gust_kt}` : ""}KT</span>}
          {ops.lvp ? <span className="tag warn">LVP · CAT {ops.required_ils}</span>
            : <span className={`tag ${ops.category === "IMC" ? "warn" : ""}`}>{ops.category}</span>}
          {!ops.ramp_open && <span className="tag crit">RAMP CLOSED</span>}
          {ops.dep_stopped && <span className="tag crit">GROUND STOP</span>}
          <span className={`tag ${srcLive ? "ok" : "sim"}`} title="Source of the current METAR">{srcLive ? "LIVE MET" : "SIM MET"}</span>
        </div>
      </div>

      <div className="cell fixed rwys">
        <div className="cap" style={{ marginBottom: 4 }}>Runways</div>
        <div className="rwy-chips">
          {Object.values(ops.runways).map((r) => {
            const cls = r.mode === "CLOSED" ? "closed" : r.mode === "MIXED" ? "mixed" : r.mode === "STANDBY" ? "mixed" : "";
            return (
              <div key={r.id} className={`rwy-chip ${cls}`} title={r.reasons.join("; ") || `${r.occ_arr} min/arrival, ${r.occ_dep} min/departure`}>
                <span className="id">{r.direction || r.id}</span>
                <span className="mode">{r.mode === "ARR" ? "ARRIVALS" : r.mode === "DEP" ? "DEPARTURES" : r.mode}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="kpis">
        <Kpi label="Avg delay" value={kpis.avg_delay.toFixed(1)} unit="min" tone={kpis.avg_delay >= 10 ? "crit" : kpis.avg_delay >= 3 ? "warn" : undefined}
          hint="Average airport-caused delay (vs ETA/ready time) of the upcoming movements that have a slot. Diverted and unallocated flights are counted under Unserved." />
        <Kpi label="Holding" value={String(kpis.holding)} unit="arr" tone={kpis.holding > 3 ? "warn" : undefined} hint="Arrivals held beyond their ETA" />
        <Kpi label="Unserved" value={String(kpis.unserved)} unit={kpis.diverted ? `${kpis.diverted} div` : ""} tone={kpis.diverted ? "crit" : kpis.unserved ? "warn" : undefined}
          hint="Flights diverted or still without a runway slot. They are not part of the average delay." />
        <Kpi label="Stands" value={String(standsInUse)} unit={`/${kpis.stand_count}`} tone={standsInUse >= kpis.stand_count - 1 ? "warn" : undefined} hint="Stands occupied now" />
        <Kpi label="Issues" value={String(conflicts.length)} unit={crit ? `${crit} crit` : ""} tone={crit ? "crit" : conflicts.length ? "warn" : undefined}
          hint="Open issues in the approved plan under current conditions" />
      </div>
    </header>
  );
}

function Kpi({ label, value, unit, tone, hint }: { label: string; value: string; unit?: string; tone?: "warn" | "crit"; hint: string }) {
  return (
    <div className={`kpi ${tone ?? ""}`} title={hint}>
      <div className="cap">{label}</div>
      <div className="v">{value}{unit && <small>{unit}</small>}</div>
    </div>
  );
}
