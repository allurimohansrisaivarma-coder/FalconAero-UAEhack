export type Kind = "ARR" | "DEP";
export type Pt = [number, number];
export type Sev = "critical" | "warning" | "info";

export interface Layout {
  icao: string;
  name: string;
  note: string;
  viewBox: number[];
  runways: { id: string; opposite: string; heading: number; y: number; x1: number; x2: number; ils_cat: string; default_mode: string; length_m: number }[];
  stands: { id: string; group: string; max_code: string; contact: boolean; x: number; y: number; lane_y: number }[];
  taxiways: { a: Pt; b: Pt; name: string }[];
  taxiway_names: string[];
  taxiway_labels: { text: string; x: number; y: number }[];
  buildings: { id: string; label: string; x: number; y: number; w: number; h: number }[];
  apron: { x: number; y: number; w: number; h: number };
  exits: Record<string, number>;
  entry_x: number;
  holding_fix: { name: string; x: number; y: number };
}

export interface Cond {
  wind_dir: number | null; wind_kt: number; gust_kt: number | null; vis_m: number; rvr_m: number | null;
  wx: string[]; ceiling_ft: number | null; temp_c: number | null; dew_c: number | null; qnh: number | null; cavok: boolean; raw: string;
}

export interface RunwayOps {
  id: string; open: boolean; arr_ok: boolean; dep_ok: boolean; mode: string; occ_arr: number; occ_dep: number; reasons: string[]; direction: string;
}

export interface Ops {
  t: number; category: string; lvp: boolean; required_ils: string; taxi_factor: number; turn_factor: number; ramp_open: boolean;
  lightning: string; dep_stopped: boolean; arr_stopped: boolean; flow: Record<string, number>; closed_regions: string[];
  runways: Record<string, RunwayOps>; weather: Cond; basis: string; advisories: string[];
}

export interface Restriction { status: string; reason: string; until: number | null; since: number; [k: string]: unknown }

export interface World {
  now: number; clock_rate: number; clock_source: string;
  runways: Record<string, Restriction>; stands: Record<string, Restriction>; taxiways: Record<string, Restriction>;
  lightning: Restriction; ground_stop: Restriction; flow: Record<string, Restriction>; airspace: Restriction;
  metar: Cond | null; metar_t: number | null; metar_source: string | null; taf_raw: string | null; taf_t: number | null;
  notes: { t: number; kind: string; text: string; source: string }[];
}

export interface Flight {
  callsign: string; kind: Kind; aircraft: string; code: string; code_known: boolean; sched: number; est: number;
  origin_dest: string; priority: "normal" | "high" | "emergency"; turnaround: number; stand: string | null; region: string | null;
  tobt: number | null; status: string; aldt: number | null; aibt: number | null; aobt: number | null; atot: number | null;
  act_runway: string | null; act_stand: string | null; active: boolean;
}

export interface PlanRow {
  callsign: string; kind: Kind; type: string; code: string; code_known: boolean; priority: Flight["priority"];
  origin_dest: string; region: string | null; sched: number; est: number; earliest: number; tobt: number | null;
  status: string; runway: string | null; t: number; stand: string | null; diverted: boolean; delay: number; added: number;
  actual: boolean; taxi_min: number; path: Pt[]; onblock: number | null; offblock: number | null;
  stand_from: number | null; stand_to: number | null; rwy_occ: number; unreachable: boolean; ground_wait?: number;
}
export type Plan = Record<string, PlanRow>;

export interface Conflict { type: string; severity: Sev; flights: string[]; resource: string; message: string; t: number }

export interface Kpis {
  served: number; unserved: number; diverted: number; avg_delay: number; avg_arr_delay: number; max_delay: number;
  delayed_15: number; total_delay: number; holding: number; ground_waits: number; taxi_total: number; remote_stands: number;
  conflicts: number; critical: number; stand_count: number;
}

export interface ForecastBin {
  t: number; category: string; risk_category: string; basis: string; lvp: boolean; ramp_open: boolean; modes: Record<string, string>;
  arr_demand: number; arr_cap: number; arr_cap_risk: number; arr_queue: number; arr_delay: number;
  dep_demand: number; dep_cap: number; dep_cap_risk: number; dep_queue: number; dep_delay: number;
  stands_used: number; stands_avail: number; f_used: number; f_avail: number;
}
export interface ForecastAlert { key: string; severity: Sev; category: string; title: string; body: string; t: number }
export interface Forecast { bins: ForecastBin[]; alerts: ForecastAlert[] }

export interface Notification {
  id: number; key: string; severity: Sev; category: string; title: string; body: string; t: number; updated: number;
  status: "open" | "ack" | "resolved"; read: boolean; count: number; action: { type: string; ref?: string } | null;
  resolved_t: number | null; resolution?: string;
}

export interface LogEntry { t: number; source: string; kind: string; text: string }

export interface OptionsMeta { id: number; t: number; auto: boolean; recommended: string | null; labels: string[]; valid: string[]; stale: boolean }

export interface Snapshot {
  version: number; now: number; clock_rate: number; clock_source: string; ops: Ops; world: World;
  flights: Record<string, Flight>; plan: Plan; frozen: string[]; conflicts: Conflict[]; kpis: Kpis; situation: string;
  forecast: Forecast; notifications: { unread: number; open: number; critical: number; latest: Notification[] };
  log: LogEntry[]; options: OptionsMeta | null; planning: boolean; can_undo: boolean;
  /** Why the engine is waiting for the controller, or null when nothing needs a decision. */
  decision: string | null;
  settings: { auto_allocate: boolean; auto_recovery: boolean; max_auto_stand_changes: number; max_auto_retimed: number; max_auto_delay: number };
  ai: { enabled: boolean; provider: string; model: string | null }; assumptions: string[];
}

export interface Change {
  callsign: string; kind: Kind; type: string; priority: string;
  changes: { field: "stand" | "runway" | "time" | "new" | "wait"; from: string | number | null; to: string | number | null }[];
  reasons: string[]; t: number; stand: string | null; runway: string | null; delay: number;
}

export interface PlanOption {
  id: string; label: string; tagline: string; also: string[]; kpis: Kpis; conflicts: Conflict[]; changes: Change[]; plan: Plan;
  solver: { status: string; ms: number; objective: number | null; variables?: number }; summary: string;
  /** Stress test against the TAF worst case: how many movements would break, and from when. Null if the TAF adds no risk. */
  risk: { t: number; category: string; basis: string; flights: number } | null;
}

export interface PlansResponse {
  id: number; t: number; auto: boolean; situation: string; stale?: boolean;
  current: { kpis: Kpis; conflicts: Conflict[] };
  baseline: { kpis: Kpis; conflicts: Conflict[]; plan: Plan; changes: Change[] };
  options: PlanOption[]; recommended: string | null; frozen: string[]; assumptions: string[];
}

export interface ScenarioMeta { id: string; name: string; category: string; severity: string; duration: number; description: string; reference: string }

export interface SimStatus {
  playing: boolean; speed: number; speeds: number[]; now: number; start: number; end: number;
  /** Speed the clock is running at; lower than `speed` while it holds at real time for a pending decision. */
  rate: number;
  params: Record<string, number | boolean>; weather_mode: "sim" | "live"; chaos: { enabled: boolean; per_hour: number; seed: number };
  base_metar: Record<string, unknown>; current_metar: string | null; outage_until: number | null;
  scheduled: { id: number; at: number; label: string; group: string | null; type: string }[];
  history: { t: number; label: string; ok: boolean; error?: string | null }[];
  active_scenarios: { group: string; id: string; name: string; at: number; until: number }[];
  sent: number; started: boolean;
}
