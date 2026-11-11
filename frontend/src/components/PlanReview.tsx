import { useState } from "react";
import type { Change, Impact, PlansResponse } from "../types";
import { clock } from "../sim";
import { Compare, Spark } from "./icons";

interface Props {
  plans: PlansResponse;
  /** Why the engine needs the controller (snap.decision), if it still does. */
  decision: string | null;
  stale: boolean;
  pick: string;
  onPick: (id: string) => void;
  onApprove: () => void;
  onIgnore: () => void;
  onCompare: () => void;
  onRegenerate: () => void;
  busy: boolean;
  planning: boolean;
  onSelectFlight: (cs: string) => void;
}

const SHOW = 6;

/**
 * The decision the controller has to take. It stays on screen until they either approve an option
 * or ignore the warning (the approved plan stays in force and the engine offers again later).
 */
export function PlanReview({ plans, decision, stale, pick, onPick, onApprove, onIgnore, onCompare, onRegenerate, busy, planning, onSelectFlight }: Props) {
  const [all, setAll] = useState(false);
  const opt = plans.options.find((o) => o.id === pick) ?? plans.options[0];
  const base = plans.baseline.kpis;
  const changes = opt ? (all ? opt.changes : opt.changes.slice(0, SHOW)) : [];

  return (
    <div className="decision-wrap">
      <section className="decision" role="alertdialog" aria-modal="true" aria-labelledby="decision-title">
        <header className="decision-h">
          <div className="row">
            <span className="tag crit">DECISION NEEDED</span>
            {decision && <span className="dim">{decision}</span>}
            <span className="spacer" />
            <span className="mono dim3">PLAN #{plans.id} · {clock(plans.t)}Z{plans.auto ? " · AUTO" : ""}</span>
          </div>
          <h2 id="decision-title">{plans.situation}</h2>
        </header>

        <ImpactTiles impact={plans.impact} critical={plans.current.kpis.critical} />

        {stale && (
          <div className="stale">
            {planning ? "Recomputing for the latest conditions…" : "Conditions changed after this plan was computed."}
            {!planning && <button className="btn sm ai" onClick={onRegenerate} disabled={busy}><Spark size={13} /> Recompute</button>}
          </div>
        )}

        {!opt ? (
          <div className="decision-body"><p className="dim">No feasible plan: check runway availability and minima, or relax a restriction, then recompute.</p></div>
        ) : (
          <div className="decision-body">
            <div className="options" style={{ ["--n" as string]: plans.options.length }}>
              {plans.options.map((o, i) => (
                <button key={o.id} className={`opt ${o.id === opt.id ? "on" : ""}`} onClick={() => onPick(o.id)}>
                  {o.id === plans.recommended && <span className="tag ai rec">RECOMMENDED</span>}
                  <div className="name"><span className="kbd">{i + 1}</span>{o.label}</div>
                  <div className="mini">
                    <div><span className="v">{o.kpis.avg_delay.toFixed(1)}′</span><span className="k">avg delay</span></div>
                    <div><span className="v">{o.changes.length}</span><span className="k">flights change</span></div>
                    <div><span className="v" style={{ color: o.kpis.unserved ? "var(--warn)" : undefined }}>{o.kpis.unserved}</span><span className="k">no slot / divert</span></div>
                    <div><span className="v" style={{ color: o.kpis.critical ? "var(--crit)" : "var(--ok)" }}>{o.kpis.critical}</span><span className="k">critical left</span></div>
                  </div>
                </button>
              ))}
            </div>

            <div className="decision-sum mono">
              <span>vs rule-based: <Delta a={opt.kpis.avg_delay} b={base.avg_delay} unit="′ avg" /></span>
              <span><Delta a={opt.kpis.total_delay} b={base.total_delay} unit="′ total" /></span>
              {opt.risk && <span style={{ color: opt.risk.flights ? "var(--warn)" : undefined }}>TAF worst case {clock(opt.risk.t)}Z: {opt.risk.flights ? `${opt.risk.flights} re-plans` : "holds"}</span>}
            </div>

            <div className="decision-chg">
              {opt.changes.length === 0 && <div className="dim">No assignments change.</div>}
              {changes.map((c) => <ChangeRow key={c.callsign} c={c} onClick={() => onSelectFlight(c.callsign)} />)}
              {opt.changes.length > SHOW && (
                <button className="btn sm ghost" onClick={() => setAll((x) => !x)}>{all ? "Show fewer" : `Show all ${opt.changes.length} changes`}</button>
              )}
            </div>
          </div>
        )}

        <footer className="decision-f">
          {opt && (
            <button className="btn primary" onClick={onApprove} disabled={busy || planning || opt.kpis.critical > 0 || stale}
              title={opt.kpis.critical > 0 ? "This option still leaves critical issues" : undefined}>
              Approve {opt.label.toLowerCase()} <span className="kbd">A</span>
            </button>
          )}
          <button className="btn" onClick={onIgnore} disabled={busy} title="Keep the approved plan as it is. Logged; offered again in 15 min if the issue remains.">
            Ignore warning <span className="kbd">I</span>
          </button>
          {opt && <button className="btn ghost" onClick={onCompare}><Compare size={14} /> Compare <span className="kbd">C</span></button>}
          <span className="spacer" />
          <span className="dim3" style={{ fontSize: 11.5 }}>Controller retains authority</span>
        </footer>
      </section>
    </div>
  );
}

