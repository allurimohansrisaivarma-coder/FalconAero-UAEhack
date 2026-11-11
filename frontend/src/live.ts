import { useEffect, useRef, useState } from "react";
import type { SimStatus, Snapshot } from "./types";

/** Subscribes to the engine's live stream (SSE). Returns the latest snapshot, sim status
 *  and an interpolated clock so aircraft move smoothly between server updates. */
export function useLive() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [sim, setSim] = useState<SimStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const [T, setT] = useState(0);
  const base = useRef({ now: 0, rate: 0, at: performance.now() });

  useEffect(() => {
    let es: EventSource | null = null;
    let retry = 0;
    let closed = false;
    const open = () => {
      es = new EventSource("/api/stream");
      es.onopen = () => { setConnected(true); retry = 0; };
      es.onerror = () => {
        setConnected(false);
        es?.close();
        if (!closed) window.setTimeout(open, Math.min(5000, 500 * 2 ** retry++));
      };
      es.addEventListener("state", (e) => {
        const s = JSON.parse((e as MessageEvent).data) as Snapshot;
        base.current = { now: s.now, rate: s.clock_rate, at: performance.now() };
        setSnap(s);
      });
      es.addEventListener("clock", (e) => {
        const c = JSON.parse((e as MessageEvent).data) as { now: number; rate: number };
        base.current = { now: c.now, rate: c.rate, at: performance.now() };
      });
      es.addEventListener("sim", (e) => setSim(JSON.parse((e as MessageEvent).data) as SimStatus));
    };
    open();
    return () => { closed = true; es?.close(); };
  }, []);

  // Interpolated clock (≈25 fps while time is running)
  useEffect(() => {
    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      const b = base.current;
      if (now - last > 40) {
        last = now;
        const dt = Math.min(3, (now - b.at) / 1000);
        setT(b.now + b.rate * dt);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  return { snap, sim, setSim, connected, T: T || snap?.now || 0 };
}
