"""Retake the README screenshots and the pitch-deck crops from the current UI.

Runs the same private engine as record.py (no captions or highlights). Writes:
  docs/screenshots/01..09-*.png                 full 1920x1080 frames for the README
  video/build/deck/image-<slide>-<n>.png        crops at the deck's existing pixel sizes, so each
                                                drops into its slide frame without distortion
"""
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

from record import ROOT, URL, W, H, Director, start_engine

DOCS = ROOT / "docs" / "screenshots"
DECK = Path(__file__).parent / "build" / "deck"


def main() -> None:
    DECK.mkdir(parents=True, exist_ok=True)
    engine = start_engine()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": W, "height": H})
            d = Director(page)

            def settle(ms=900):
                d.api("POST", "/sim/pause")
                page.wait_for_timeout(ms)

            def full(name):
                page.screenshot(path=str(DOCS / name))
                print("saved", name, flush=True)

            def crop(name, sel, w, h, centre=False):
                """A w x h crop anchored at the element's top-left (or centred on it)."""
                b = page.locator(sel).first.bounding_box()
                x, y = (b["x"] + (b["width"] - w) / 2, b["y"] + (b["height"] - h) / 2) if centre else (b["x"], b["y"])
                x, y = max(0, min(W - w, x)), max(0, min(H - h, y))
                page.screenshot(path=str(DECK / name), clip={"x": x, "y": y, "width": w, "height": h})
                print("saved deck", name, f"from {sel} {b['width']:.0f}x{b['height']:.0f}", flush=True)

            # Normal operations
            d.api("POST", "/sim/restart")
            d.api("POST", "/api/settings", {"auto_recovery": False})   # open each plan by hand, like a controller
            # Same moments as the README and deck: 04:00Z with the clock paused, then the first fog report
            # (3000 m BR) one simulated minute later. Nothing runs in between, so the measurement repeats.
            d.api("POST", "/sim/pause")
            page.goto(URL)
            page.wait_for_selector(".panel.strips", timeout=30000)
            page.get_by_title("Minimise").first.click()                  # tuck the sim player out of the way
            page.wait_for_timeout(5000)
            settle()
            full("01-operations-airspace.png")
            crop("image-4-1.png", "body", W, H)
            page.keyboard.press("m")
            page.wait_for_timeout(900)
            full("02-surface-view.png")
            crop("image-4-2.png", ".mapwrap", 1230, 790)
            page.keyboard.press("m")

            # Fog: new operating conditions
            d.api("POST", "/sim/scenario", {"scenario": "fog", "delay": 0})
            d.api("POST", "/sim/jump", {"minutes": 1})
            page.wait_for_timeout(3000)
            settle()
            full("03-fog-alerts.png")
            crop("image-6-1.png", ".panel.side", 360, 600)
            page.get_by_role("button", name=re.compile(r"^forecast", re.I)).first.click()
            page.wait_for_timeout(900)
            full("04-forecast.png")
            crop("image-4-3.png", ".tl-body", 1215, 235)

            # Notifications while the decision is pending: issues, weather and forecast, each with its impact
            page.keyboard.press("n")
            page.wait_for_selector(".notif-list", timeout=10000)
            page.wait_for_timeout(1500)
            full("08-notifications.png")
            crop("image-4-4.png", ".notif-main", 1690, 365)
            page.keyboard.press("n")
            page.wait_for_timeout(800)

            # Recovery plan: the blocking decision pop-up
            page.get_by_role("button", name=re.compile(r"(Generate|Review) recovery plan|recompute plan")).first.click()
            page.wait_for_selector(".decision .opt", timeout=90000)
            page.locator(".decision .opt", has_text="Balanced").first.click()   # the plan the README and slide 7 measure
            page.wait_for_timeout(1200)
            full("05-recovery-options.png")
            plans = d.api("GET", "/api/plans/latest")
            bal = next(o for o in plans["options"] if o["label"] == "Balanced")
            keys = ("critical", "unserved", "avg_delay", "total_delay", "max_delay", "delayed_15", "holding")
            print("MEASURED current:", {k: plans["current"]["kpis"].get(k) for k in keys})
            print("MEASURED rule-based:", {k: plans["baseline"]["kpis"].get(k) for k in keys})
            print("MEASURED balanced:", {k: bal["kpis"].get(k) for k in keys}, "ms", bal["solver"]["ms"],
                  "all ms", [o["solver"]["ms"] for o in plans["options"]], "risk", bal["risk"], "recommended", plans["recommended"])
            # Slide 6's middle frame is tall and narrow (620x1020): show the pop-up as it lays out on a
            # narrow window, stacked in one column, instead of squeezing the wide desktop version
            narrow = browser.new_page(viewport={"width": 600, "height": 1500})
            narrow.goto(URL)
            narrow.wait_for_selector(".decision .opt", timeout=30000)   # a pending decision opens it by itself
            narrow.locator(".decision .opt", has_text="Balanced").first.click()
            narrow.wait_for_timeout(1200)
            b = narrow.locator(".decision").bounding_box()
            w = round(b["width"]); h = round(w * 1020 / 620)
            narrow.screenshot(path=str(DECK / "image-6-2.png"), clip={"x": b["x"], "y": b["y"], "width": w, "height": min(h, round(b["height"]))})
            print(f"saved deck image-6-2.png {w}x{min(h, round(b['height']))} (pop-up {b['width']:.0f}x{b['height']:.0f})", flush=True)
            narrow.close()

            page.get_by_role("button", name=re.compile(r"Compare")).first.click()
            page.wait_for_selector(".modal", timeout=10000)
            page.wait_for_timeout(900)
            full("06-full-comparison.png")
            crop("image-7-1.png", ".modal", 980, 850, centre=True)
            page.keyboard.press("Escape")
            page.wait_for_timeout(600)

            approve = page.get_by_role("button", name=re.compile(r"^Approve"))
            if not approve.is_enabled():
                for opt in page.locator(".decision .opt").all():
                    opt.click()
                    page.wait_for_timeout(400)
                    if approve.is_enabled():
                        break
            approve.click()
            page.wait_for_timeout(2500)
            page.get_by_role("button", name=re.compile(r"^stands", re.I)).first.click()
            settle(1200)
            full("07-plan-approved.png")
            crop("image-6-3.png", ".panel.side", 360, 760)

            page.locator(".simfab .fab-t").click()                      # expand the player again
            page.wait_for_timeout(500)
            page.get_by_title("Open Scenario Studio").first.click()
            page.wait_for_selector(".studio", timeout=10000)
            page.get_by_role("button", name=re.compile(r"Scenario library")).first.click()
            page.wait_for_timeout(1000)
            full("09-scenario-studio.png")
            crop("image-8-9.png", ".studio", 1240, 820, centre=True)
            browser.close()
    finally:
        engine.kill()


if __name__ == "__main__":
    main()
