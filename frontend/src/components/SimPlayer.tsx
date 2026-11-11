import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Layout, SimStatus, Snapshot } from "../types";
import { clock } from "../sim";
import { Maximize, Minimize, Pause, Play, Restart } from "./icons";
import { SimStudio } from "./SimStudio";

type Mode = "bubble" | "compact" | "max";

interface Props {
  sim: SimStatus | null;
  setSim: (s: SimStatus) => void;
  snap: Snapshot;
  layout: Layout;
  T: number;
  notify: (t: string, err?: boolean) => void;
  tucked?: boolean;       // collapse to bubble while a plan is under review
}

const PANEL_W = 360;
const FAB = 56;
const KEY = "falconaero.simplayer.v2";
const QUICK = [
  ["fog", "Fog"], ["thunderstorm", "Thunderstorm"], ["disabled", "Runway blocked"], ["medical", "Medical emerg."],
  ["airspace", "Airspace closure"], ["drone", "Drone"],
] as const;

interface Saved { x: number; y: number; mode: Mode }
const load = (): Saved | null => { try { const r = localStorage.getItem(KEY); return r ? JSON.parse(r) : null; } catch { return null; } };
const save = (s: Saved) => { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* unavailable */ } };

export function SimPlayer({ sim, setSim, snap, layout, T, notify, tucked }: Props) {
  const [pos, setPos] = useState<Saved>(() => load() ?? { x: window.innerWidth - PANEL_W - 16, y: window.innerHeight - 330, mode: "compact" });
  const drag = useRef<{ dx: number; dy: number; sx: number; sy: number; moved: boolean } | null>(null);
  const box = useRef<HTMLElement | null>(null);

  const clamp = useCallback((x: number, y: number, mode: Mode) => {
    const w = mode === "bubble" ? FAB : PANEL_W;
    const h = mode === "bubble" ? FAB + 20 : box.current?.offsetHeight ?? 300;
    return { x: Math.max(8, Math.min(window.innerWidth - w - 8, x)), y: Math.max(64, Math.min(window.innerHeight - h - 8, y)) };
  }, []);
  useEffect(() => { save(pos); }, [pos]);
  useEffect(() => {
    const f = () => setPos((s) => (s.mode === "max" ? s : { ...s, ...clamp(s.x, s.y, s.mode) }));
    window.addEventListener("resize", f);
    f();
    return () => window.removeEventListener("resize", f);
  }, [clamp]);

  const onDown = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest("[data-nodrag]")) return;
    drag.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y, sx: e.clientX, sy: e.clientY, moved: false };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (Math.abs(e.clientX - d.sx) + Math.abs(e.clientY - d.sy) > 4) d.moved = true;
    if (d.moved) setPos((s) => ({ ...s, ...clamp(e.clientX - d.dx, e.clientY - d.dy, s.mode) }));
  };
  const onUp = () => { const m = drag.current?.moved ?? false; drag.current = null; return m; };

  const setMode = (mode: Mode) => setPos((s) => {
    if (mode === "max") return { ...s, mode };
    if (s.mode === "max") return { mode, ...clamp(s.x, s.y, mode) };
    const nx = mode === "bubble" ? s.x + PANEL_W - FAB : s.x - PANEL_W + FAB;
    return { mode, ...clamp(nx, s.y, mode) };
  });

  const run = async (p: Promise<unknown>, ok?: string) => {
    try {
      const r = await p;
      if (r && typeof r === "object" && "playing" in (r as object)) setSim(r as SimStatus);
      if (ok) notify(ok);
    } catch (e) { notify(e instanceof Error ? e.message : String(e), true); }
  };
  const toggle = () => run(sim?.playing ? api.sim.pause() : api.sim.play());

  // Space = play/pause (global), never also "clicking" a focused button
  useEffect(() => {
    const kd = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.code === "Space") { e.preventDefault(); (document.activeElement as HTMLElement | null)?.blur(); if (!e.repeat) toggle(); }
    };
    const ku = (e: KeyboardEvent) => { if (e.code === "Space") e.preventDefault(); };
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);
    return () => { window.removeEventListener("keydown", kd); window.removeEventListener("keyup", ku); };
  });

  if (!sim) return null;
  const end = sim.end;
  const frac = Math.max(0, Math.min(1, (T - sim.start) / Math.max(1, end - sim.start)));

  if (pos.mode === "max" && !tucked) {
    return <SimStudio sim={sim} setSim={setSim} snap={snap} layout={layout} T={T} notify={notify} onCompact={() => setMode("compact")} onBubble={() => setMode("bubble")} />;
  }

  const forced = tucked && pos.mode !== "bubble";
  if (pos.mode === "bubble" || forced) {
    const R = 25, C = 2 * Math.PI * R;
    const at = forced ? { x: window.innerWidth - 620 - FAB - 16, y: window.innerHeight - FAB - 36 } : pos;
    return (
      <button ref={(el) => { box.current = el; }} className="simfab" style={{ left: at.x, top: at.y }}
        onPointerDown={forced ? undefined : onDown} onPointerMove={forced ? undefined : onMove}
        onPointerUp={() => { if (!onUp() && !forced) setMode("compact"); }}
        title={forced ? "Simulation controls are tucked away while you review a plan" : "Simulation (click to expand, drag to move)"} aria-label="Expand simulation controls">
        <svg className="ring" width={FAB} height={FAB} viewBox={`0 0 ${FAB} ${FAB}`}>
          <circle cx={FAB / 2} cy={FAB / 2} r={R} fill="none" stroke="var(--line-2)" strokeWidth="2" />
          <circle cx={FAB / 2} cy={FAB / 2} r={R} fill="none" stroke={sim.playing ? "var(--text)" : "var(--text-3)"} strokeWidth="2"
            strokeDasharray={`${C * frac} ${C}`} transform={`rotate(-90 ${FAB / 2} ${FAB / 2})`} strokeLinecap="round" />
        </svg>
        <span data-nodrag onPointerUp={(e) => { e.stopPropagation(); toggle(); }}
          style={{ width: 30, height: 30, borderRadius: "50%", display: "grid", placeItems: "center", background: sim.playing ? "var(--bg-3)" : "var(--text)", color: sim.playing ? "var(--text)" : "#0b0e12", position: "relative" }}
          title={sim.playing ? "Pause" : "Play"}>
          {sim.playing ? <Pause size={14} /> : <Play size={14} />}
        </span>
        <span className="fab-t">{clock(T)}Z ×{sim.rate}</span>
      </button>
    );
  }

  return (
    <div ref={(el) => { box.current = el; }} className="simctl" style={{ left: pos.x, top: pos.y, width: PANEL_W }} role="region" aria-label="Simulation player">
      <div className="simctl-h" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}>
        <span className="grip" />
        <span className="cap" style={{ color: "var(--text-2)" }}>Simulation</span>
        <span className="tag sim" style={{ height: 16, fontSize: 9.5 }}>EVENT SOURCE</span>
        <span className="spacer" />
        <button data-nodrag className="icon-btn" onClick={() => setMode("max")} title="Open Scenario Studio"><Maximize /></button>
        <button data-nodrag className="icon-btn" onClick={() => setMode("bubble")} title="Minimise"><Minimize /></button>
      </div>
      <div className="simctl-b">
        <div className="sim-clock-row">
          <span className="sim-clock">{clock(T, true)}<small className="dim3" style={{ fontSize: 13 }}>Z</small></span>
          <span className="sim-t">T+{Math.max(0, Math.floor(T - sim.start))}′</span>
          <span className="spacer" />
          {sim.playing && sim.rate < sim.speed
            ? <span className="tag warn" title="The simulation runs at real time while a decision is pending, so the plan you are reading stays current. Pick a speed to override.">×{sim.rate} · DECISION PENDING</span>
            : <span className={`tag ${sim.playing ? "ok" : ""}`}>{sim.playing ? "RUNNING" : "PAUSED"}</span>}
        </div>
        <div className="transport">
          <button className="tbtn" onClick={() => confirm("Restart the simulation? The engine state is reset and new traffic is generated.") && run(api.sim.restart(), "Simulation restarted")} title="Restart"><Restart /></button>
          <button className="tbtn play" onClick={toggle} title={sim.playing ? "Pause (Space)" : "Play (Space)"}>{sim.playing ? <Pause /> : <Play />}</button>
          <button className="tbtn wide" onClick={() => run(api.sim.jump(15))} title="Fast-forward 15 sim minutes">+15′</button>
          <div className="speeds" role="group" aria-label="Simulation speed">
            {sim.speeds.map((s) => <button key={s} className={sim.speed === s ? "on" : ""} onClick={() => run(api.sim.speed(s))}>×{s}</button>)}
          </div>
        </div>
        <div className="prog"><div style={{ width: `${frac * 100}%` }} /></div>
        <div className="scrub-axis"><span>{clock(sim.start)}Z</span><span>{sim.scheduled.length} queued · {sim.active_scenarios.length} active</span><span>{clock(end)}Z</span></div>
        <div>
          <div className="cap" style={{ marginBottom: 6 }}>Quick inject</div>
          <div className="chips">
            {QUICK.map(([id, label]) => (
              <button key={id} className="chip" onClick={() => run(api.sim.scenario(id), `${label} injected`)}>{label}</button>
            ))}
            <button className="chip more" onClick={() => setMode("max")}>All options…</button>
          </div>
        </div>
      </div>
    </div>
  );
}