function ImpactTiles({ impact, critical }: { impact?: Impact; critical: number }) {
  const n = impact?.flights.length ?? 0;
  const list = (xs: string[], max: number) => (xs.length ? xs.slice(0, max).join(" ") + (xs.length > max ? ` +${xs.length - max}` : "") : "none");
  return (
    <div className="impact">
      <div><span className="k">Aircraft affected</span><span className="v">{n}</span><span className="s">{impact ? `${impact.arr} arr · ${impact.dep} dep` : "—"}</span></div>
      <div><span className="k">Runways affected</span><span className="v">{impact?.runways.length ?? 0}</span><span className="s">{list(impact?.runways ?? [], 4)}</span></div>
      <div><span className="k">Stands affected</span><span className="v">{impact?.stands.length ?? 0}</span><span className="s">{list(impact?.stands ?? [], 4)}</span></div>
      <div><span className="k">Critical now</span><span className="v" style={{ color: critical ? "var(--crit)" : "var(--ok)" }}>{critical}</span><span className="s">in the approved plan</span></div>
    </div>
  );
}

function Delta({ a, b, unit }: { a: number; b: number; unit: string }) {
  const d = Math.round((a - b) * 10) / 10;
  return <b className={d < 0 ? "good" : d > 0 ? "bad" : ""}>{d > 0 ? "+" : d === 0 ? "±" : ""}{d}{unit}</b>;
}

function ChangeRow({ c, onClick }: { c: Change; onClick: () => void }) {
  return (
    <div className="chg" onClick={onClick} style={{ cursor: "pointer" }} title={c.reasons.join("\n")}>
      <div>
        <div className="cs" style={{ color: c.priority === "emergency" ? "var(--crit)" : undefined }}>{c.callsign}</div>
        <div className="ty">{c.type} · {c.kind}</div>
      </div>
      <div>
        <div className="moves">
          {c.changes.map((m, i) => {
            if (m.field === "time") {
              const late = Number(m.to) > Number(m.from);
              return <span key={i} className={`mv ${late ? "late" : ""}`}>{clock(Number(m.from))}<span className="arrow">→</span>{clock(Number(m.to))}</span>;
            }
            if (m.field === "wait") return <span key={i} className="mv late">GROUND WAIT {m.to}′</span>;
            if (m.field === "new") return <span key={i} className="mv">NEW<span className="arrow">→</span>{c.runway} · {c.stand}</span>;
            return <span key={i} className="mv">{m.field === "runway" ? "RWY " : "STD "}{m.from ?? "—"}<span className="arrow">→</span>{m.to}</span>;
          })}
        </div>
        {c.reasons[0] && <div className="why">{c.reasons[0]}</div>}
      </div>
    </div>
  );
}
