"""Personen-Regeln (rules.json): gesperrte Wochentage, gesperrte Tage, Monatslimit, Notiz.

Format (neu):
  {"persons": [{"personId": 2453, "blockedWeekdays": [0,1,2,3,4,5], "maxPerMonth": 1, "limitHard": true,
                "blockedDates": [{"from": "2026-11-12", "to": "2026-11-14", "note": "Pruefung"}], "note": "..."}]}
Wochentage: 0 = Montag ... 6 = Sonntag. limitHard: Monatslimit sperrt (sonst nur Rueckfrage).
intern: Praktikant/in, nur fuer Praktikanten-Dienste (config intern_services).
Alte Eintraege {"personId": .., "onlySunday": true} werden beim Lesen als Mo–Sa gesperrt uebernommen.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
ALL_DAYS = frozenset(range(7))
KEEP_PAST_DATES_DAYS = 365  # vergangene Sperrtage werden nach einem Jahr beim Speichern entfernt


@dataclass
class BlockedDates:
    start: date
    end: date
    note: str = ""

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def label(self) -> str:
        text = f"{self.start:%d.%m.%Y}" if self.start == self.end else f"{self.start:%d.%m.}–{self.end:%d.%m.%Y}"
        return f"{text} ({self.note})" if self.note else text

    def to_json(self) -> dict:
        out = {"from": self.start.isoformat(), "to": self.end.isoformat()}
        if self.note.strip():
            out["note"] = self.note.strip()
        return out


@dataclass
class PersonRule:
    person_id: int
    blocked_weekdays: set[int] = field(default_factory=set)
    max_per_month: int | None = None
    note: str = ""
    limit_hard: bool = False
    blocked_dates: list[BlockedDates] = field(default_factory=list)
    intern: bool = False  # Praktikant/in: nur fuer Praktikanten-Dienste einplanen

    def is_empty(self) -> bool:
        return (not self.blocked_weekdays and not self.max_per_month and not self.note.strip()
                and not self.blocked_dates and not self.intern)

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

    def block_reason(self, day: date | None) -> str | None:
        """Grund, warum die Person an diesem Tag nicht eingeplant werden darf (Wochentag oder Sperrtag)."""
        if not day:
            return None
        for b in self.blocked_dates:
            if b.contains(day):
                return "gesperrt " + b.label()
        if day.weekday() in self.blocked_weekdays:
            return self.summary()
        return None

    def blocks(self, day: date | None) -> bool:
        return self.block_reason(day) is not None

    def limit_blocks(self, count_elsewhere: int) -> bool:
        """Verbindliches Monatslimit: schon so viele Einsaetze in anderen Events dieses Monats."""
        return bool(self.limit_hard and self.max_per_month and count_elsewhere >= self.max_per_month)

    def limit_text(self) -> str:
        return f"max. {self.max_per_month}×/Monat" if self.max_per_month else ""

    def to_json(self) -> dict:
        out: dict = {"personId": self.person_id}
        if self.blocked_weekdays:
            out["blockedWeekdays"] = sorted(self.blocked_weekdays)
        if self.max_per_month:
            out["maxPerMonth"] = self.max_per_month
            if self.limit_hard:
                out["limitHard"] = True
        if self.blocked_dates:
            out["blockedDates"] = [b.to_json() for b in sorted(self.blocked_dates, key=lambda b: b.start)]
        if self.intern:
            out["intern"] = True
        if self.note.strip():
            out["note"] = self.note.strip()
        return out

    def fits_service(self, label: str | None, intern_labels: set[str]) -> bool:
        """Praktikanten nur fuer Praktikanten-Dienste, alle anderen nur fuer die uebrigen (fuer Vorschlaege)."""
        return self.intern == (label in intern_labels)

    def to_api(self) -> dict:
        return {"blocked_weekdays": sorted(self.blocked_weekdays), "max_per_month": self.max_per_month,
                "limit_hard": bool(self.max_per_month and self.limit_hard), "note": self.note, "intern": self.intern,
                "blocked_dates": [{**b.to_json(), "label": b.label()}
                                  for b in sorted(self.blocked_dates, key=lambda b: b.start)],
                "summary": self.summary()}


def parse_blocked_dates(items, today: date | None = None) -> list[BlockedDates]:
    """Liest [{"from", "to"?, "note"?}]; ungueltige und laengst vergangene Eintraege fallen weg."""
    out: list[BlockedDates] = []
    cutoff = (today or date.today()) - timedelta(days=KEEP_PAST_DATES_DAYS)
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            start = date.fromisoformat(str(item.get("from"))[:10])
            end = date.fromisoformat(str(item.get("to") or item.get("from"))[:10])
        except ValueError:
            continue
        if end < start:
            start, end = end, start
        if end < cutoff:
            continue
        out.append(BlockedDates(start, end, str(item.get("note") or "")[:200]))
    return out


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
    return PersonRule(pid, blocked, max_pm, str(r.get("note") or ""), bool(r.get("limithard")),
                      parse_blocked_dates(r.get("blockeddates")), bool(r.get("intern")))


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
