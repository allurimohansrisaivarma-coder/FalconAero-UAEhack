import { useState } from "react";
import type { Snapshot } from "../types";
import { api } from "../api";
import { clock } from "../sim";
import { Spark, Undo } from "./icons";
import { Assistant } from "./Assistant";

interface Props {
  snap: Snapshot;
  busy: boolean;
  onReview: () => void;
  onGenerate: () => void;
  onUndo: () => void;
  onSelect: (cs: string) => void;
  notify: (t: string, err?: boolean) => void;
}

const TYPE_LABEL: Record<string, string> = {
  separation: "RWY SPACING", runway: "RUNWAY", stand_overlap: "STAND CLASH", stand_closed: "STAND U/S", stand_size: "STAND SIZE",
  timing: "TIMING", overdue: "OVERDUE", unplanned: "ALLOCATION", ground_stop: "GROUND STOP", airspace: "AIRSPACE",
  ramp: "RAMP / LIGHTNING", flow: "ATFM", route: "TAXI ROUTE",
};

export function AlertsPanel({ snap, busy, onReview, onGenerate, onUndo, onSelect, notify }: Props) {
  const [tab, setTab] = useState<"alerts" | "assistant">("alerts");
  const { conflicts } = snap;
  const crit = conflicts.filter((c) => c.severity === "critical").length;
  const opts = snap.options;
  const auto = snap.settings;
  // With routine upkeep off, even an unallocated flight needs a plan the controller approves
  const needsPlan = snap.decision !== null || crit > 0 || (!auto.auto_allocate && conflicts.length > 0);
  const setAuto = (key: "auto_allocate" | "auto_recovery", value: boolean) =>
    api.settings({ [key]: value }).catch((e) => notify(e instanceof Error ? e.message : "Could not change the setting", true));

  return (
    <aside className="panel side">
      <div className="strip-tabs" role="tablist">
        <button role="tab" className={`strip-tab ${tab === "alerts" ? "on" : ""}`} onClick={() => setTab("alerts")}>
          Alerts<span className="n" style={{ color: crit ? "var(--crit)" : undefined }}>{conflicts.length}</span>
        </button>
        <button role="tab" className={`strip-tab ${tab === "assistant" ? "on" : ""}`} onClick={() => setTab("assistant")}>
          Assistant<span className="n">{snap.ai.enabled ? "AI" : "RULES"}</span>
        </button>
      </div>

      {tab === "assistant" ? <Assistant snap={snap} notify={notify} /> : (
        <div className="side-scroll">
          <div className="situation">
            <div className="sec-h" style={{ marginBottom: 0 }}>
              <span className="cap">Situation</span>
              <span className="spacer" />
              {conflicts.length > 0 ? <span className={`tag ${crit ? "crit" : "warn"}`}>{conflicts.length} OPEN</span> : <span className="tag ok">CLEAR</span>}
              <button className="icon-btn" title="Undo last approval" disabled={!snap.can_undo || busy} onClick={onUndo} style={{ opacity: snap.can_undo ? 1 : 0.35 }}><Undo /></button>
            </div>
            <p>{snap.situation}</p>
            {snap.ops.advisories.map((a) => <p key={a} className="adv">{a}</p>)}
          </div>

          <div className="automation">
            <label className="field" title="When on, new flights and ETA/ready-time drift are re-sequenced without approval, within limits: no diversion, no new conflict, at most a few stand changes. Anything larger always waits for approval.">
              <span>Routine upkeep without approval</span>
              <button className={`switch ${auto.auto_allocate ? "on" : ""}`} role="switch" aria-checked={auto.auto_allocate}
                onClick={(e) => { e.preventDefault(); setAuto("auto_allocate", !auto.auto_allocate); }} />
            </label>
            <label className="field" title="When on, recovery options are computed as soon as a critical issue appears. They are never applied without approval.">
              <span>Prepare recovery options automatically</span>
              <button className={`switch ${auto.auto_recovery ? "on" : ""}`} role="switch" aria-checked={auto.auto_recovery}
                onClick={(e) => { e.preventDefault(); setAuto("auto_recovery", !auto.auto_recovery); }} />
            </label>
          </div>

          {(needsPlan || opts) && (
            <div className={`cta-box ${snap.decision ? "decide" : ""}`}>
              {snap.decision && (
                <div className="decide-h">
                  <span className="tag ai">YOUR DECISION</span>
                  <span>{snap.decision}</span>
                </div>
              )}
              {opts && !opts.stale ? (
                <button className="btn ai block" onClick={onReview} disabled={busy}>
                  <Spark /> Review recovery plan{opts.auto ? " (auto-generated)" : ""} <span className="kbd">R</span>
                </button>
              ) : (
                <button className="btn ai block" onClick={onGenerate} disabled={busy || snap.planning}>
                  <Spark /> {snap.planning ? "Computing recovery plans…" : opts?.stale ? "Conditions changed: recompute plan" : "Generate recovery plan"} <span className="kbd">R</span>
                </button>
              )}
              <div className="dim3" style={{ fontSize: 11.5, lineHeight: 1.45 }}>
                {auto.auto_recovery ? "Options are prepared as soon as a decision is needed. " : ""}
                {auto.auto_allocate
                  ? `Upkeep on its own may re-time at most ${auto.max_auto_retimed} flights by ${auto.max_auto_delay} min and change ${auto.max_auto_stand_changes} stands. A new operating condition, or anything larger, waits for your approval.`
                  : "Nothing changes in the plan until you approve it."}
              </div>
            </div>
          )}

          {conflicts.map((c, i) => (
            <div key={i} className={`alert ${c.severity}`} onClick={() => c.flights[0] && onSelect(c.flights[0])}>
              <div className="sev" />
              <div>
                <div className="msg">{c.message}</div>
                <div className="meta"><span>{TYPE_LABEL[c.type] ?? c.type.toUpperCase()}</span><span>{c.resource}</span><span>{clock(c.t)}Z</span></div>
              </div>
            </div>
          ))}
          {conflicts.length === 0 && <div className="empty">No open issues. The approved plan satisfies all modelled runway, stand, ramp and airspace constraints.</div>}

          {snap.forecast.alerts.length > 0 && (
            <>
              <div className="panel-h" style={{ borderTop: "1px solid var(--line)" }}><span className="cap">Outlook</span></div>
              {snap.forecast.alerts.map((a) => (
                <div key={a.key} className={`alert ${a.severity === "critical" ? "critical" : ""}`} style={{ cursor: "default" }}>
                  <div className="sev" style={{ background: a.severity === "info" ? "var(--ai)" : undefined }} />
                  <div><div className="msg">{a.title}</div><div className="meta" style={{ fontFamily: "var(--font)", fontSize: 11.5, lineHeight: 1.4 }}>{a.body}</div></div>
                </div>
              ))}
            </>
          )}

          <div className="panel-h" style={{ borderTop: "1px solid var(--line)" }}><span className="cap">Event log</span></div>
          <div className="log">
            {[...snap.log].reverse().map((l, i) => (
              <div key={i} className={`log-row ${l.kind}`}>
                <span className="t">{clock(l.t)}Z</span>
                <span>{l.text}<span className="src-tag">{l.source}</span></span>
              </div>
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}
