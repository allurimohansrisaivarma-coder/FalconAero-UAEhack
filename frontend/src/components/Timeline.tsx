import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ForecastBin, Layout, PlanOption, PlanRow, Snapshot } from "../types";
import { clock } from "../sim";

export type TlView = "stands" | "runways" | "forecast";

interface Props {
  layout: Layout;
  snap: Snapshot;
  T: number;
  selected: string | null;
  onSelect: (cs: string | null) => void;
  proposal: PlanOption | null;
  view: TlView;
  onView: (v: TlView) => void;
}

const LABEL_W = 50;
const BEFORE = 30;
const AFTER = 150;
const ROW = 15;
const AXIS = 20;

interface Tip { x: number; y: number; body: ReactNode }

export function Timeline({ layout, snap, T, selected, onSelect, proposal, view, onView }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(800);
  const [h, setH] = useState(190);
  const [tip, setTip] = useState<Tip | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => { setW(e.contentRect.width); setH(e.contentRect.height); });
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  useEffect(() => setTip(null), [view]);

  const t0 = T - BEFORE;
  const t1 = T + AFTER;
  const ppm = (w - LABEL_W - 8) / (t1 - t0);
  const X = (t: number) => LABEL_W + (Math.max(t0, Math.min(t1, t)) - t0) * ppm;
  const conflicted = new Set(snap.conflicts.filter((c) => c.type !== "unplanned").flatMap((c) => c.flights));
  const rows = Object.values(snap.plan).filter((r) => snap.flights[r.callsign]?.active || r.t > T - BEFORE);
  const major = [30, 60].find((s) => s * ppm >= 70) ?? 60;
  const ticks: number[] = [];
  for (let t = Math.ceil(t0 / 15) * 15; t <= t1; t += 15) ticks.push(t);

  /** Tooltip position from a pointer event, relative to the scrolling body. */
  const show = (e: React.MouseEvent, body: ReactNode) => {
    const box = ref.current?.getBoundingClientRect();
    if (box) setTip({ x: e.clientX - box.left, y: e.clientY - box.top + (ref.current?.scrollTop ?? 0), body });
  };
  const hover = (body: ReactNode) => ({ onMouseMove: (e: React.MouseEvent) => show(e, body), onMouseLeave: () => setTip(null) });

  const standBar = (r: PlanRow, y: number, hh: number, ghost = false) => {
    if (r.stand_from === null || r.stand_to === null) return null;
    const a = Math.max(r.stand_from, t0 - 5);
    const occEnd = r.stand_to - 10;
    if (r.stand_to < t0 || a > t1) return null;
    const xa = X(a), xb = X(occEnd), xbuf = X(r.stand_to);
    const sel = selected === r.callsign;
    const conf = conflicted.has(r.callsign);
    const stroke = ghost ? "var(--ai)" : sel ? "var(--sel)" : conf ? "var(--crit)" : "none";
    return (
      <g key={(ghost ? "g" : "") + r.callsign} className="tl-bar" onClick={(e) => { e.stopPropagation(); onSelect(sel ? null : r.callsign); }}
        {...hover(<>
          <b>{r.callsign}</b> <span className="dim">{r.type} · Code {r.code}</span>
          <div>{ghost ? "Proposed: " : ""}Stand {r.stand} · {r.kind === "ARR" ? `on-block ${clock(r.onblock)}Z, off ${clock(r.offblock)}Z` : `off-block ${clock(r.offblock)}Z`}</div>
          {r.added > 0 && <div className="warn">+{r.added} min vs {r.kind === "ARR" ? "ETA" : "ready time"}</div>}
          {conf && !ghost && <div className="crit">In conflict</div>}
        </>)}>
        <rect x={xa} y={y} width={Math.max(2, xb - xa - 1)} height={hh} rx={2} fill={ghost ? "var(--ai-dim)" : r.kind === "ARR" ? "var(--arr-fill)" : "var(--dep-fill)"}
          stroke={stroke} strokeWidth={stroke === "none" ? 0 : 1.2} strokeDasharray={ghost ? "3 2" : undefined} />
        {!ghost && <rect x={xa} y={y} width={2} height={hh} fill={r.kind === "ARR" ? "var(--arr)" : "var(--dep)"} />}
        {!ghost && xbuf - xb > 1 && <rect x={xb} y={y + hh / 2 - 1} width={xbuf - xb} height={2} fill="#3a4552" />}
        {xb - xa > 50 && !ghost && <text x={Math.max(xa, LABEL_W) + 6} y={y + hh - 3} className="tl-bar-label">{r.callsign}</text>}
      </g>
    );
  };

  const height = view === "stands" ? AXIS + layout.stands.length * ROW + 4 : AXIS + layout.runways.length * 62 + 6;

  return (
    <section className="tl">
      <div className="panel-h">
        <span className="cap">Resource timeline</span>
        <div className="strip-tabs" style={{ border: 0, marginLeft: 8 }}>
          {(["stands", "runways", "forecast"] as const).map((v) => (
            <button key={v} className={`strip-tab ${view === v ? "on" : ""}`} style={{ height: 33, padding: "0 10px", flex: "none" }} onClick={() => onView(v)}>
              {v}{v === "forecast" && snap.forecast.alerts.length > 0 && <span className="n" style={{ color: "var(--warn)" }}>{snap.forecast.alerts.length}</span>}
            </button>
          ))}
        </div>
        <span className="spacer" />
        <div className="chart-legend">
          {view === "forecast" ? (
            <>
              <span><i style={{ background: "var(--arr)" }} />Arrivals</span>
              <span><i style={{ background: "var(--dep)" }} />Departures</span>
              <span><i className="ln" style={{ background: "var(--text)" }} />Capacity</span>
              <span><i className="ln dash" />TAF worst case</span>
            </>
          ) : (
            <>
              <span><i style={{ background: "var(--arr)" }} />Arrival</span>
              <span><i style={{ background: "var(--dep)" }} />Departure</span>
              {proposal && <span><i style={{ background: "var(--ai)" }} />Proposed</span>}
            </>
          )}
        </div>
        <span className="count">{view === "forecast" ? `NEXT 6 H · ${snap.forecast.bins[0]?.basis ?? ""} THEN TAF` : `${clock(t0)}–${clock(t1)}Z`}</span>
      </div>
      <div className="tl-body" ref={ref} style={{ overflowY: view === "forecast" ? "hidden" : "auto" }}>
        {view === "forecast" ? <ForecastChart bins={snap.forecast.bins} w={w} h={Math.max(150, h)} setTip={setTip} /> : (
          <svg width={w} height={height} style={{ height }} onClick={() => onSelect(null)}>
            <defs>
              <pattern id="tl-blk" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <line x1="0" y1="0" x2="0" y2="5" stroke="#8a6b2b" strokeWidth="1.2" />
              </pattern>
            </defs>
            {view === "stands" && layout.stands.map((st, i) => i % 2 === 0 && <rect key={st.id} x={0} y={AXIS + i * ROW} width={w} height={ROW} fill="#0e1217" />)}
            {ticks.map((t) => (
              <g key={t}>
                <line x1={X(t)} x2={X(t)} y1={t % major === 0 ? 14 : AXIS} y2={height} stroke={t % major === 0 ? "#222a34" : "#171c23"} />
                {t % major === 0 && <text className="tl-axis" x={X(t) + 4} y={12}>{clock(t)}Z</text>}
              </g>
            ))}
            <rect x={LABEL_W} y={AXIS} width={Math.max(0, X(T) - LABEL_W)} height={height - AXIS} fill="#0b0e12" opacity={0.45} pointerEvents="none" />
            {view === "stands" && layout.stands.map((st, i) => {
              const y = AXIS + i * ROW;
              const blk = snap.world.stands[st.id];
              return (
                <g key={st.id}>
                  <text className="tl-row-label" x={8} y={y + 11}>{st.id}<tspan className="sub"> {st.max_code}{st.contact ? "" : " R"}</tspan></text>
                  {blk && <rect x={X(blk.since)} y={y + 2} width={X(blk.until ?? t1) - X(blk.since)} height={ROW - 4} fill="url(#tl-blk)" {...hover(<><b>Stand {st.id} unavailable</b><div>{blk.reason || "No reason given"}{blk.until ? ` · until ${clock(blk.until)}Z` : ""}</div></>)} />}
                  {rows.filter((r) => r.stand === st.id && !r.diverted).map((r) => standBar(r, y + 2, ROW - 4))}
                  {proposal && proposal.changes.filter((c) => c.stand === st.id && c.changes.some((x) => x.field === "stand" || x.field === "new"))
                    .map((c) => proposal.plan[c.callsign]).filter(Boolean).map((r) => standBar(r, y + 2, ROW - 4, true))}
                </g>
              );
            })}
            {view === "runways" && layout.runways.map((rw, i) => {
              const y = AXIS + 6 + i * 62;
              const ro = snap.ops.runways[rw.id];
              const closed = ro?.mode === "CLOSED";
              const slots = rows.filter((r) => r.runway === rw.id && !r.diverted && r.t + r.rwy_occ >= t0 && r.t <= t1).sort((a, b) => a.t - b.t);
              return (
                <g key={rw.id}>
                  <text className="tl-row-label big" x={8} y={y + 24}>{ro?.direction || rw.id}</text>
                  <text className="tl-row-label" x={8} y={y + 36}>{closed ? "CLOSED" : ro?.mode === "ARR" ? "ARR" : ro?.mode === "DEP" ? "DEP" : ro?.mode ?? ""}</text>
                  <rect x={LABEL_W} y={y + 16} width={w - LABEL_W - 8} height={20} rx={2} fill={closed ? "url(#tl-blk)" : "#0e1217"} />
                  {slots.map((r, k) => {
                    const xa = X(r.t), xb = X(r.t + r.rwy_occ);
                    const sel = selected === r.callsign;
                    const conf = conflicted.has(r.callsign);
                    const up = k % 2 === 0;
                    return (
                      <g key={r.callsign} className="tl-bar" onClick={(e) => { e.stopPropagation(); onSelect(sel ? null : r.callsign); }}
                        {...hover(<>
                          <b>{r.callsign}</b> <span className="dim">{r.type}</span>
                          <div>{r.kind === "ARR" ? "Lands" : "Departs"} {clock(r.t)}Z · runway held {r.rwy_occ} min</div>
                          {r.added > 0 && <div className="warn">+{r.added} min</div>}
                          {conf && <div className="crit">In conflict</div>}
                        </>)}>
                        <rect x={xa} y={y + 17} width={Math.max(3, xb - xa - 2)} height={18} rx={2} fill={r.kind === "ARR" ? "var(--arr)" : "var(--dep)"} opacity={r.actual ? 0.45 : 1}
                          stroke={sel ? "var(--sel)" : conf ? "var(--crit)" : "none"} strokeWidth={1.5} />
                        {(slots.length < 28 || sel) && <text x={xa} y={up ? y + 12 : y + 48} className="tl-slot-label" fill={sel ? "var(--sel)" : undefined}>{r.callsign}</text>}
                      </g>
                    );
                  })}
                </g>
              );
            })}
            <line x1={X(T)} x2={X(T)} y1={0} y2={height} stroke="var(--text)" strokeWidth="1" />
            <rect x={X(T) - 20} y={0} width={40} height={14} fill="var(--text)" rx={2} />
            <text x={X(T)} y={10.5} textAnchor="middle" className="tl-now">{clock(T)}Z</text>
          </svg>
        )}
        {tip && <div className="chart-tip" style={{ left: tip.x + 14 > w - 290 ? tip.x - 290 : tip.x + 14, top: Math.max(4, Math.min(tip.y - 12, (ref.current?.scrollTop ?? 0) + h - 104)) }}>{tip.body}</div>}
      </div>
    </section>
  );
}

