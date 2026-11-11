"""ICAO aircraft type designators → aerodrome reference code letter and defaults."""
from __future__ import annotations

CODE_RANK = {"C": 1, "D": 2, "E": 3, "F": 4}

# Code letter by wingspan (ICAO Annex 14): C < 36 m, D < 52 m, E < 65 m, F < 80 m
TYPE_CODE: dict[str, str] = {
    # Code F
    "A388": "F", "B748": "F", "B744": "E",
    # Code E (wide-body)
    "B77W": "E", "B77L": "E", "B772": "E", "B773": "E", "B778": "E", "B779": "E",
    "B788": "E", "B789": "E", "B78X": "E",
    "A332": "E", "A333": "E", "A338": "E", "A339": "E", "A359": "E", "A35K": "E", "A346": "E",
    # Code D
    "B763": "D", "B764": "D", "B752": "D", "B753": "D", "MD11": "D",
    # Code C (narrow-body / regional)
    "A318": "C", "A319": "C", "A320": "C", "A321": "C", "A19N": "C", "A20N": "C", "A21N": "C",
    "B737": "C", "B738": "C", "B739": "C", "B37M": "C", "B38M": "C", "B39M": "C",
    "E190": "C", "E195": "C", "E290": "C", "E295": "C", "BCS1": "C", "BCS3": "C",
    "AT76": "C", "AT75": "C", "DH8D": "C", "CRJ9": "C",
}

DEFAULT_TURNAROUND = {"C": 50, "D": 75, "E": 95, "F": 120}


def code_for(type_designator: str) -> tuple[str, bool]:
    """Return (code letter, known). Unknown types default to the conservative 'E'."""
    t = (type_designator or "").upper().strip()
    if t in TYPE_CODE:
        return TYPE_CODE[t], True
    if t.startswith("A38"):
        return "F", False
    if t.startswith(("B77", "B78", "A33", "A34", "A35")):
        return "E", False
    if t.startswith(("A31", "A32", "A2", "B73", "B3", "E1", "E2", "AT", "CRJ", "DH")):
        return "C", False
    return "E", False


def fits(code: str, stand_max: str) -> bool:
    return CODE_RANK[code] <= CODE_RANK[stand_max]
