import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Notification, Snapshot } from "../types";
import { clock } from "../sim";

interface Props {
  snap: Snapshot;
  onAction: (n: Notification) => void;
}

const CATS = ["all", "conflict", "plan", "forecast", "weather", "resource", "flight", "data"] as const;
const CAT_LABEL: Record<string, string> = {
  all: "All", conflict: "Plan issues", plan: "Recovery", forecast: "Forecast", weather: "Weather", resource: "Resources", flight: "Flights", data: "Data quality",
};

export function NotificationsView({ snap, onAction }: Props) {
  const [items, setItems] = useState<Notification[]>([]);
  const [cat, setCat] = useState<(typeof CATS)[number]>("all");
  const [showResolved, setShowResolved] = useState(false);
  const [sev, setSev] = useState<"all" | "critical" | "warning">("all");

  useEffect(() => {
    let alive = true;
    api.notifications().then((r) => alive && setItems(r.items)).catch(() => undefined);
    return () => { alive = false; };
  }, [snap.version, snap.notifications.unread]);

  const list = useMemo(() => items.filter((n) =>
    (cat === "all" || n.category === cat) && (showResolved || n.status !== "resolved") && (sev === "all" || n.severity === sev)), [items, cat, showResolved, sev]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of items) if (n.status !== "resolved") c[n.category] = (c[n.category] ?? 0) + 1;
    return c;
  }, [items]);

  const ack = async (n: Notification) => {
    await api.ack(n.id).catch(() => undefined);
    setItems((xs) => xs.map((x) => (x.id === n.id ? { ...x, read: true, status: x.status === "open" ? "ack" : x.status } : x)));
  };

  return (
    <main className="notif">
      <div className="notif-side">
        <div className="cap" style={{ padding: "14px 14px 8px" }}>Categories</div>
        {CATS.map((c) => (
          <button key={c} className={`nav-item ${cat === c ? "on" : ""}`} onClick={() => setCat(c)}>
            {CAT_LABEL[c]}<span className="n">{c === "all" ? Object.values(counts).reduce((a, b) => a + b, 0) : counts[c] ?? 0}</span>
          </button>
        ))}
        <div className="cap" style={{ padding: "18px 14px 8px" }}>Severity</div>
        {(["all", "critical", "warning"] as const).map((s) => (
          <button key={s} className={`nav-item ${sev === s ? "on" : ""}`} onClick={() => setSev(s)}>{s === "all" ? "Any" : s[0].toUpperCase() + s.slice(1)}</button>
        ))}
        <label className="nav-check"><input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} /> Show resolved</label>
      </div>
      <section className="notif-main">
        <div className="notif-h">
          <div>
            <h2>Notifications</h2>
            <p className="dim">The engine raises, updates and auto-resolves these from live events, forecasts and plan checks.</p>
          </div>
          <span className="spacer" />
          <button className="btn sm" onClick={() => api.ackAll().then(() => setItems((xs) => xs.map((x) => ({ ...x, read: true, status: x.status === "open" ? "ack" : x.status }))))}>Acknowledge all</button>
        </div>
        {snap.options && !snap.options.stale && (
          <div className="notif-banner">
            <span className="tag ai">RECOVERY PLAN READY</span>
            <span>{snap.options.labels.join(" · ")} generated at {clock(snap.options.t)}Z{snap.options.auto ? " (automatic)" : ""}.</span>
            <span className="spacer" />
            <button className="btn sm ai" onClick={() => onAction({ action: { type: "review_plan" } } as Notification)}>Review &amp; approve</button>
          </div>
        )}
        <div className="notif-list">
          {list.length === 0 && <div className="empty">Nothing here. FalconAero will notify you when conditions change.</div>}
          {list.map((n) => (
            <article key={n.id} className={`nrow ${n.severity} ${n.status} ${n.read ? "" : "unread"}`}>
              <div className="sev" />
              <div className="nbody">
                <div className="nt">
                  {!n.read && n.status !== "resolved" && <span className="unread-dot" />}
                  <span className="title">{n.title}</span>
                  {n.count > 1 && <span className="tag">×{n.count}</span>}
                </div>
                <div className="nb">{n.body}</div>
                <div className="nm mono">
                  <span>{n.severity.toUpperCase()}</span><span>{CAT_LABEL[n.category] ?? n.category}</span>
                  <span>raised {clock(n.t)}Z</span>{n.updated !== n.t && <span>updated {clock(n.updated)}Z</span>}
                  {n.status === "resolved" && <span style={{ color: "var(--ok)" }}>resolved {clock(n.resolved_t)}Z · {n.resolution}</span>}
                  {n.status === "ack" && <span>acknowledged</span>}
                </div>
              </div>
              <div className="nact">
                {n.action && n.status !== "resolved" && (
                  <button className="btn sm ai" onClick={() => { ack(n); onAction(n); }}>
                    {n.action.type === "review_plan" ? "Review plan" : n.action.type === "focus_flight" ? `Show ${n.action.ref}` : n.action.type === "forecast" ? "Open forecast" : "Open"}
                  </button>
                )}
                {n.status === "open" && <button className="btn sm ghost" onClick={() => ack(n)}>Acknowledge</button>}
              </div>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