/** Round a maximum up to a clean axis top with three or four steps. */
function niceMax(v: number): { max: number; step: number } {
  const step = [1, 2, 5, 10, 15, 20, 30, 60, 120].find((s) => v / s <= 4) ?? 240;
  return { max: Math.max(step, Math.ceil(v / step) * step), step };
}

/**
 * Two charts on one time axis, each with its own single scale:
 * movements per 15 minutes against capacity, and the delay that follows from any shortfall.
 */
function ForecastChart({ bins, w, h, setTip }: { bins: ForecastBin[]; w: number; h: number; setTip: (t: Tip | null) => void }) {
  const [at, setAt] = useState<number | null>(null);
  if (!bins.length) return <div className="empty">No forecast yet.</div>;
  const L = 50, R = 16, gap = 22, bot = 18, top = 14;
  const iw = w - L - R;
  const ih = h - top - bot - gap;
  const h1 = Math.round(ih * 0.6), h2 = ih - h1;
  const y1 = top, y2 = top + h1 + gap;
  const bw = iw / bins.length;
  const cap = (b: ForecastBin) => (b.arr_cap + b.dep_cap) / 4;
  const capRisk = (b: ForecastBin) => (b.arr_cap_risk + b.dep_cap_risk) / 4;
  const mv = niceMax(Math.max(4, ...bins.map((b) => Math.max(b.arr_demand + b.dep_demand, cap(b)))));
  const dl = niceMax(Math.max(15, ...bins.map((b) => Math.max(b.arr_delay, b.dep_delay))));
  const Y1 = (v: number) => y1 + h1 - (Math.min(v, mv.max) / mv.max) * h1;
  const Y2 = (v: number) => y2 + h2 - (Math.min(v, dl.max) / dl.max) * h2;
  const cx = (i: number) => L + i * bw + bw / 2;
  const col = Math.min(16, bw - 6);
  const step = (f: (b: ForecastBin) => number) => bins.map((b, i) => `${i ? "L" : "M"}${L + i * bw} ${Y1(f(b))} L${L + (i + 1) * bw} ${Y1(f(b))}`).join(" ");
  const line = (k: "arr_delay" | "dep_delay") => bins.map((b, i) => `${i ? "L" : "M"}${cx(i)} ${Y2(b[k])}`).join(" ");
  const area = (k: "arr_delay" | "dep_delay") => `${line(k)} L${cx(bins.length - 1)} ${y2 + h2} L${cx(0)} ${y2 + h2} Z`;
  const peak = bins.reduce((m, b, i) => (Math.max(b.arr_delay, b.dep_delay) > Math.max(bins[m].arr_delay, bins[m].dep_delay) ? i : m), 0);
  const peakV = Math.max(bins[peak].arr_delay, bins[peak].dep_delay);
  const lvpRuns: [number, number][] = [];
  bins.forEach((b, i) => { if (b.lvp) { const last = lvpRuns[lvpRuns.length - 1]; if (last && last[1] === i - 1) last[1] = i; else lvpRuns.push([i, i]); } });

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - box.left - L) / bw);
    if (i < 0 || i >= bins.length) { setAt(null); setTip(null); return; }
    const b = bins[i];
    const over = b.arr_demand + b.dep_demand > cap(b) + 0.01;
    setAt(i);
    setTip({ x: e.clientX - box.left, y: e.clientY - box.top, body: (
      <>
        <b>{clock(b.t)}Z</b> <span className="dim">{b.category}{b.risk_category !== b.category ? ` · risk ${b.risk_category}` : ""} · {b.basis}</span>
        <div className="row"><i style={{ background: "var(--arr)" }} />Arrivals<b>{b.arr_demand}</b><span className="dim">of {(b.arr_cap / 4).toFixed(1)} · delay {b.arr_delay} min</span></div>
        <div className="row"><i style={{ background: "var(--dep)" }} />Departures<b>{b.dep_demand}</b><span className="dim">of {(b.dep_cap / 4).toFixed(1)} · delay {b.dep_delay} min</span></div>
        <div className="row"><i className="none" />Stands<b>{b.stands_used}</b><span className="dim">of {b.stands_avail} in use</span></div>
        {over && <div className="crit">Demand above capacity</div>}
      </>
    ) });
  };

  return (
    <svg width={w} height={h} onMouseMove={onMove} onMouseLeave={() => { setAt(null); setTip(null); }}>
      {lvpRuns.map(([a, b]) => (
        <g key={a}>
          <rect x={L + a * bw} y={y1} width={(b - a + 1) * bw} height={h1 + gap + h2} fill="rgba(232,169,58,0.07)" />
          <text className="tl-axis" x={L + a * bw + 4} y={y1 + 10} fill="var(--warn)">LVP</text>
        </g>
      ))}
      <text className="chart-title" x={L} y={y1 - 4}>Movements per 15 min</text>
      <text className="chart-title" x={L} y={y2 - 6}>Predicted delay, min</text>
      {Array.from({ length: Math.round(mv.max / mv.step) + 1 }, (_, i) => i * mv.step).map((v) => (
        <g key={v}><line x1={L} x2={w - R} y1={Y1(v)} y2={Y1(v)} stroke={v ? "#1a2028" : "#2e3743"} /><text className="tl-axis" x={L - 6} y={Y1(v) + 3} textAnchor="end">{v}</text></g>
      ))}
      {Array.from({ length: Math.round(dl.max / dl.step) + 1 }, (_, i) => i * dl.step).map((v) => (
        <g key={v}><line x1={L} x2={w - R} y1={Y2(v)} y2={Y2(v)} stroke={v ? "#1a2028" : "#2e3743"} /><text className="tl-axis" x={L - 6} y={Y2(v) + 3} textAnchor="end">{v}</text></g>
      ))}
      {bins.map((b, i) => {
        const x = cx(i) - col / 2;
        const ya = Y1(b.arr_demand), yd = Y1(b.arr_demand + b.dep_demand);
        const over = b.arr_demand + b.dep_demand > cap(b) + 0.01;
        return (
          <g key={b.t}>
            {b.arr_demand > 0 && <path d={bar(x, ya, col, y1 + h1 - ya, b.dep_demand === 0)} fill="var(--arr)" />}
            {b.dep_demand > 0 && <path d={bar(x, yd, col, ya - yd - (b.arr_demand > 0 ? 2 : 0), true)} fill="var(--dep)" />}
            {over && <path d={`M${cx(i) - 4} ${yd - 5} L${cx(i) + 4} ${yd - 5} L${cx(i)} ${yd - 11} Z`} fill="var(--crit)" />}
            {i % 4 === 0 && <text className="tl-axis" x={L + i * bw + 2} y={h - 5}>{clock(b.t)}Z</text>}
          </g>
        );
      })}
      <path d={step(capRisk)} fill="none" stroke="var(--warn)" strokeWidth="1.5" strokeDasharray="4 3" />
      <path d={step(cap)} fill="none" stroke="var(--text)" strokeWidth="2" strokeLinejoin="round" />
      <path d={area("arr_delay")} fill="var(--arr)" opacity="0.1" />
      <path d={line("dep_delay")} fill="none" stroke="var(--dep)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <path d={line("arr_delay")} fill="none" stroke="var(--arr)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      {peakV > 0 && (
        <g>
          <circle cx={cx(peak)} cy={Y2(peakV)} r={4} fill={bins[peak].arr_delay >= bins[peak].dep_delay ? "var(--arr)" : "var(--dep)"} stroke="var(--bg-1)" strokeWidth="2" />
          <text className="chart-value" x={cx(peak) + (peak > bins.length - 5 ? -8 : 8)} y={Y2(peakV) + (Y2(peakV) < y2 + 12 ? 12 : -5)} textAnchor={peak > bins.length - 5 ? "end" : "start"}>peak {peakV} min</text>
        </g>
      )}
      {at !== null && <line x1={cx(at)} x2={cx(at)} y1={y1} y2={y2 + h2} stroke="var(--text-2)" strokeWidth="1" pointerEvents="none" />}
    </svg>
  );
}

/** Column with a 3px rounded top (the data end) and a square base. */
function bar(x: number, y: number, w: number, h: number, round: boolean): string {
  if (h <= 0) return "";
  const r = round ? Math.min(3, h, w / 2) : 0;
  return `M${x} ${y + h} L${x} ${y + r} Q${x} ${y} ${x + r} ${y} L${x + w - r} ${y} Q${x + w} ${y} ${x + w} ${y + r} L${x + w} ${y + h} Z`;
}
