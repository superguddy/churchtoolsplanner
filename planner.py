"""Planungslogik ohne Netzwerk und ohne UI (aus MainWindow.xaml.cs uebernommen).

Alles hier arbeitet auf den rohen ChurchTools-JSON-dicts, damit es mit Testdaten
geprueft werden kann.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def normalize(s: str | None) -> str:
    """Nur Buchstaben/Ziffern, klein (wie Normalize() im C#-Original)."""
    return "".join(ch for ch in (s or "") if ch.isalnum()).lower()


@dataclass
class PlannerSettings:
    info_services: list[str]
    plannable_services: list[str]
    service_aliases: dict[str, str]
    group_services: dict[str, list[str]]
    timezone: str = "Europe/Zurich"
    _labels: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        for label in [*self.info_services, *self.plannable_services]:
            self._labels[normalize(label)] = label
        for alias, target in self.service_aliases.items():
            self._labels.setdefault(normalize(alias), target)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def canonical_label(self, service_name: str | None) -> str | None:
        """Ordnet einen CT-Dienstnamen einem bekannten Label zu (laengster passender Praefix).

        Beispiele: "Tontechnik" -> "Ton", "Praktikant II Podcast" -> "Praktikant II Podcast",
        "Lobpreis" -> "Lobpreisleitung".
        """
        k = normalize(service_name)
        if not k:
            return None
        best = None
        for key, label in self._labels.items():
            if key and k.startswith(key) and (best is None or len(key) > len(best[0])):
                best = (key, label)
        return best[1] if best else None

    def relevant_labels_for_group(self, group_name: str | None) -> set[str]:
        g = normalize(group_name)
        for needle, labels in self.group_services.items():
            if normalize(needle) and normalize(needle) in g:
                return set(labels)
        return set(self.plannable_services)


# ---------------------------------------------------------------------- Datum/Zeit
def parse_ct_datetime(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def event_local_start(ev: dict, tz: ZoneInfo) -> datetime | None:
    return parse_ct_datetime(ev.get("startDate"), tz)


def event_display(ev: dict, tz: ZoneInfo) -> str:
    start = event_local_start(ev, tz)
    name = ev.get("name") or ev.get("title") or ""
    if not start:
        return name
    return f"{WEEKDAYS_DE[start.weekday()]} {start:%d.%m.%Y} {start:%H:%M} – {name}"


# ---------------------------------------------------------------------- Personen
def person_id_from_ref(person: dict | None) -> int | None:
    """Liest die Personen-ID aus einem CT-DomainObject (domainIdentifier/apiUrl)."""
    if not isinstance(person, dict):
        return None
    di = str(person.get("domainIdentifier") or "")
    m = re.search(r"\d+", di)
    if m:
        return int(m.group())
    api_url = str(person.get("apiUrl") or "").rstrip("/")
    m = re.search(r"(\d+)$", api_url)
    if m:
        return int(m.group(1))
    return None


def person_name(person: dict | None) -> str:
    if not isinstance(person, dict):
        return ""
    title = (person.get("title") or "").strip()
    if title:
        return title
    attrs = person.get("domainAttributes") or {}
    return f"{attrs.get('firstName') or ''} {attrs.get('lastName') or ''}".strip()


def members_to_persons(members: list[dict]) -> list[dict]:
    """Gruppenmitglieder -> eindeutige [{id, name}]."""
    seen: dict[int, dict] = {}
    for m in members or []:
        p = m.get("person") if isinstance(m, dict) else None
        pid = person_id_from_ref(p) or (m.get("personId") if isinstance(m, dict) else None)
        if not pid or pid <= 0 or pid in seen:
            continue
        seen[pid] = {"id": pid, "name": person_name(p) or f"Person {pid}"}
    return list(seen.values())


# ---------------------------------------------------------------------- Dienste eines Events
def service_entry_is_current(es: dict) -> bool:
    """CT liefert in eventServices auch historische/abgesagte Anfragen mit isValid=false.

    Das C#-Original hat diese mitgezaehlt und angezeigt (dadurch tauchten alte Einteilungen auf).
    """
    if es.get("isValid") is False:
        return False
    if es.get("isDeclined") is True or es.get("declined") is True:
        return False
    return True


def service_entry_person(es: dict) -> tuple[int | None, str]:
    pid = es.get("personId")
    if not pid:
        pid = person_id_from_ref(es.get("person"))
    name = person_name(es.get("person"))
    if not pid and not name:
        name = (es.get("name") or "").strip()  # externe Person / Freitext
    return (int(pid) if pid else None), name


def service_label(es: dict, service_names: dict[int, str], settings: PlannerSettings) -> str | None:
    sid = es.get("serviceId")
    name = service_names.get(sid) if sid is not None else None
    if not name and isinstance(es.get("service"), dict):
        name = es["service"].get("name")
    return settings.canonical_label(name)


def build_event_rows(event: dict, service_names: dict[int, str], settings: PlannerSettings,
                     absent_ids: set[int]) -> tuple[list[dict], list[dict]]:
    """Baut die Info-Zeilen (Leitung/Lobpreis/Predigt) und die planbaren Dienst-Zeilen.

    Pro CT-Anfrage (Slot) eine Zeile; mehrere Slots desselben Dienstes werden nummeriert.
    """
    by_label: dict[str, list[dict]] = {}
    for es in event.get("eventServices") or []:
        if not isinstance(es, dict) or not service_entry_is_current(es):
            continue
        label = service_label(es, service_names, settings)
        if not label:
            continue
        pid, name = service_entry_person(es)
        row = {
            "request_id": es.get("id") or 0,
            "label": label,
            "person": None,
            "counter": es.get("counter") or 0,
        }
        if pid or name:
            row["person"] = {
                "id": pid,
                "name": name or f"Person {pid}",
                "external": pid is None,
                "absent": bool(pid and pid in absent_ids),
                "agreed": bool(es.get("agreed")),
            }
        by_label.setdefault(label, []).append(row)

    def rows_for(labels: list[str], placeholder: bool) -> list[dict]:
        out: list[dict] = []
        for label in labels:
            rows = sorted(by_label.get(label, []), key=lambda r: (r["counter"], r["request_id"]))
            if not rows and placeholder:
                out.append({"request_id": 0, "label": label, "slot": "", "person": None})
            for i, r in enumerate(rows):
                r = dict(r)
                r.pop("counter", None)
                r["slot"] = f"{i + 1}" if len(rows) > 1 else ""
                out.append(r)
        return out

    return rows_for(settings.info_services, placeholder=True), rows_for(settings.plannable_services, placeholder=False)


# ---------------------------------------------------------------------- Abwesenheiten
def absent_person_ids(absences: list[dict], day: date) -> set[int]:
    result: set[int] = set()
    for a in absences or []:
        pid = person_id_from_ref(a.get("person")) or a.get("personId")
        if not pid:
            continue
        try:
            start = date.fromisoformat(str(a.get("startDate"))[:10])
            end = date.fromisoformat(str(a.get("endDate"))[:10])
        except ValueError:
            continue
        end_time = str(a.get("endTime") or "")
        # Ganztaegige Abwesenheit mit Ende 00:00 Uhr: der Endtag zaehlt nicht mehr (wie C#-Original)
        if end_time and "T00:00:00" in end_time and end > start:
            end -= timedelta(days=1)
        if end < start:
            end = start
        if start <= day <= end:
            result.add(int(pid))
    return result


# ---------------------------------------------------------------------- Einsatz-Historie
def assignments(events: list[dict], service_names: dict[int, str], settings: PlannerSettings,
                relevant_labels: set[str]) -> list[tuple[date, int]]:
    """Alle (Tag, PersonId) fuer relevante, gueltige Dienste."""
    tz = settings.tz
    out: list[tuple[date, int]] = []
    for ev in events or []:
        start = event_local_start(ev, tz)
        if not start:
            continue
        for es in ev.get("eventServices") or []:
            if not isinstance(es, dict) or not service_entry_is_current(es):
                continue
            if service_label(es, service_names, settings) not in relevant_labels:
                continue
            pid, _ = service_entry_person(es)
            if pid:
                out.append((start.date(), pid))
    return out


def last_assignment_dates(history: list[tuple[date, int]], person_ids: set[int]) -> dict[int, date]:
    last: dict[int, date] = {}
    for day, pid in history:
        if pid in person_ids and (pid not in last or day > last[pid]):
            last[pid] = day
    return last


def month_bounds(today: date, offset: int) -> tuple[date, date]:
    y, m = today.year, today.month + offset
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    first = date(y, m, 1)
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return first, nxt - timedelta(days=1)


def stats_range(today: date) -> tuple[date, date]:
    first_last, _ = month_bounds(today, -1)
    _, last_next = month_bounds(today, 1)
    return min(first_last, date(today.year, 1, 1)), max(last_next, date(today.year, 12, 31))


def person_stats(history: list[tuple[date, int]], persons: list[dict], today: date) -> list[dict]:
    ranges = {
        "last_month": month_bounds(today, -1),
        "current_month": month_bounds(today, 0),
        "next_month": month_bounds(today, 1),
        "year_to_date": (date(today.year, 1, 1), today),
        "year_full": (date(today.year, 1, 1), date(today.year, 12, 31)),
    }
    ids = {p["id"] for p in persons}
    counts = {pid: dict.fromkeys(ranges, 0) for pid in ids}
    for day, pid in history:
        if pid not in counts:
            continue
        for key, (a, b) in ranges.items():
            if a <= day <= b:
                counts[pid][key] += 1
    return [{"id": p["id"], "name": p["name"], **counts[p["id"]]}
            for p in sorted(persons, key=lambda p: p["name"].lower())]


def sort_persons(persons: list[dict]) -> list[dict]:
    """Verfuegbare zuerst, dann wer am laengsten nicht dran war, dann Name."""
    return sorted(persons, key=lambda p: (
        p["absent"] or p["rule_blocked"],
        p["last_date"] or "",
        p["name"].lower(),
    ))


def event_person_services(event: dict, service_names: dict[int, str], settings: PlannerSettings) -> dict[int, list[str]]:
    """PersonId -> Dienste, fuer die die Person in diesem Event schon (gueltig) eingetragen ist."""
    out: dict[int, list[str]] = {}
    for es in event.get("eventServices") or []:
        if not isinstance(es, dict) or not service_entry_is_current(es):
            continue
        pid, _ = service_entry_person(es)
        if not pid:
            continue
        sid = es.get("serviceId")
        label = service_label(es, service_names, settings) or service_names.get(sid) or "Dienst"
        out.setdefault(pid, []).append(label)
    return out


def counts_in_month(history: list[tuple[date, int]], year: int, month: int) -> dict[int, int]:
    counts: dict[int, int] = {}
    for day, pid in history:
        if day.year == year and day.month == month:
            counts[pid] = counts.get(pid, 0) + 1
    return counts
