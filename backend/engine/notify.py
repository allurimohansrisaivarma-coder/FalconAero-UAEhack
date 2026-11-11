"""Notification store: raise / update / auto-resolve, deduplicated by key."""
from __future__ import annotations

import itertools

SEV_RANK = {"info": 0, "warning": 1, "critical": 2}


class Notifications:
    def __init__(self) -> None:
        self.items: dict[int, dict] = {}
        self.by_key: dict[str, int] = {}
        self._ids = itertools.count(1)

    def raise_(self, key: str, severity: str, category: str, title: str, body: str, t: float,
               action: dict | None = None) -> dict:
        nid = self.by_key.get(key)
        n = self.items.get(nid) if nid else None
        if n is not None and n["status"] != "resolved":
            changed = n["title"] != title or n["severity"] != severity
            escalated = SEV_RANK[severity] > SEV_RANK[n["severity"]]
            n.update(severity=severity, category=category, title=title, body=body, updated=t, action=action)
            if changed:
                n["count"] += 1
            if escalated:
                n["status"], n["read"] = "open", False
            return n
        nid = next(self._ids)
        n = {"id": nid, "key": key, "severity": severity, "category": category, "title": title, "body": body,
             "t": t, "updated": t, "status": "open", "read": False, "count": 1, "action": action, "resolved_t": None}
        self.items[nid] = n
        self.by_key[key] = nid
        return n

    def resolve(self, key: str, t: float, note: str = "Condition cleared") -> None:
        nid = self.by_key.get(key)
        n = self.items.get(nid) if nid else None
        if n is not None and n["status"] != "resolved":
            n.update(status="resolved", resolved_t=t, resolution=note)

    def sync(self, prefix: str, active: set[str], t: float) -> None:
        for key, nid in list(self.by_key.items()):
            if key.startswith(prefix) and key not in active:
                self.resolve(key, t)

    def ack(self, nid: int) -> dict | None:
        n = self.items.get(nid)
        if n and n["status"] == "open":
            n["status"] = "ack"
        if n:
            n["read"] = True
        return n

    def ack_all(self) -> None:
        for n in self.items.values():
            n["read"] = True
            if n["status"] == "open":
                n["status"] = "ack"

    def listing(self, limit: int = 200) -> list[dict]:
        order = sorted(self.items.values(),
                       key=lambda n: (n["status"] == "resolved", -SEV_RANK[n["severity"]], -n["updated"]))
        return order[:limit]

    def summary(self) -> dict:
        live = [n for n in self.items.values() if n["status"] != "resolved"]
        return {
            "unread": sum(1 for n in live if not n["read"]),
            "open": len(live),
            "critical": sum(1 for n in live if n["severity"] == "critical"),
            "latest": sorted(live, key=lambda n: -n["updated"])[:5],
        }
