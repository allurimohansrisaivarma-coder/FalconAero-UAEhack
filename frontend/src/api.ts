import type { Layout, Notification, PlansResponse, ScenarioMeta, SimStatus, Snapshot } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      const d = body?.detail;
      if (typeof d === "string") msg = d;
      else if (d?.errors) msg = d.errors.map((e: { error: string }) => e.error).join("; ");
      else if (d) msg = JSON.stringify(d);
    } catch { /* keep status text */ }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}
const post = <T,>(path: string, body: unknown = {}) => req<T>(path, { method: "POST", body: JSON.stringify(body) });
const del = <T,>(path: string) => req<T>(path, { method: "DELETE" });

export const api = {
  layout: () => req<Layout>("/api/layout"),
  events: (events: Record<string, unknown>[]) => post<{ results: { ok: boolean; error?: string; messages?: string[] }[] }>("/api/events", { events }),
  notifications: () => req<{ items: Notification[] }>("/api/notifications"),
  ack: (id: number) => post(`/api/notifications/${id}/ack`),
  ackAll: () => post("/api/notifications/ack_all"),
  plans: () => post<PlansResponse>("/api/plans"),
  latestPlans: () => req<PlansResponse | Record<string, never>>("/api/plans/latest"),
  approve: (id: string) => post(`/api/plans/${id}/approve`, { by: "AOC Duty Manager" }),
  reject: () => post("/api/plans/reject", { by: "AOC Duty Manager" }),
  undo: () => post("/api/undo"),
  settings: (s: Partial<Snapshot["settings"]>) => post("/api/settings", s),
  aiParse: (text: string) => post<{ events: Record<string, unknown>[]; errors: string[]; summary: string; engine: string }>("/api/ai/parse", { text }),
  aiBrief: () => req<{ text: string; engine: string }>("/api/ai/brief"),
  aiAsk: (question: string, history: { role: string; content: string }[]) =>
    post<{ text: string; engine: string; tools: string[] }>("/api/ai/ask", { question, history }),
  liveWeather: (enabled: boolean) => post<Record<string, unknown>>("/api/feeds/weather", { enabled }),
  liveWeatherStatus: () => req<Record<string, unknown>>("/api/feeds/weather"),
  sim: {
    state: () => req<SimStatus>("/sim/state"),
    scenarios: () => req<{ categories: string[]; items: ScenarioMeta[] }>("/sim/scenarios"),
    play: () => post<SimStatus>("/sim/play"),
    pause: () => post<SimStatus>("/sim/pause"),
    speed: (speed: number) => post<SimStatus>("/sim/speed", { speed }),
    restart: (params?: Record<string, unknown>) => post<SimStatus>("/sim/restart", { params }),
    jump: (minutes: number) => post<SimStatus>("/sim/jump", { minutes }),
    scenario: (scenario: string, delay = 0) => post("/sim/scenario", { scenario, delay }),
    event: (event: Record<string, unknown>, delay = 0) => post("/sim/event", { event, delay }),
    weather: (body: { fields?: Record<string, unknown>; raw?: string; delay?: number }) => post("/sim/weather", body),
    preview: (body: { fields?: Record<string, unknown>; raw?: string }) => post<{
      raw: string; category: string; lvp: boolean; required_ils: string; taxi_factor: number; advisories: string[];
      runways: Record<string, { mode: string; occ_arr: number; occ_dep: number; reasons: string[] }>;
    }>("/sim/metar/preview", body),
    cancel: (id: number) => del(`/sim/scheduled/${id}`),
    cancelGroup: (g: string) => del(`/sim/group/${encodeURIComponent(g)}`),
    chaos: (enabled: boolean, per_hour: number, seed?: number) => post("/sim/chaos", { enabled, per_hour, seed }),
  },
};
