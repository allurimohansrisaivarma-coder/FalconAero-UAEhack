"""METAR and TAF parsing (ICAO Annex 3 subset) into condition objects.

Only the elements that drive airport capacity are decoded: wind, visibility,
RVR, present weather, cloud/vertical visibility, temperature. Unknown groups
are ignored rather than failing, so real-world feeds never break the engine.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

WX_RE = re.compile(r"^(\+|-|VC)?(MI|PR|BC|DR|BL|SH|TS|FZ)?((DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PO|SQ|FC|SS|DS)+)?$")


@dataclass
class Conditions:
    wind_dir: int | None = None      # degrees true, None = variable/calm
    wind_kt: int = 0
    gust_kt: int | None = None
    vis_m: int = 9999
    rvr_m: int | None = None         # lowest reported RVR
    wx: list[str] = field(default_factory=list)
    ceiling_ft: int | None = None    # lowest BKN/OVC/VV
    temp_c: int | None = None
    dew_c: int | None = None
    qnh: int | None = None
    cavok: bool = False
    raw: str = ""

    def has(self, *codes: str) -> bool:
        return any(any(c in w for c in codes) for w in self.wx)

    def to_dict(self) -> dict:
        return {
            "wind_dir": self.wind_dir, "wind_kt": self.wind_kt, "gust_kt": self.gust_kt,
            "vis_m": self.vis_m, "rvr_m": self.rvr_m, "wx": self.wx, "ceiling_ft": self.ceiling_ft,
            "temp_c": self.temp_c, "dew_c": self.dew_c, "qnh": self.qnh, "cavok": self.cavok, "raw": self.raw,
        }


def _parse_groups(tokens: list[str], c: Conditions) -> None:
    for tok in tokens:
        if tok in ("NOSIG", "AUTO", "COR", "AMD", "RMK", "NSW"):
            if tok == "NSW":
                c.wx = []
            continue
        m = re.match(r"^(\d{3}|VRB)(\d{2,3})(G(\d{2,3}))?(KT|MPS)$", tok)
        if m:
            f = 1.94 if m.group(5) == "MPS" else 1
            c.wind_dir = None if m.group(1) == "VRB" else int(m.group(1))
            c.wind_kt = round(int(m.group(2)) * f)
            c.gust_kt = round(int(m.group(4)) * f) if m.group(4) else None
            continue
        if re.match(r"^\d{3}V\d{3}$", tok):
            continue
        if tok == "CAVOK":
            c.cavok, c.vis_m, c.ceiling_ft = True, 9999, None
            continue
        if re.match(r"^\d{4}$", tok):
            c.vis_m = int(tok)
            continue
        m = re.match(r"^(\d{4})(N|NE|E|SE|S|SW|W|NW|NDV)$", tok)
        if m:
            c.vis_m = int(m.group(1))
            continue
        m = re.match(r"^(\d+)SM$", tok)
        if m:
            c.vis_m = min(9999, int(int(m.group(1)) * 1609))
            continue
        m = re.match(r"^R\d{2}[LCR]?/([PM])?(\d{4})(V(\d{4}))?(FT)?[UDN]?$", tok)
        if m:
            val = int(m.group(2))
            if m.group(5):
                val = round(val * 0.3048)
            c.rvr_m = val if c.rvr_m is None else min(c.rvr_m, val)
            continue
        m = re.match(r"^(FEW|SCT|BKN|OVC)(\d{3})(CB|TCU)?$", tok)
        if m:
            if m.group(1) in ("BKN", "OVC"):
                h = int(m.group(2)) * 100
                c.ceiling_ft = h if c.ceiling_ft is None else min(c.ceiling_ft, h)
            if m.group(3) == "CB" and "CB" not in c.wx:
                c.wx.append("CB")
            continue
        m = re.match(r"^VV(\d{3}|///)$", tok)
        if m:
            c.ceiling_ft = 0 if m.group(1) == "///" else int(m.group(1)) * 100
            continue
        if tok in ("NSC", "SKC", "CLR", "NCD"):
            continue
        m = re.match(r"^(M?\d{2})/(M?\d{2})?$", tok)
        if m:
            c.temp_c = int(m.group(1).replace("M", "-"))
            if m.group(2):
                c.dew_c = int(m.group(2).replace("M", "-"))
            continue
        m = re.match(r"^Q(\d{4})$", tok)
        if m:
            c.qnh = int(m.group(1))
            continue
        if WX_RE.match(tok) and tok not in ("", "+", "-", "VC"):
            c.wx.append(tok)
            continue


def parse_metar(raw: str) -> tuple[Conditions, dict]:
    """Return (conditions, meta) where meta has station and day/hour/minute of issue."""
    text = " ".join(raw.replace("=", " ").split())
    # Cut trend/remarks: the observation part only
    for cut in (" BECMG ", " TEMPO ", " RMK "):
        if cut in f" {text} ":
            text = text.split(cut.strip())[0].strip()
    tokens = text.split()
    meta: dict = {}
    if tokens and tokens[0] in ("METAR", "SPECI"):
        meta["kind"] = tokens.pop(0)
    if tokens and re.match(r"^[A-Z]{4}$", tokens[0]):
        meta["station"] = tokens.pop(0)
    if tokens and re.match(r"^\d{6}Z$", tokens[0]):
        z = tokens.pop(0)
        meta.update(day=int(z[:2]), hour=int(z[2:4]), minute=int(z[4:6]))
    c = Conditions(raw=raw.strip())
    _parse_groups(tokens, c)
    return c, meta


@dataclass
class TafPeriod:
    kind: str          # BASE | FM | BECMG | TEMPO | PROB30 | PROB40 | PROB30 TEMPO ...
    start: int         # epoch minutes
    end: int
    cond: Conditions
    fields: set[str]   # which fields this group sets


def _resolve(day: int, hour: int, minute: int, ref: datetime) -> int:
    """Turn a (day, hour, minute) group into epoch minutes near the reference time."""
    y, mo = ref.year, ref.month
    best = None
    for dm in (-1, 0, 1):
        mm, yy = mo + dm, y
        if mm == 0:
            mm, yy = 12, y - 1
        if mm == 13:
            mm, yy = 1, y + 1
        last = calendar.monthrange(yy, mm)[1]
        if day > last:
            continue
        h, extra = (0, 1) if hour == 24 else (hour, 0)
        dt = datetime(yy, mm, day, h, minute, tzinfo=timezone.utc)
        t = int(dt.timestamp() // 60) + extra * 1440
        if best is None or abs(t - ref.timestamp() / 60) < abs(best - ref.timestamp() / 60):
            best = t
    return best if best is not None else int(ref.timestamp() // 60)


def _fields_set(tokens: list[str]) -> set[str]:
    probe = Conditions()
    probe.vis_m = -1
    probe.wind_kt = -1
    _parse_groups(tokens, probe)
    s = set()
    if probe.vis_m != -1 or probe.cavok:
        s.add("vis")
    if probe.wind_kt != -1:
        s.add("wind")
    if probe.wx or "NSW" in tokens:
        s.add("wx")
    if probe.ceiling_ft is not None or probe.cavok or any(t in ("NSC", "SKC") for t in tokens):
        s.add("cloud")
    return s


def parse_taf(raw: str, ref_minutes: int) -> list[TafPeriod]:
    ref = datetime.fromtimestamp(ref_minutes * 60, tz=timezone.utc)
    text = " ".join(raw.replace("=", " ").split())
    tokens = text.split()
    while tokens and tokens[0] in ("TAF", "AMD", "COR"):
        tokens.pop(0)
    if tokens and re.match(r"^[A-Z]{4}$", tokens[0]):
        tokens.pop(0)
    if tokens and re.match(r"^\d{6}Z$", tokens[0]):
        tokens.pop(0)
    m = re.match(r"^(\d{2})(\d{2})/(\d{2})(\d{2})$", tokens[0]) if tokens else None
    if not m:
        return []
    tokens.pop(0)
    v_start = _resolve(int(m.group(1)), int(m.group(2)), 0, ref)
    v_end = _resolve(int(m.group(3)), int(m.group(4)), 0, ref)

    # Split into groups
    groups: list[tuple[str, list[str]]] = [("BASE", [])]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("BECMG", "TEMPO") or re.match(r"^FM\d{6}$", tok) or re.match(r"^PROB\d{2}$", tok):
            kind = tok
            if re.match(r"^PROB\d{2}$", tok) and i + 1 < len(tokens) and tokens[i + 1] == "TEMPO":
                kind = f"{tok} TEMPO"
                i += 1
            groups.append((kind, []))
        else:
            groups[-1][1].append(tok)
        i += 1

    periods: list[TafPeriod] = []
    for kind, toks in groups:
        start, end = v_start, v_end
        if kind.startswith("FM"):
            d, h, mi = int(kind[2:4]), int(kind[4:6]), int(kind[6:8])
            start = _resolve(d, h, mi, ref)
            kind = "FM"
        elif toks and re.match(r"^\d{4}/\d{4}$", toks[0]):
            a, b = toks.pop(0).split("/")
            start = _resolve(int(a[:2]), int(a[2:]), 0, ref)
            end = _resolve(int(b[:2]), int(b[2:]), 0, ref)
        c = Conditions(raw=" ".join(toks))
        _parse_groups(toks, c)
        periods.append(TafPeriod(kind, start, end, c, _fields_set(toks)))
    # FM groups end where the next FM starts
    fms = [p for p in periods if p.kind in ("BASE", "FM")]
    for a, b in zip(fms, fms[1:]):
        a.end = b.start
    return periods


def _overlay(base: Conditions, p: TafPeriod) -> Conditions:
    c = replace(base, wx=list(base.wx))
    if "wind" in p.fields:
        c.wind_dir, c.wind_kt, c.gust_kt = p.cond.wind_dir, p.cond.wind_kt, p.cond.gust_kt
    if "vis" in p.fields:
        c.vis_m, c.cavok = p.cond.vis_m, p.cond.cavok
        if p.cond.cavok:
            c.ceiling_ft, c.wx = None, []
    if "wx" in p.fields:
        c.wx = list(p.cond.wx)
    if "cloud" in p.fields:
        c.ceiling_ft = p.cond.ceiling_ft
    return c


def taf_conditions_at(periods: list[TafPeriod], t: int) -> tuple[Conditions | None, Conditions | None, str | None]:
    """(prevailing, worst-case, label) forecast for time t; None outside validity."""
    if not periods:
        return None, None, None
    base_p = next((p for p in periods if p.kind == "BASE"), None)
    if base_p is None or not (base_p.start <= t < max(p.end for p in periods)):
        return None, None, None
    prev = replace(base_p.cond, wx=list(base_p.cond.wx))
    for p in periods:
        if p.kind == "FM" and p.start <= t:
            prev = replace(p.cond, wx=list(p.cond.wx))
        elif p.kind == "BECMG" and p.end <= t:
            prev = _overlay(prev, p)
    worst = prev
    label = None
    for p in periods:
        if p.kind in ("BECMG",) and p.start <= t < p.end:
            worst = _overlay(worst, p)
            label = "BECMG"
        if (p.kind == "TEMPO" or p.kind.startswith("PROB")) and p.start <= t < p.end:
            worst = _overlay(worst, p)
            label = p.kind
    return prev, worst, label
