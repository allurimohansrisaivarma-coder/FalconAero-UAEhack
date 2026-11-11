import type { Kpis, PlansResponse } from "../types";
import { Close } from "./icons";

interface Props { plans: PlansResponse; pick: string; onClose: () => void }

const METRICS: { key: keyof Kpis; label: string; unit?: string }[] = [
  { key: "critical", label: "Critical safety issues" },
  { key: "unserved", label: "Flights diverted or without a slot" },
  { key: "avg_delay", label: "Average delay of served flights", unit: "min" },
  { key: "max_delay", label: "Worst single delay", unit: "min" },
  { key: "total_delay", label: "Total delay", unit: "min" },
  { key: "delayed_15", label: "Flights delayed 15 min or more" },
  { key: "avg_arr_delay", label: "Average arrival holding", unit: "min" },
  { key: "ground_waits", label: "Ground waits for stands" },
  { key: "taxi_total", label: "Total taxi time", unit: "min" },
  { key: "remote_stands", label: "Arrivals on remote stands" },
];

const BUCKETS: { label: string; test: (d: number) => boolean }[] = [
  { label: "On time", test: (d) => d === 0 },
  { label: "1–5 min", test: (d) => d >= 1 && d <= 5 },
  { label: "6–14 min", test: (d) => d >= 6 && d <= 14 },
  { label: "15–29 min", test: (d) => d >= 15 && d <= 29 },
  { label: "30 min +", test: (d) => d >= 30 },
];

const RULE = "#8793a1";       // the rule-based plan: neutral, it is the reference
const OURS = "var(--ai)";     // FalconAero's proposal, the same cyan as on the map and timeline

export function CompareView({ plans, pick, onClose }: Props) {
  const opt = plans.options.find((o) => o.id === pick) ?? plans.options[0];
  if (!opt) return null;
  const base = plans.baseline.kpis;
  const cur = plans.current.kpis;
  const upcoming = (cs: string) => opt.plan[cs].t >= plans.t && !opt.plan[cs].actual && !opt.plan[cs].diverted && opt.plan[cs].runway;
  const all = Object.keys(opt.plan).filter(upcoming);
  const delays = all.map((cs) => ({ cs, o: opt.plan[cs].added, b: plans.baseline.plan[cs]?.added ?? 0, known: !!plans.baseline.plan[cs]?.runway }));
  const top = delays.filter((d) => d.o > 0 || d.b > 0).sort((x, y) => Math.max(y.o, y.b) - Math.max(x.o, x.b)).slice(0, 14);
  const counts = BUCKETS.map((k) => ({ label: k.label, b: delays.filter((d) => d.known && k.test(d.b)).length, o: delays.filter((d) => k.test(d.o)).length }));

  return (
    <div className="modal-wrap" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Plan comparison">
        <div className="modal-h">
          <div>
            <div className="cap">Same world state · same committed movements · same operating rules</div>
            <h2>Rule-based allocation vs FalconAero ({opt.label})</h2>
          </div>
          <span className="spacer" />
          <button className="icon-btn" onClick={onClose}><Close /></button>
        </div>
        <table className="cmp">
          <thead><tr><th>Metric</th><th>Approved plan, unresolved</th><th>Rule-based (first-come, first-served)</th><th className="fa">FalconAero</th><th>Difference</th></tr></thead>
          <tbody>
            {METRICS.map((m) => {
              const b = Number(base[m.key]); const o = Number(opt.kpis[m.key]);
              const d = Math.round((o - b) * 10) / 10;
              return (
                <tr key={m.key}>
                  <td>{m.label}</td>
                  <td className="dim">{Number(cur[m.key])}{m.unit ? ` ${m.unit}` : ""}</td>
                  <td>{b}{m.unit ? ` ${m.unit}` : ""}</td>
                  <td className="ours">{o}{m.unit ? ` ${m.unit}` : ""}</td>
                  <td className={d < 0 ? "win" : d > 0 ? "lose" : "dim"}>{d === 0 ? "same" : `${d > 0 ? "▲ +" : "▼ −"}${Math.abs(d)}`}</td>
                </tr>
              );
            })}
          </tbody>
        </table>

        <div className="cmp-charts">
          <figure>
            <figcaption>
              <span className="cap">How delay is spread across flights</span>
              <span className="chart-legend"><span><i style={{ background: RULE }} />Rule-based</span><span><i style={{ background: OURS }} />FalconAero</span></span>
            </figcaption>
            <Distribution counts={counts} />
          </figure>
          <figure>
            <figcaption><span className="cap">Most delayed flights, minutes</span></figcaption>
            {top.length === 0 ? <div className="dim" style={{ padding: "18px 0" }}>No flight is delayed in either plan.</div> : <Dumbbells rows={top} />}
          </figure>
        </div>

        <p className="cmp-note">
          Both plans obey the same operating rules (runway modes, separation, ATFM caps, ramp suspension, holding limit). The rule-based policy
          handles one movement at a time without look-ahead; FalconAero solves runway slots and stands together, starting from the rule-based
          plan. Delay figures cover served flights only, so read them with the diverted / no-slot row. Figures come from the engine's current
          world state; they are not measurements from live airport operations.
        </p>
      </div>
    </div>
  );
}

