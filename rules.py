"""Personen-Regeln (rules.json): gesperrte Wochentage, Monatslimit, Notiz.

Format (neu):
  {"persons": [{"personId": 2453, "blockedWeekdays": [0,1,2,3,4,5], "maxPerMonth": 2, "note": "..."}]}
Wochentage: 0 = Montag ... 6 = Sonntag.
Alte Eintraege {"personId": .., "onlySunday": true} werden beim Lesen als Mo–Sa gesperrt uebernommen.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
ALL_DAYS = frozenset(range(7))


@dataclass
class PersonRule:
    person_id: int
    blocked_weekdays: set[int] = field(default_factory=set)
    max_per_month: int | None = None
    note: str = ""

    def is_empty(self) -> bool:
        return not self.blocked_weekdays and not self.max_per_month and not self.note.strip()

    def summary(self) -> str:
        """Kurztext fuer Badges, z. B. "nur So", "nie Mi" oder "nie Mo, Mi"."""
        if not self.blocked_weekdays:
            return ""
        allowed = sorted(ALL_DAYS - self.blocked_weekdays)
        if not allowed:
            return "nie"
        if len(allowed) <= 2:
            return "nur " + ", ".join(WEEKDAYS[d] for d in allowed)
        return "nie " + ", ".join(WEEKDAYS[d] for d in sorted(self.blocked_weekdays))

    def blocks(self, day: date | None) -> bool:
        return bool(day and day.weekday() in self.blocked_weekdays)

    def to_json(self) -> dict:
        out: dict = {"personId": self.person_id}
        if self.blocked_weekdays:
            out["blockedWeekdays"] = sorted(self.blocked_weekdays)
        if self.max_per_month:
            out["maxPerMonth"] = self.max_per_month
        if self.note.strip():
            out["note"] = self.note.strip()
        return out

    def to_api(self) -> dict:
        return {"blocked_weekdays": sorted(self.blocked_weekdays), "max_per_month": self.max_per_month,
                "note": self.note, "summary": self.summary()}


def _parse(raw: dict) -> PersonRule | None:
    r = {str(k).lower(): v for k, v in raw.items()}
    try:
        pid = int(r.get("personid") or 0)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    blocked = {int(d) for d in r.get("blockedweekdays") or [] if str(d).isdigit() and 0 <= int(d) <= 6}
    if r.get("onlysunday"):
        blocked |= set(range(6))
    max_pm = r.get("maxpermonth")
    max_pm = int(max_pm) if isinstance(max_pm, (int, float)) and max_pm > 0 else None
    return PersonRule(pid, blocked, max_pm, str(r.get("note") or ""))


def load_rules(path: Path) -> dict[int, PersonRule]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    persons = (data.get("persons") or data.get("Persons") or []) if isinstance(data, dict) else []
    out: dict[int, PersonRule] = {}
    for raw in persons:
        rule = _parse(raw) if isinstance(raw, dict) else None
        if rule:
            out[rule.person_id] = rule
    return out


def save_rules(path: Path, rules: dict[int, PersonRule]) -> None:
    data = {"persons": [r.to_json() for _, r in sorted(rules.items()) if not r.is_empty()]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomar, damit eine halb geschriebene Datei nie Regeln verliert
