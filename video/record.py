"""Record the FalconAero demo: two situations (radiation fog, full emergency), each approved on screen.

Starts a private engine on PORT (serving the built frontend), drives it with Playwright, paces every
scene to its Piper narration (run voiceover.py first), then muxes video + voice with ffmpeg.
Output: video/build/FalconAero-demo.mp4
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
ROOT = HERE.parent
BUILD = HERE / "build"
VO = BUILD / "vo"
PORT = 8010
URL = f"http://127.0.0.1:{PORT}"
W, H = 1920, 1080
GAP = 1.0                      # breathing room after each narration line (s)

DUR = json.loads((VO / "durations.json").read_text())

FX = """
(() => {
  if (window.__fx) return;
  const css = document.createElement('style');
  css.textContent = `
    #fx-card{position:fixed;inset:0;z-index:99998;display:flex;flex-direction:column;align-items:center;justify-content:center;
      background:radial-gradient(ellipse at center,#121a24 0%,#07090c 75%);opacity:0;transition:opacity .8s ease;pointer-events:none;
      font-family:Inter,system-ui,sans-serif;color:#e8edf2}
    #fx-card.on{opacity:1}
    #fx-card .k{font:600 15px/1 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.28em;color:#4cc2ff;margin-bottom:18px;text-transform:uppercase}
    #fx-card .t{font-weight:700;font-size:64px;letter-spacing:-.02em}
    #fx-card .s{margin-top:14px;font-size:22px;color:#9aa5b2}
    #fx-cap{position:fixed;left:50%;bottom:44px;transform:translate(-50%,16px);z-index:99999;max-width:1100px;padding:14px 22px;
      background:rgba(9,12,17,.92);border:1px solid #263040;border-left:4px solid #4cc2ff;border-radius:6px;opacity:0;
      transition:opacity .5s ease,transform .5s ease;pointer-events:none;font:500 22px/1.35 Inter,system-ui,sans-serif;color:#e8edf2}
    #fx-cap.on{opacity:1;transform:translate(-50%,0)}
    #fx-cap b{color:#4cc2ff;font-weight:600}
    #fx-hl{position:fixed;z-index:99990;border:2.5px solid #4cc2ff;border-radius:8px;box-shadow:0 0 0 9999px rgba(3,5,8,.42),0 0 24px rgba(76,194,255,.45);
      opacity:0;transition:all .55s cubic-bezier(.4,0,.2,1);pointer-events:none}
    #fx-hl.on{opacity:1}`;
  document.head.appendChild(css);
  const mk = (id) => { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); return d; };
  const card = mk('fx-card'), cap = mk('fx-cap'), hl = mk('fx-hl');
  window.__fx = {
    card(k, t, s) { if (t === null) { card.classList.remove('on'); return; }
      card.innerHTML = `<div class="k">${k}</div><div class="t">${t}</div>${s ? `<div class="s">${s}</div>` : ''}`; card.classList.add('on'); },
    cap(html) { if (!html) { cap.classList.remove('on'); return; } cap.innerHTML = html; cap.classList.add('on'); },
    hl(sel, pad = 6) {
      const el = sel && document.querySelector(sel);
      if (!el) { hl.classList.remove('on'); return false; }
      const r = el.getBoundingClientRect();
      Object.assign(hl.style, { left: r.left - pad + 'px', top: r.top - pad + 'px', width: r.width + 2 * pad + 'px', height: r.height + 2 * pad + 'px' });
      hl.classList.add('on'); return true;
    },
  };
})();
"""


def start_engine() -> subprocess.Popen:
    py = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.Popen([str(py), "-m", "uvicorn", "api.main:app", "--port", str(PORT), "--app-dir", str(ROOT / "backend")],
                            cwd=ROOT / "backend", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            urllib.request.urlopen(URL + "/api/layout", timeout=1)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    sys.exit("engine did not start")


class Director:
    def __init__(self, page):
        self.page = page
        self.t0 = time.monotonic()
        self.marks: dict[str, float] = {}
        self._start = 0.0

    def now(self) -> float:
        return time.monotonic() - self.t0

    def api(self, method: str, path: str, body: dict | None = None):
        r = self.page.request.fetch(URL + path, method=method, data=body if body is not None else None,
                                    headers={"Content-Type": "application/json"})
        return r.json() if r.ok else None

    def fx(self, expr: str):
        self.page.evaluate(FX)                       # survives any re-render; installs once
        return self.page.evaluate(expr)

    def at(self, sec: float):
        """Wait until `sec` seconds into the current scene."""
        left = self._start + sec - self.now()
        if left > 0:
            self.page.wait_for_timeout(left * 1000)

    def scene(self, sid: str, body):
        self._start = self.now()
        self.marks[sid] = self._start
        body()
        self.at(DUR[sid] + GAP)
        print(f"{sid:14s} {self._start:7.1f}s  narration {DUR[sid]:5.1f}s  scene {self.now() - self._start:5.1f}s", flush=True)

    # helpers ---------------------------------------------------------------
    def cap(self, html):
        self.fx(f"__fx.cap({json.dumps(html)})")

    def hl(self, sel):
        return self.fx(f"__fx.hl({json.dumps(sel)})")

    def card(self, k, t=None, s=None):
        self.fx(f"__fx.card({json.dumps(k)}, {json.dumps(t)}, {json.dumps(s)})")

    def wait_modal(self, timeout=90):
        """Ask for recovery options from the alerts panel; the plan opens as a blocking pop-up when ready."""
        btn = self.page.get_by_role("button", name=re.compile(r"(Generate|Review) recovery plan|recompute plan"))
        if not self.page.locator(".decision").count() and btn.count():
            self.hl(".cta-box")
            self.page.wait_for_timeout(1200)
            btn.first.click()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.page.locator(".decision").count():
                self.page.wait_for_timeout(600)
                return True
            self.page.wait_for_timeout(400)
        return False

    def approve(self) -> str:
        """Approve the recommended option (or the first one without critical issues). If the engine
        withdraws the proposal in the meantime, ask for it again and approve without the pause."""
        for attempt in range(3):
            if not self.page.locator(".decision").count() and not self.wait_modal():
                continue
            btn = self.page.get_by_role("button", name=re.compile(r"^Approve"))
            try:
                if not btn.is_enabled(timeout=3000):
                    for opt in self.page.locator(".decision .opt").all():
                        opt.click(timeout=3000)
                        self.page.wait_for_timeout(400)
                        if btn.is_enabled(timeout=3000):
                            break
                if btn.is_enabled(timeout=3000):
                    if attempt == 0:
                        self.hl(".decision-f .btn.primary")
                        self.page.wait_for_timeout(1300)
                    btn.click(timeout=3000)
                    return "approved"
                self.page.get_by_role("button", name=re.compile(r"^Ignore warning")).click(timeout=3000)
                return "ignored"
            except Exception as ex:      # the pop-up closed under us
                print(f"  retry approve ({type(ex).__name__})", flush=True)
        raise RuntimeError("could not approve a recovery plan")


def run(page, d: Director) -> None:
    def intro():
        d.card("AOC recovery · Dubai International", "FalconAero", "Disruption planning with the controller in command")
        d.at(3.2)
        d.card("", None)
        d.at(4.0)
        d.hl(".panel.strips"); d.cap("<b>Flight strips</b> · arrivals, on ground, departures")
        d.at(8.0)
        d.hl(".mapwrap"); d.cap("<b>Live traffic</b> · 40 NM radar and airfield surface, in sync with the strips")
        d.at(12.5)
        d.hl(".panel.side"); d.cap("<b>Alerts & decisions</b> · what needs the duty manager")

    def normal():
        d.hl(None); d.cap(None)
        page.keyboard.press("m")                     # surface view
        d.at(1.5)
        d.hl(".automation"); d.cap("<b>Routine upkeep</b> runs on its own, inside limits the controller sets")
        d.at(7.5)
        d.hl(None)

    def fog_onset():
        d.cap(None)
        d.card("Situation 1", "Radiation fog at dawn", "Visibility 150 m · CAT III low-visibility procedures")
        page.get_by_role("button", name=re.compile(r"^forecast", re.I)).first.click()
        d.api("POST", "/sim/scenario", {"scenario": "fog", "delay": 0})
        d.api("POST", "/sim/jump", {"minutes": 32})
        d.at(3.4)
        d.card("", None)
        d.at(4.2)
        d.hl(".map-status"); d.cap("<b>LVP in force</b> · wider arrival spacing, slower taxi")
        d.at(9.0)
        d.hl(".tl-body"); d.cap("<b>Forecast</b> · holding and departure delay building")

    def fog_plan():
        d.hl(None); d.cap(None)
        d.wait_modal()
        d.hl(".decision .impact"); d.cap("<b>Impact</b> · aircraft, runways and stands touched by the fog")
        d.at(7.5)
        d.hl(".decision .options"); d.cap("<b>Options</b> · delay, changes, flights without a slot, critical issues left")

    def fog_approve():
        d.cap("<b>Approve</b> · the decision goes to the tower and stand planning")
        d.at(2.5)
        print("  fog:", d.approve(), flush=True)
        d.at(4.5)
        d.hl(None); d.cap(None)
        page.get_by_role("button", name=re.compile(r"^stands", re.I)).first.click()
        page.keyboard.press("m")                     # back to the radar
        d.at(7.0)
        d.hl(".mapwrap"); d.cap("<b>New sequence live</b> · radar, surface and strips update together")

    def emerg_onset():
        d.hl(None); d.cap(None)
        d.card("Situation 2", "Full emergency", "Inbound B737 MAX reporting an engine fire")
        d.api("POST", "/sim/scenario", {"scenario": "full_emergency", "delay": 0})
        d.api("POST", "/sim/jump", {"minutes": 2})
        d.at(3.4)
        d.card("", None)
        d.at(4.2)
        page.locator(".strip-tab", has_text=re.compile("Arrivals", re.I)).first.click()
        d.at(4.8)
        if not d.hl(".strip.emerg"):
            d.hl(".panel.strips")
        d.cap("<b>Emergency</b> · strip turns red, first safe landing slot is protected")

    def emerg_plan():
        d.hl(None); d.cap(None)
        d.wait_modal()
        d.hl(".decision .impact"); d.cap("<b>Blocking pop-up</b> · closes only when a decision is made")
        d.at(7.5)
        d.hl(".decision-f"); d.cap("<b>Approve</b> or <b>Ignore warning</b> · either choice is logged")

    def emerg_approve():
        print("  emergency:", d.approve(), flush=True)
        d.at(2.0)
        d.hl(".panel.strips"); d.cap("<b>Emergency first</b> · every other change explained flight by flight")
        d.at(6.0)
        d.hl(None)

    def notifications():
        d.cap(None)
        page.keyboard.press("n")
        d.at(1.0)
        d.hl(".notif-list"); d.cap("<b>Notifications</b> · flights, runways and stands affected, one action each")

    def outro():
        d.hl(None); d.cap(None)
        d.card("Decision support for airport operations", "FalconAero", "The controller stays in command")

    for sid, fn in [("intro", intro), ("normal", normal), ("fog_onset", fog_onset), ("fog_plan", fog_plan),
                    ("fog_approve", fog_approve), ("emerg_onset", emerg_onset), ("emerg_plan", emerg_plan),
                    ("emerg_approve", emerg_approve), ("notifications", notifications), ("outro", outro)]:
        d.scene(sid, fn)
    d.marks["end"] = d.now()


def mux(raw: Path, marks: dict[str, float]) -> Path:
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    start = marks["intro"] - 0.2
    length = marks["end"] - start + 0.3
    ids = [k for k in DUR]
    cmd = [ff, "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(raw)]
    for sid in ids:
        cmd += ["-i", str(VO / f"{sid}.wav")]
    parts = [f"[{i + 1}:a]adelay={int((marks[sid] - start) * 1000) + 250}|{int((marks[sid] - start) * 1000) + 250}[a{i}]"
             for i, sid in enumerate(ids)]
    mix = "".join(f"[a{i}]" for i in range(len(ids))) + f"amix=inputs={len(ids)}:normalize=0,apad[aud]"
    vf = f"[0:v]fps=30,format=yuv420p,fade=t=in:st=0:d=0.8,fade=t=out:st={length - 1.0:.2f}:d=1.0[vid]"
    out = BUILD / "FalconAero-demo.mp4"
    cmd += ["-filter_complex", ";".join(parts + [mix, vf]), "-map", "[vid]", "-map", "[aud]", "-t", f"{length:.2f}",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    engine = start_engine()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(BUILD / "raw"),
                                      record_video_size={"width": W, "height": H})
            page = ctx.new_page()
            d = Director(page)
            # Pre-roll (trimmed away): a fresh day with traffic already flowing
            d.api("POST", "/sim/restart")
            # Options are requested on screen, so each pop-up appears when the narration reaches it
            d.api("POST", "/api/settings", {"auto_recovery": False})
            d.api("POST", "/sim/jump", {"minutes": 40})
            d.api("POST", "/sim/speed", {"speed": 10})
            d.api("POST", "/sim/play")
            page.goto(URL)
            page.wait_for_selector(".panel.strips", timeout=30000)
            page.wait_for_timeout(4000)
            try:
                run(page, d)
            except Exception:
                page.screenshot(path=str(BUILD / "failed.png"))
                raise
            raw = Path(page.video.path())
            ctx.close()
            browser.close()
        (BUILD / "marks.json").write_text(json.dumps(d.marks, indent=2))
        out = mux(raw, d.marks)
        print("wrote", out, f"({d.marks['end'] - d.marks['intro']:.0f}s)")
    finally:
        engine.kill()


if __name__ == "__main__":
    main()
