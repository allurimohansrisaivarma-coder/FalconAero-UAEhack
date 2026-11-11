import type { Change, PlansResponse } from "../types";
import { clock } from "../sim";
import { Close, Compare, Spark } from "./icons";

interface Props {
  plans: PlansResponse;
  stale: boolean;
  pick: string;
  onPick: (id: string) => void;
  onApprove: () => void;
  onCompare: () => void;
  onClose: () => void;
  onReject: () => void;
  onRegenerate: () => void;
  busy: boolean;
  onSelectFlight: (cs: string) => void;
}

/** Who has to act on each change: the AOC co-ordinates, but sequence, stands and diversions have different owners. */
function routing(changes: Change[]) {
  let atc = 0, stands = 0, diversions = 0;
  for (const c of changes) {
    if (c.changes.some((m) => m.to === "DIVERT")) { diversions++; continue; }
    if (c.changes.some((m) => m.field === "runway" || m.field === "time" || m.field === "new")) atc++;
    if (c.changes.some((m) => m.field === "stand" || m.field === "wait" || m.field === "new")) stands++;
  }
  return { atc, stands, diversions };
}

function delta(a: number, b: number) {
  const d = Math.round((a - b) * 10) / 10;
  if (d === 0) return <div className="d same">±0 vs rule-based</div>;
  return <div className={`d ${d < 0 ? "good" : "bad"}`}>{d > 0 ? "+" : ""}{d} vs rule-based</div>;
}

export function PlanReview({ plans, stale, pick, onPick, onApprove, onCompare, onClose, onReject, onRegenerate, busy, onSelectFlight }: Props) {
  const opt = plans.options.find((o) => o.id === pick) ?? plans.options[0];
  const base = plans.baseline.kpis;

  if (!opt) {
    return (
      <>
        <div className="scrim" onClick={onClose} />
        <aside className="drawer">
          <div className="drawer-h"><div style={{ flex: 1 }}><h2>No feasible plan found</h2><p>Check runway availability and minima, or relax a restriction, then regenerate.</p></div>
            <button className="icon-btn" onClick={onClose}><Close /></button></div>
          <div className="drawer-f"><button className="btn ai" onClick={onRegenerate} disabled={busy}><Spark /> Regenerate</button></div>
        </aside>
      </>
    );
  }
  const k = opt.kpis;
  const who = routing(opt.changes);

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Recovery plan review">
        <div className="drawer-h">
          <div style={{ flex: 1 }}>
            <div className="cap">Recovery plan #{plans.id} · generated {clock(plans.t)}Z{plans.auto ? " automatically" : ""}</div>
            <h2>Review and approve</h2>
            <p>{plans.situation}</p>
          </div>
          <button className="icon-btn" onClick={onClose} title="Close (Esc)"><Close /></button>
        </div>
        {stale && (
          <div className="stale">
            Conditions changed after this plan was computed. <button className="btn sm ai" onClick={onRegenerate} disabled={busy}><Spark size={13} /> Recompute now</button>
          </div>
        )}
        <div className="drawer-body">
          <div className="options" style={{ ["--n" as string]: plans.options.length }}>
            {plans.options.map((o, i) => (
              <button key={o.id} className={`opt ${o.id === opt.id ? "on" : ""}`} onClick={() => onPick(o.id)}>
                {o.id === plans.recommended && <span className="tag ai rec">RECOMMENDED</span>}
                <div className="name"><span className="kbd">{i + 1}</span>{o.label}</div>
                <div className="tagl">{o.tagline}{o.also.length > 0 && <> · same result as {o.also.join(", ")}</>}</div>
                <div className="mini">
                  <div><span className="v">{o.kpis.avg_delay.toFixed(1)}</span><span className="k">avg delay</span></div>
                  <div><span className="v" style={{ color: o.kpis.unserved ? "var(--warn)" : undefined }}>{o.kpis.unserved}</span><span className="k">diverted / no slot</span></div>
                  <div><span className="v">{o.changes.length}</span><span className="k">flights changed</span></div>
                  <div><span className="v" style={{ color: o.kpis.critical ? "var(--crit)" : "var(--ok)" }}>{o.kpis.critical}</span><span className="k">critical</span></div>
                </div>
              </button>
            ))}
          </div>

          <div className="section">
            <div className="sec-h">
              <span className="cap">Impact vs rule-based allocation</span>
              <span className="spacer" />
              <button className="btn sm ghost" onClick={onCompare}><Compare size={14} /> Full comparison <span className="kbd">C</span></button>
            </div>
            <div className="versus">
              <div><div className="cap">Critical issues</div><div className="v" style={{ color: k.critical ? "var(--crit)" : "var(--ok)" }}>{k.critical}</div>{delta(k.critical, base.critical)}</div>
              <div><div className="cap">Avg delay</div><div className="v">{k.avg_delay.toFixed(1)}<small className="dim3"> min</small></div>{delta(k.avg_delay, base.avg_delay)}</div>
              <div><div className="cap">Total delay</div><div className="v">{k.total_delay}<small className="dim3"> min</small></div>{delta(k.total_delay, base.total_delay)}</div>
              <div><div className="cap">Diverted / no slot</div><div className="v">{k.unserved}</div>{delta(k.unserved, base.unserved)}</div>
            </div>
            <div className="dim3" style={{ fontSize: 11.5, marginTop: 8 }}>
              {opt.summary} Delay figures cover the {k.served} served flights. Solved in {opt.solver.ms} ms ({opt.solver.status.toLowerCase()}{opt.solver.variables ? `, ${opt.solver.variables} variables` : ""}).
            </div>
            {opt.risk && (
              <div className="assume">
                Forecast stress test: if the {opt.risk.basis} conditions ({opt.risk.category}) occur from {clock(opt.risk.t)}Z,{" "}
                {opt.risk.flights === 0 ? "this plan still holds." : `${opt.risk.flights} movements in this plan would need re-planning.`}
              </div>
            )}
            <div className="dim3" style={{ fontSize: 11.5, marginTop: 8 }}>
              On approval, co-ordination goes to: ATC tower {who.atc} runway-sequence changes · stand planning {who.stands} stand changes
              {who.diversions > 0 && <> · airline OCC and ATC {who.diversions} diversion proposals</>}. Clearances stay with ATC.
            </div>
            {plans.assumptions.length > 0 && <div className="assume">{plans.assumptions.map((a) => <div key={a}>Assumption: {a}</div>)}</div>}
          </div>

          <div className="section">
            <div className="sec-h"><span className="cap">What changes</span><span className="count mono dim3">{opt.changes.length}</span></div>
            {opt.changes.length === 0 && <div className="dim">No assignments change.</div>}
            {opt.changes.map((c) => <ChangeRow key={c.callsign} c={c} onClick={() => onSelectFlight(c.callsign)} />)}
          </div>
        </div>
        <div className="drawer-f">
          <button className="btn primary" onClick={onApprove} disabled={busy || k.critical > 0 || stale}>Approve {opt.label.toLowerCase()} plan <span className="kbd">A</span></button>
          <button className="btn" onClick={onClose}>Not now</button>
          <button className="btn ghost" onClick={onReject} disabled={busy} title="Discard these options and keep the approved plan. Logged.">Reject</button>
          <span className="spacer" />
          <span className="dim3" style={{ fontSize: 11.5 }}>Decision support · controller retains authority</span>
        </div>
      </aside>
    </>
  );
}

function ChangeRow({ c, onClick }: { c: Change; onClick: () => void }) {
  return (
    <div className="chg" onClick={onClick} style={{ cursor: "pointer" }}>
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
        <ul>{c.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
      </div>
    </div>
  );
}
