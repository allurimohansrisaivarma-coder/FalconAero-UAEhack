import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { useLive } from "./live";
import { runwayGeo } from "./airspace";
import { buildTraffic, type Ac } from "./traffic";
import type { Layout, Notification, PlansResponse } from "./types";
import { TopBar, type Tab } from "./components/TopBar";
import { FlightStrips } from "./components/FlightStrips";
import { AirfieldMap } from "./components/AirfieldMap";
import { AirspaceMap } from "./components/AirspaceMap";
import { Timeline, type TlView } from "./components/Timeline";
import { AlertsPanel } from "./components/AlertsPanel";
import { PlanReview } from "./components/PlanReview";
import { CompareView } from "./components/CompareView";
import { SimPlayer } from "./components/SimPlayer";
import { NotificationsView } from "./components/NotificationsView";

export default function App() {
  const { snap, sim, setSim, connected, T } = useLive();
  const [layout, setLayout] = useState<Layout | null>(null);
  const [tab, setTab] = useState<Tab>("ops");
  const [selected, setSelected] = useState<string | null>(null);
  const [plans, setPlans] = useState<PlansResponse | null>(null);
  const [pick, setPick] = useState("");
  const [compare, setCompare] = useState(false);
  const [busy, setBusy] = useState(false);
  const [tlView, setTlView] = useState<TlView>("stands");
  const [mapView, setMapView] = useState<"airspace" | "surface">("airspace");
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null);

  const notify = useCallback((text: string, err = false) => {
    setToast({ text, err });
    window.setTimeout(() => setToast((t) => (t?.text === text ? null : t)), 4200);
  }, []);

  useEffect(() => { api.layout().then(setLayout).catch(() => notify("Cannot reach the FalconAero engine on :8000.", true)); }, [notify]);

  const run = useCallback(async <R,>(fn: () => Promise<R>): Promise<R | undefined> => {
    setBusy(true);
    try { return await fn(); } catch (e) { notify(e instanceof Error ? e.message : "Request failed", true); return undefined; } finally { setBusy(false); }
  }, [notify]);

  const openReview = () => run(async () => {
    let p = await api.latestPlans();
    if (!("options" in p) || !p.options) p = await api.plans();
    const pr = p as PlansResponse;
    setPlans(pr);
    setPick(pr.recommended ?? pr.options[0]?.id ?? "");
    setTab("ops");
  });
  const regenerate = () => run(async () => {
    const p = await api.plans();
    setPlans(p);
    setPick(p.recommended ?? p.options[0]?.id ?? "");
  });
  const approve = () => run(async () => {
    if (!plans) return;
    const opt = plans.options.find((o) => o.id === pick);
    await api.approve(pick);
    setPlans(null); setCompare(false);
    notify(`${opt?.label ?? "Plan"} approved · ${opt?.changes.length ?? 0} flights updated`);
  });
  const ignore = () => run(async () => {
    // Nothing pending on the engine side any more (withdrawn or already handled): just close
    if (snap?.options) await api.reject();
    setPlans(null); setCompare(false);
    notify("Warning ignored · approved plan kept · offered again in 15 min if it persists");
  });
  const undo = () => run(async () => { await api.undo(); notify("Previous plan restored"); });

  // Keep an open review in sync: if the engine produced a newer plan set, refresh it
  useEffect(() => {
    if (plans && snap?.options && snap.options.id !== plans.id && !snap.options.stale) {
      api.latestPlans().then((p) => { if ("options" in p && p.options) { setPlans(p as PlansResponse); setPick((p as PlansResponse).recommended ?? ""); } }).catch(() => undefined);
    }
  }, [snap?.options?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // A fresh set of options for a pending decision pops the review up on its own, once per set
  const [offered, setOffered] = useState<number | null>(null);
  useEffect(() => {
    const o = snap?.options;
    if (!plans && !busy && snap?.decision && o && !o.stale && o.id !== offered) { setOffered(o.id); openReview(); }
  }, [snap?.options?.id, snap?.options?.stale, snap?.decision]); // eslint-disable-line react-hooks/exhaustive-deps

  // The engine withdrew the proposal (the issue settled itself): nothing is left to decide
  useEffect(() => {
    if (plans && !busy && snap && !snap.options && !snap.decision && !snap.planning) {
      setPlans(null); setCompare(false);
      notify("Situation cleared · recovery plan no longer needed");
    }
  }, [snap?.options, snap?.decision, snap?.planning]); // eslint-disable-line react-hooks/exhaustive-deps

  const onAction = (n: Notification) => {
    const a = n.action;
    if (!a) return;
    if (a.type === "review_plan") openReview();
    else if (a.type === "focus_flight" && a.ref) { setTab("ops"); setSelected(a.ref); }
    else if (a.type === "forecast") { setTab("ops"); setTlView("forecast"); }
  };

  // Keyboard shortcuts (Space handled by the sim player)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      // Escape never dismisses a pending decision: only Approve or Ignore close it
      if (e.key === "Escape") { if (compare) setCompare(false); else if (!plans) setSelected(null); return; }
      if (e.key.toLowerCase() === "m") { setMapView((v) => (v === "airspace" ? "surface" : "airspace")); return; }
      if (plans) {
        if (e.key.toLowerCase() === "i" && !busy) ignore();
        const idx = Number(e.key) - 1;
        if (idx >= 0 && idx < plans.options.length) setPick(plans.options[idx].id);
        // Same guard as the Approve button: never approve a stale plan or one with critical issues from the keyboard
        const sel = plans.options.find((o) => o.id === pick);
        const approvable = !!sel && sel.kpis.critical === 0 && !(snap?.options?.stale && snap.options.id === plans.id);
        if (e.key.toLowerCase() === "a" && !busy && !snap?.planning && approvable) approve();
        if (e.key.toLowerCase() === "c") setCompare((c) => !c);
      } else if (e.key.toLowerCase() === "r" && snap && (snap.options || snap.decision || snap.conflicts.some((c) => c.severity === "critical")) && !busy) {
        openReview();
      } else if (e.key.toLowerCase() === "n") setTab((t) => (t === "ops" ? "notifications" : "ops"));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const geos = useMemo(() => (layout ? runwayGeo(layout) : []), [layout]);
  const traffic = useMemo(() => (snap && layout ? buildTraffic(snap, layout, T, geos) : new Map<string, Ac>()), [snap, layout, T, geos]);
  const standsInUse = useMemo(() => {
    let n = 0;
    for (const ac of traffic.values()) if (ac.where === "GROUND" && (ac.phase === "ON BLOCK" || ac.phase === "ON STAND")) n++;
    return n;
  }, [traffic]);

  if (!layout || !snap) {
    return <div className="boot"><div className="mono">{toast?.err ? toast.text : "Connecting to FalconAero engine…"}</div></div>;
  }
  const proposal = plans ? plans.options.find((o) => o.id === pick) ?? null : null;
  const mapSwitch = (
    <div className="seg" role="tablist" aria-label="Map view">
      <button role="tab" aria-selected={mapView === "airspace"} className={mapView === "airspace" ? "on" : ""} onClick={() => setMapView("airspace")}>Airspace 40 NM</button>
      <button role="tab" aria-selected={mapView === "surface"} className={mapView === "surface" ? "on" : ""} onClick={() => setMapView("surface")}>Surface <span className="kbd">M</span></button>
    </div>
  );

  return (
    <div className={`shell ${tab === "notifications" ? "notif-mode" : ""}`}>
      <TopBar snap={snap} sim={sim} T={T} tab={tab} onTab={setTab} connected={connected} standsInUse={standsInUse} />
      {tab === "ops" ? (
        <>
          <FlightStrips snap={snap} traffic={traffic} selected={selected} onSelect={setSelected} />
          {mapView === "airspace"
            ? <AirspaceMap layout={layout} snap={snap} traffic={traffic} geos={geos} T={T} selected={selected} onSelect={setSelected} proposal={proposal} toolbar={mapSwitch} />
            : <AirfieldMap layout={layout} snap={snap} traffic={traffic} T={T} selected={selected} onSelect={setSelected} proposal={proposal} toolbar={mapSwitch} />}
          <Timeline layout={layout} snap={snap} T={T} selected={selected} onSelect={setSelected} proposal={proposal} view={tlView} onView={setTlView} />
          <AlertsPanel snap={snap} busy={busy} onReview={openReview} onGenerate={regenerate} onUndo={undo} onSelect={setSelected} notify={notify} />
        </>
      ) : (
        <NotificationsView snap={snap} onAction={onAction} />
      )}

      {plans && (
        <PlanReview plans={plans} decision={snap.decision} stale={snap.options?.id === plans.id && (snap.options.stale || !snap.options.valid.includes(pick))} pick={pick} onPick={setPick}
          onApprove={approve} onIgnore={ignore} onCompare={() => setCompare(true)} onRegenerate={regenerate} busy={busy} planning={snap.planning} onSelectFlight={setSelected} />
      )}
      {plans && compare && <CompareView plans={plans} pick={pick} onClose={() => setCompare(false)} />}

      <SimPlayer sim={sim} setSim={setSim} snap={snap} layout={layout} T={T} notify={notify} tucked={!!plans} />
      {toast && <div className={`toast ${toast.err ? "err" : ""}`}>{toast.text}</div>}
    </div>
  );
}