/** Grouped columns: flights per delay band in each plan. One scale, counts labelled on the caps. */
function Distribution({ counts }: { counts: { label: string; b: number; o: number }[] }) {
  const W = 420, H = 190, L = 8, top = 18, bot = 26;
  const ih = H - top - bot;
  const max = Math.max(1, ...counts.flatMap((c) => [c.b, c.o]));
  const slot = (W - L * 2) / counts.length;
  const bw = Math.min(22, slot / 2 - 8);
  const Y = (v: number) => top + ih - (v / max) * ih;
  const col = (x: number, v: number, fill: string) => {
    const h = top + ih - Y(v);
    if (h <= 0) return null;
    const r = Math.min(4, h);
    return <path d={`M${x} ${top + ih} L${x} ${Y(v) + r} Q${x} ${Y(v)} ${x + r} ${Y(v)} L${x + bw - r} ${Y(v)} Q${x + bw} ${Y(v)} ${x + bw} ${Y(v) + r} L${x + bw} ${top + ih} Z`} fill={fill} />;
  };
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Flights per delay band, rule-based against FalconAero">
      <line x1={L} x2={W - L} y1={top + ih} y2={top + ih} stroke="#2e3743" />
      {counts.map((c, i) => {
        const cx = L + slot * i + slot / 2;
        return (
          <g key={c.label}>
            <title>{`${c.label}: rule-based ${c.b} flights, FalconAero ${c.o} flights`}</title>
            {col(cx - bw - 1, c.b, RULE)}
            {col(cx + 1, c.o, OURS)}
            <text className="chart-value dim" x={cx - bw / 2 - 1} y={Y(c.b) - 5} textAnchor="middle">{c.b}</text>
            <text className="chart-value" x={cx + bw / 2 + 1} y={Y(c.o) - 5} textAnchor="middle">{c.o}</text>
            <text className="tl-axis" x={cx} y={H - 8} textAnchor="middle">{c.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** One row per flight: a dot for each plan joined by a line, so the gap is the saving (or the cost). */
function Dumbbells({ rows }: { rows: { cs: string; o: number; b: number }[] }) {
  const W = 420, L = 70, R = 34, rowH = 17, top = 20;
  const H = top + rows.length * rowH + 6;
  const raw = Math.max(5, ...rows.map((r) => Math.max(r.o, r.b)));
  const step = [5, 10, 15, 30, 60].find((s) => raw / s <= 5) ?? 120;
  const max = Math.ceil(raw / step) * step;
  const X = (v: number) => L + (v / max) * (W - L - R);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Delay per flight in each plan">
      {Array.from({ length: max / step + 1 }, (_, i) => i * step).map((v) => (
        <g key={v}><line x1={X(v)} x2={X(v)} y1={top - 4} y2={H - 4} stroke={v ? "#1a2028" : "#2e3743"} /><text className="tl-axis" x={X(v)} y={top - 8} textAnchor="middle">{v}</text></g>
      ))}
      {rows.map((r, i) => {
        const y = top + i * rowH + rowH / 2;
        const better = r.o < r.b;
        return (
          <g key={r.cs}>
            <title>{`${r.cs}: rule-based ${r.b} min, FalconAero ${r.o} min`}</title>
            <text className="chart-value" x={L - 10} y={y + 3.5} textAnchor="end">{r.cs}</text>
            <line x1={X(r.o)} x2={X(r.b)} y1={y} y2={y} stroke={better ? "#3a4552" : "var(--crit)"} strokeWidth="2" strokeOpacity={better ? 1 : 0.6} />
            <circle cx={X(r.b)} cy={y} r={4.5} fill={RULE} stroke="var(--bg-1)" strokeWidth="2" />
            <circle cx={X(r.o)} cy={y} r={4.5} fill={OURS} stroke="var(--bg-1)" strokeWidth="2" />
            <text className="tl-axis" x={X(Math.max(r.o, r.b)) + 9} y={y + 3.5}>{r.o === r.b ? r.o : `${r.b} → ${r.o}`}</text>
          </g>
        );
      })}
    </svg>
  );
}
