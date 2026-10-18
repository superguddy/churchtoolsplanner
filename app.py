"""ChurchTools Planer (Python/FastAPI) – Nachfolger der WPF-App ChurchToolsPlanner.UI.

Aufbau wie ChurchTranslation: lokaler FastAPI-Server (uvicorn), Oberflaeche im Browser
(static/), Start ueber start.bat, zentrale Python-Umgebung + Config unter TOOL_DIR.
"""
from __future__ import annotations

import json
import os
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import planner
import rules as rules_mod
from ct_api import ChurchToolsClient, ChurchToolsError
from login_store import LoginStore, dpapi_available

APP_VERSION = "2.6.0"
APP_DIR = Path(__file__).resolve().parent
TOOL_DIR = Path(os.environ.get("CT_PLANNER_TOOL_DIR")
                or (r"C:\ChurchToolsPlanner\Tool" if os.name == "nt" else str(APP_DIR / "Tool")))
TOOL_DIR.mkdir(parents=True, exist_ok=True)
DEFAULTS_DIR = APP_DIR / "config_defaults"

DEFAULT_CONFIG: dict[str, Any] = {
    "base_url": "https://feg-sulgen.church.tools",
    "host": "127.0.0.1",
    "port": 8092,
    "open_browser": True,
    "timezone": "Europe/Zurich",
    "allowed_groups": ["Bild-Technik", "Podcast", "Tontechnik"],
    "info_services": ["Leitung", "Lobpreisleitung", "Predigt"],
    # Dienstnamen wie in ChurchTools ("Dienst zum Event hinzufuegen"); Licht bewusst nicht (keine Licht-Gruppe)
    "plannable_services": ["Podcast", "Ton", "Bild", "Praktikant Podcast", "Praktikant Ton", "Praktikant Bild"],
    # Alte Namen der WPF-App weiterhin erkennen
    "service_aliases": {"Lobpreis": "Lobpreisleitung", "Praktikant II Podcast": "Praktikant Podcast",
                        "Praktikant I Tontechnik": "Praktikant Ton"},
    # Gruppenname enthaelt Schluessel -> diese Dienste zaehlen fuer Statistik/Rotation
    "group_services": {
        "Podcast": ["Podcast", "Praktikant Podcast"],
        "Ton": ["Ton", "Praktikant Ton"],
        "Bild": ["Bild", "Praktikant Bild"],
    },
    # Fuer diese Dienste macht "Vorschlag" automatisch Vorschlaege (Praktikanten-Dienste bewusst nicht)
    "suggestion_services": ["Podcast", "Ton", "Bild"],
    # Praktikanten-Dienste: nur Personen mit Regel "Praktikant/in"; bekommen ebenfalls Vorschlaege
    "intern_services": ["Praktikant Podcast", "Praktikant Ton", "Praktikant Bild"],
    "past_event_days": 28,
    "future_event_days": 84,
    "history_days": 365,
    "cache_seconds": 120,
}


# ====================================================================== Config-Dateien
def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _seed(name: str, fallback: Any) -> Path:
    """Legt TOOL_DIR/<name> beim ersten Start aus config_defaults an (wird danach nie ueberschrieben)."""
    target = TOOL_DIR / name
    if not target.exists():
        src = DEFAULTS_DIR / name
        content = src.read_text(encoding="utf-8-sig") if src.exists() else json.dumps(fallback, indent=2, ensure_ascii=False)
        target.write_text(content, encoding="utf-8")
    return target


# Werte, die fruehere Versionen beim ersten Start in config.json geschrieben haben. Steht in der Datei
# noch genau so ein alter Standard (also nie von Hand geaendert), wird er auf den neuen umgestellt.
OLD_DEFAULTS: dict[str, list[Any]] = {
    "plannable_services": [["Podcast", "Ton", "Bild", "Praktikant II Podcast", "Praktikant I Tontechnik"]],
    "service_aliases": [{"Lobpreis": "Lobpreisleitung"}],
    "group_services": [{"Podcast": ["Podcast", "Praktikant II Podcast"], "Ton": ["Ton", "Praktikant I Tontechnik"],
                        "Bild": ["Bild"]}],
    "intern_services": [["Praktikant I Tontechnik", "Praktikant II Podcast"]],
}


def migrate_config(user: dict[str, Any]) -> list[str]:
    changed = []
    for key, olds in OLD_DEFAULTS.items():
        if key in user and user[key] in olds:
            user[key] = DEFAULT_CONFIG[key]
            changed.append(key)
    return changed


def load_config() -> dict[str, Any]:
    path = _seed("config.json", DEFAULT_CONFIG)
    cfg = dict(DEFAULT_CONFIG)
    user = _read_json(path, {})
    if isinstance(user, dict):
        if migrate_config(user):
            try:
                path.write_text(json.dumps(user, indent=2, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass  # dann eben nur im Speicher umgestellt
        cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    return cfg


CONFIG = load_config()
RULES_PATH = _seed("rules.json", {"persons": []})
HIDDEN_PATH = _seed("hidden_persons.json", {})
SETTINGS = planner.PlannerSettings(
    info_services=CONFIG["info_services"],
    plannable_services=CONFIG["plannable_services"],
    service_aliases=CONFIG["service_aliases"],
    group_services=CONFIG["group_services"],
    timezone=CONFIG["timezone"],
)
LEGACY_LOGIN = Path(os.environ["APPDATA"]) / "ChurchToolsPlanner" / "login.json" if os.environ.get("APPDATA") else None
LOGIN_STORE = LoginStore(TOOL_DIR / "login.json", legacy_path=LEGACY_LOGIN)


def load_rules() -> dict[int, rules_mod.PersonRule]:
    return rules_mod.load_rules(RULES_PATH)


def _hidden_map() -> dict[int, set[int]]:
    data = _read_json(HIDDEN_PATH, {})
    if not isinstance(data, dict):
        return {}
    out: dict[int, set[int]] = {}
    for gid, ids in data.items():
        if str(gid).isdigit() and isinstance(ids, list):
            out[int(gid)] = {int(i) for i in ids if str(i).isdigit() and int(i) > 0}
    return out


def set_hidden(group_id: int, person_id: int, hidden: bool) -> None:
    data = _hidden_map()
    ids = data.setdefault(group_id, set())
    (ids.add if hidden else ids.discard)(person_id)
    out = {str(g): sorted(v) for g, v in sorted(data.items()) if v}
    tmp = HIDDEN_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
    tmp.replace(HIDDEN_PATH)


def hidden_ids(group_id: int) -> set[int]:
    """hidden_persons.json: {"<groupId>": [personId, ...]} – wird bei jedem Aufruf neu gelesen."""
    return _hidden_map().get(group_id, set())


# ====================================================================== Sitzung + Cache
class Session:
    def __init__(self) -> None:
        self.client: ChurchToolsClient | None = None
        self.username = ""
        self._cache: dict[Any, tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()
        self._key_locks: dict[Any, threading.Lock] = {}

    def require(self) -> ChurchToolsClient:
        if not self.client:
            raise HTTPException(status_code=401, detail="Nicht angemeldet.")
        return self.client

    def cached(self, key: Any, loader: Callable[[], Any], ttl: float | None = None) -> Any:
        ttl = CONFIG["cache_seconds"] if ttl is None else ttl
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
            lock = self._key_locks.setdefault(key, threading.Lock())
        with lock:  # gleiche Anfrage nicht doppelt parallel an CT schicken
            with self._cache_lock:
                hit = self._cache.get(key)
                if hit and time.monotonic() - hit[0] < ttl:
                    return hit[1]
            value = loader()
            with self._cache_lock:
                self._cache[key] = (time.monotonic(), value)
            return value

    def invalidate(self, *prefixes: str) -> None:
        with self._cache_lock:
            for key in list(self._cache):
                name = key[0] if isinstance(key, tuple) else key
                if not prefixes or name in prefixes:
                    del self._cache[key]


SESSION = Session()


def intern_labels() -> set[str]:
    return set(CONFIG["intern_services"])


def suggestion_labels(relevant: set[str]) -> set[str]:
    """Dienste mit Vorschlaegen: suggestion_services plus Praktikanten-Dienste (nur mit Praktikanten besetzt)."""
    return relevant & (set(CONFIG["suggestion_services"]) | intern_labels())


def today_local() -> date:
    return datetime.now(SETTINGS.tz).date()


def services_full() -> list[dict]:
    client = SESSION.require()
    return SESSION.cached("services_full", client.get_services, ttl=3600)


def service_names() -> dict[int, str]:
    return {s["id"]: s.get("name") or "" for s in services_full()}


def setup_group_ids() -> set[int]:
    """Dienstgruppen, deren Dienste im Dialog "Dienste bearbeiten" erscheinen: die der planbaren Dienste."""
    planbar = set(SETTINGS.plannable_services)
    return {s.get("serviceGroupId") for s in services_full()
            if SETTINGS.canonical_label(s.get("name")) in planbar and s.get("serviceGroupId") is not None}


def groups_by_id() -> dict[int, dict]:
    client = SESSION.require()
    allowed = {planner.normalize(g) for g in CONFIG["allowed_groups"]}

    def load() -> dict[int, dict]:
        return {g["id"]: g for g in client.get_groups()
                if not allowed or planner.normalize(g.get("name")) in allowed}
    return SESSION.cached("groups", load, ttl=3600)


def group_or_404(group_id: int) -> dict:
    group = groups_by_id().get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Gruppe nicht gefunden.")
    return group


def all_group_persons(group_id: int) -> list[dict]:
    client = SESSION.require()
    members = SESSION.cached(("members", group_id), lambda: client.get_group_members(group_id))
    return planner.members_to_persons(members)


def group_persons(group_id: int) -> list[dict]:
    hidden = hidden_ids(group_id)
    return [p for p in all_group_persons(group_id) if p["id"] not in hidden]


def group_absences(group_id: int) -> list[dict]:
    client = SESSION.require()
    today = today_local()
    return SESSION.cached(("absences", group_id), lambda: client.get_group_absences(
        group_id, today - timedelta(days=CONFIG["past_event_days"] + 7),
        today + timedelta(days=CONFIG["future_event_days"] + 7)), ttl=300)


def service_history() -> list[dict]:
    """Alle Events mit Diensten ueber Rotation- und Statistik-Zeitraum, einmal geladen."""
    client = SESSION.require()
    today = today_local()
    s_from, s_to = planner.stats_range(today)
    start = min(today - timedelta(days=CONFIG["history_days"]), s_from)
    end = max(today + timedelta(days=CONFIG["future_event_days"]), s_to)
    return SESSION.cached("history", lambda: client.get_events(start, end + timedelta(days=1), include_services=True))


def event_detail(event_id: int) -> dict:
    client = SESSION.require()
    return SESSION.cached(("event", event_id), lambda: client.get_event(event_id), ttl=30)


# ====================================================================== FastAPI
app = FastAPI(title="ChurchTools Planer", version=APP_VERSION)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


@app.middleware("http")
async def guard(request: Request, call_next):
    # Der Server haelt die ChurchTools-Anmeldung. Darum nur lokale Aufrufe zulassen
    # (Host-Pruefung gegen DNS-Rebinding) und schreibende API-Aufrufe nur mit eigenem Header
    # (fremde Webseiten koennen diesen ohne CORS-Freigabe nicht setzen).
    if CONFIG["host"] in ("127.0.0.1", "localhost"):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Nur lokaler Zugriff erlaubt."}, status_code=403)
    if request.url.path.startswith("/api/") and request.method != "GET" \
            and request.headers.get("x-planner") != "1":
        return JSONResponse({"detail": "Ungueltige Anfrage."}, status_code=403)
    return await call_next(request)


@app.exception_handler(ChurchToolsError)
async def ct_error(_: Request, exc: ChurchToolsError):
    return JSONResponse({"detail": str(exc)}, status_code=502)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(APP_DIR / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/regeln")
def rules_page() -> FileResponse:
    return FileResponse(APP_DIR / "static" / "rules.html", headers={"Cache-Control": "no-store"})


@app.get("/monat")
def month_page() -> FileResponse:
    return FileResponse(APP_DIR / "static" / "month.html", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------- Login
class LoginBody(BaseModel):
    username: str
    password: str = ""
    remember: bool = False


@app.get("/api/session")
def session_info() -> dict:
    saved_user, saved_pw = LOGIN_STORE.load()
    return {
        "logged_in": SESSION.client is not None,
        "username": SESSION.username,
        "saved_username": saved_user,
        "has_saved_password": bool(saved_pw),
        "can_remember_password": dpapi_available(),
        "base_url": CONFIG["base_url"],
        "version": APP_VERSION,
    }


@app.post("/api/login")
def login(body: LoginBody) -> dict:
    username = body.username.strip()
    password = body.password
    if not password:
        saved_user, saved_pw = LOGIN_STORE.load()
        if saved_pw and saved_user == username:
            password = saved_pw
    if not username or not password:
        raise HTTPException(status_code=400, detail="Bitte Benutzername und Passwort eingeben.")
    client = ChurchToolsClient(CONFIG["base_url"])
    try:
        client.login(username, password)
    except ChurchToolsError as exc:
        client.close()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if SESSION.client:
        SESSION.client.close()
    SESSION.client, SESSION.username = client, username
    SESSION.invalidate()
    if body.remember:
        LOGIN_STORE.save(username, password)
    else:
        LOGIN_STORE.clear()
    return session_info()


@app.post("/api/logout")
def logout() -> dict:
    if SESSION.client:
        SESSION.client.close()
    SESSION.client, SESSION.username = None, ""
    SESSION.invalidate()
    return session_info()


# ---------------------------------------------------------------------- Stammdaten
@app.get("/api/groups")
def api_groups() -> list[dict]:
    return sorted(({"id": g["id"], "name": g.get("name") or ""} for g in groups_by_id().values()),
                  key=lambda g: g["name"].lower())


@app.get("/api/events")
def api_events() -> list[dict]:
    client = SESSION.require()
    today = today_local()
    start = today - timedelta(days=CONFIG["past_event_days"])
    end = today + timedelta(days=CONFIG["future_event_days"])
    events = SESSION.cached("events", lambda: client.get_events(start, end + timedelta(days=1), include_services=False))
    out = []
    for ev in events:
        local = planner.event_local_start(ev, SETTINGS.tz)
        if not local or not (start <= local.date() <= end):
            continue
        out.append({"id": ev["id"], "display": planner.event_display(ev, SETTINGS.tz),
                    "start": local.isoformat(), "past": local.date() < today})
    return sorted(out, key=lambda e: e["start"])


# ---------------------------------------------------------------------- Planungsansicht
def build_view(group_id: int | None, event_id: int | None) -> dict:
    """Eine konsistente Momentaufnahme: Event, Dienste und Personenliste gehoeren immer zusammen.

    Dadurch kann die Oberflaeche nie Dienste eines Events mit Daten eines anderen mischen.
    """
    event = event_detail(event_id) if event_id else None
    local = planner.event_local_start(event, SETTINGS.tz) if event else None
    day = local.date() if local else None

    absent: set[int] = set()
    if group_id and day:
        absent = planner.absent_person_ids(group_absences(group_id), day)

    rules = load_rules()
    view: dict[str, Any] = {"group_id": group_id, "event_id": event_id, "event": None,
                            "info": [], "services": [], "persons": [], "suggestions": {}}
    in_event: dict[int, list[str]] = {}
    if event:
        info, services = planner.build_event_rows(event, service_names(), SETTINGS, absent)
        for row in [*info, *services]:
            p = row.get("person")
            rule = rules.get(p["id"]) if p and p.get("id") else None
            reason = rule.block_reason(day) if rule else None
            if reason:
                p["rule_reason"] = reason
        in_event = planner.event_person_services(event, service_names(), SETTINGS)
        view["event"] = {"id": event_id, "display": planner.event_display(event, SETTINGS.tz),
                         "date": day.isoformat() if day else None,
                         "weekday": day.weekday() if day else None,
                         "weekday_name": rules_mod.WEEKDAYS[day.weekday()] if day else None}
        view["info"], view["services"] = info, services

    if group_id:
        group = group_or_404(group_id)
        persons = group_persons(group_id)
        relevant = SETTINGS.relevant_labels_for_group(group.get("name"))
        history = planner.assignments(service_history(), service_names(), SETTINGS, relevant)
        last = planner.last_assignment_dates(history, {p["id"] for p in persons})
        month_counts = planner.counts_in_month(history, day.year, day.month) if day else {}
        here = {}  # Einsaetze in diesem Event (zaehlen beim verbindlichen Limit nicht als "anderswo")
        for row in view["services"]:
            pid = (row.get("person") or {}).get("id")
            if pid and row["label"] in relevant:
                here[pid] = here.get(pid, 0) + 1
        rows = []
        for p in persons:
            rule = rules.get(p["id"]) or rules_mod.PersonRule(p["id"])
            count = month_counts.get(p["id"], 0)
            reason = rule.block_reason(day)
            if not reason and day and rule.limit_blocks(count - here.get(p["id"], 0)):
                reason = f"{rule.limit_text()} erreicht"
            rows.append({
                **p,
                "absent": p["id"] in absent,
                "rule": rule.to_api(),
                "rule_blocked": reason is not None,
                "rule_reason": reason or rule.summary(),
                "month_count": count,
                "limit_reached": bool(day and rule.max_per_month and count >= rule.max_per_month),
                "in_event": in_event.get(p["id"], []),
                "intern": rule.intern,
                "last_date": last[p["id"]].isoformat() if p["id"] in last else None,
            })
        view["persons"] = planner.sort_persons(rows)
        view["relevant_services"] = sorted(relevant)
        if event:
            view["suggestions"] = {str(k): v for k, v in planner.suggest_assignments(
                view["services"], view["persons"], suggestion_labels(relevant), intern_labels()).items()}
        view["intern_services"] = sorted(intern_labels())
    return view


@app.get("/api/view")
def api_view(group_id: int | None = None, event_id: int | None = None) -> dict:
    SESSION.require()
    return build_view(group_id, event_id)


@app.get("/api/groups/{group_id}/stats")
def api_stats(group_id: int) -> dict:
    group = group_or_404(group_id)
    persons = group_persons(group_id)
    relevant = SETTINGS.relevant_labels_for_group(group.get("name"))
    history = planner.assignments(service_history(), service_names(), SETTINGS, relevant)
    return {"group_id": group_id, "rows": planner.person_stats(history, persons, today_local()),
            "as_of": datetime.now(SETTINGS.tz).strftime("%d.%m.%Y %H:%M")}


# ---------------------------------------------------------------------- Regel-Seite
@app.get("/api/rules")
def api_rules() -> dict:
    """Alle Personen der erlaubten Gruppen (auch ausgeblendete) mit ihren Regeln."""
    SESSION.require()
    rules = load_rules()
    hidden = _hidden_map()
    groups = api_groups()
    persons: dict[int, dict] = {}
    for g in groups:
        for p in all_group_persons(g["id"]):
            entry = persons.setdefault(p["id"], {**p, "groups": [], "hidden_in": []})
            entry["groups"].append(g["id"])
            if p["id"] in hidden.get(g["id"], set()):
                entry["hidden_in"].append(g["id"])
    rows = []
    for p in sorted(persons.values(), key=lambda x: x["name"].lower()):
        rule = rules.get(p["id"]) or rules_mod.PersonRule(p["id"])
        rows.append({**p, "rule": rule.to_api()})
    return {"groups": groups, "persons": rows, "weekdays": rules_mod.WEEKDAYS}


class BlockedDatesBody(BaseModel):
    from_: str = Field(alias="from")
    to: str | None = None
    note: str = ""


class RuleBody(BaseModel):
    blocked_weekdays: list[int] = []
    max_per_month: int | None = None
    limit_hard: bool = False
    blocked_dates: list[BlockedDatesBody] = []
    note: str = ""
    intern: bool = False


@app.put("/api/rules/{person_id}")
def api_rule(person_id: int, body: RuleBody) -> dict:
    rules = load_rules()
    rule = rules_mod.PersonRule(
        person_id,
        {d for d in body.blocked_weekdays if 0 <= d <= 6},
        body.max_per_month if body.max_per_month and body.max_per_month > 0 else None,
        body.note.strip()[:500],
        body.limit_hard,
        rules_mod.parse_blocked_dates([{"from": b.from_, "to": b.to, "note": b.note} for b in body.blocked_dates]),
        body.intern,
    )
    rules[person_id] = rule
    rules_mod.save_rules(RULES_PATH, rules)
    return {"person_id": person_id, "rule": rule.to_api()}


class HiddenBody(BaseModel):
    group_id: int
    person_id: int
    hidden: bool


@app.post("/api/hidden")
def api_hidden(body: HiddenBody) -> dict:
    group_or_404(body.group_id)
    set_hidden(body.group_id, body.person_id, body.hidden)
    return {"ok": True}


class Change(BaseModel):
    request_id: int
    person_id: int | None = None  # None = austragen (absagen)
    expected_person_id: int | None = None  # Stand beim Laden; Abweichung = jemand hat in CT geaendert


class SaveBody(BaseModel):
    group_id: int | None = None
    changes: list[Change]


def save_changes(event_id: int, changes: list[Change], prefix: str = "") -> tuple[int, list[str]]:
    """Schreibt Aenderungen eines Events nach ChurchTools; nur wo der CT-Stand noch dem erwarteten entspricht."""
    client = SESSION.require()
    SESSION.invalidate("event")  # frischer Stand, um Aenderungen anderer zu erkennen
    current = event_detail(event_id)
    entries = {es.get("id"): es for es in current.get("eventServices") or []
               if isinstance(es, dict) and planner.service_entry_is_current(es)}
    names = service_names()
    errors: list[str] = []
    done = 0
    for ch in changes:
        es = entries.get(ch.request_id)
        if es is None:
            errors.append(f"{prefix}Anfrage {ch.request_id} gehoert nicht (mehr) zu diesem Event.")
            continue
        now_pid, now_name = planner.service_entry_person(es)
        if now_pid != ch.expected_person_id:
            label = planner.service_label(es, names, SETTINGS) or "Dienst"
            errors.append(f"{prefix}{label}: wurde inzwischen in ChurchTools geaendert (jetzt: {now_name or 'leer'}) "
                          "- nicht ueberschrieben.")
            continue
        try:
            if ch.person_id:
                client.assign_service_request(event_id, ch.request_id, ch.person_id)
            else:
                client.decline_service_request(event_id, ch.request_id)
            done += 1
        except ChurchToolsError as exc:
            errors.append(f"{prefix}{exc}")
    SESSION.invalidate("event", "history", "events", "month")
    return done, errors


class ServiceSetupChange(BaseModel):
    service_id: int
    count: int  # 0 = Dienst entfernen


class ServiceSetupBody(BaseModel):
    group_id: int | None = None
    changes: list[ServiceSetupChange]


@app.get("/api/events/{event_id}/servicesetup")
def api_service_setup(event_id: int) -> dict:
    SESSION.invalidate("event")
    event = event_detail(event_id)
    return {"event_id": event_id, "services": planner.event_service_setup(event, services_full(), setup_group_ids())}


@app.post("/api/events/{event_id}/servicesetup")
def api_service_setup_save(event_id: int, body: ServiceSetupBody) -> dict:
    """Dienste zum Event hinzufuegen/entfernen (ChurchTools-Dialog "Dienst zum Event hinzufuegen oder entfernen")."""
    client = SESSION.require()
    allowed = {s["id"] for s in services_full() if s.get("serviceGroupId") in setup_group_ids()}
    changes = [(c.service_id, max(0, min(c.count, 20))) for c in body.changes]
    bad = [sid for sid, _ in changes if sid not in allowed]
    if bad:
        raise HTTPException(status_code=400, detail=f"Dienst(e) {bad} koennen hier nicht geaendert werden.")
    client.set_event_services(event_id, changes)
    SESSION.invalidate("event", "history", "events", "month")
    return {"view": build_view(body.group_id, event_id),
            "services": planner.event_service_setup(event_detail(event_id), services_full(), setup_group_ids())}


@app.post("/api/events/{event_id}/save")
def api_save(event_id: int, body: SaveBody) -> dict:
    done, errors = save_changes(event_id, body.changes)
    return {"saved": done, "errors": errors, "view": build_view(body.group_id, event_id)}


# ---------------------------------------------------------------------- Monatsuebersicht
def month_bounds(month: str) -> tuple[date, date]:
    try:
        first = datetime.strptime(month, "%Y-%m").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Monat im Format JJJJ-MM angeben.") from exc
    return planner.month_bounds(first, 0)


def build_month(group_id: int, month: str, overrides: dict[tuple[int, int], int | None]) -> dict:
    """Alle Events eines Monats mit den Diensten der Gruppe, Verfuegbarkeit je Event und Vorschlaegen.

    overrides: vorgemerkte (noch nicht gespeicherte) Aenderungen der Oberflaeche, (event_id, request_id) ->
    PersonId oder None. Sie fliessen nur in die Vorschlaege ein.
    """
    client = SESSION.require()
    group = group_or_404(group_id)
    first, last = month_bounds(month)
    relevant = SETTINGS.relevant_labels_for_group(group.get("name"))
    labels = [lbl for lbl in SETTINGS.plannable_services if lbl in relevant]
    persons = group_persons(group_id)
    names_by_pid = {p["id"]: p["name"] for p in persons}
    rules = load_rules()
    svc_names = service_names()
    month_events = SESSION.cached(("month", month), lambda: client.get_events(
        first, last + timedelta(days=1), include_services=True))
    absences = SESSION.cached(("month_absences", group_id, month), lambda: client.get_group_absences(
        group_id, first, last), ttl=300)

    events_out: list[dict] = []
    sim_events: list[dict] = []
    month_history: list[tuple[date, int]] = []
    for ev in sorted(month_events, key=lambda e: e.get("startDate") or ""):
        local = planner.event_local_start(ev, SETTINGS.tz)
        if not local or not (first <= local.date() <= last):
            continue
        day = local.date()
        absent = planner.absent_person_ids(absences, day)
        _, services = planner.build_event_rows(ev, svc_names, SETTINGS, absent)
        rows = [r for r in services if r["label"] in relevant]
        in_event = planner.event_person_services(ev, svc_names, SETTINGS)
        blocked = {p["id"]: rules[p["id"]].block_reason(day) for p in persons
                   if p["id"] in rules and rules[p["id"]].blocks(day)}
        events_out.append({
            "id": ev["id"], "display": planner.event_display(ev, SETTINGS.tz), "date": day.isoformat(),
            "past": day < today_local(), "rows": rows, "absent": sorted(absent), "blocked": {str(k): v for k, v in blocked.items()},
            "in_event": {str(k): v for k, v in in_event.items()},
        })
        # Simulation fuer Vorschlaege: vorgemerkte Aenderungen einrechnen
        sim_rows, sim_in_event = [], {k: list(v) for k, v in in_event.items()}
        for r in rows:
            key = (ev["id"], r["request_id"])
            r2 = dict(r)
            if key in overrides:
                old = r.get("person") or {}
                if old.get("id") and r["label"] in sim_in_event.get(old["id"], []):
                    sim_in_event[old["id"]].remove(r["label"])
                    if not sim_in_event[old["id"]]:
                        del sim_in_event[old["id"]]
                pid = overrides[key]
                r2["person"] = {"id": pid, "name": names_by_pid.get(pid, "")} if pid else None
                if pid:
                    sim_in_event.setdefault(pid, []).append(r["label"])
            if r2.get("person") and r2["person"].get("id"):
                month_history.append((day, r2["person"]["id"]))
            sim_rows.append(r2)
        sim_events.append({"id": ev["id"], "date": day, "rows": sim_rows, "absent": absent,
                           "in_event": sim_in_event, "past": day < today_local()})

    month_ids = {e["id"] for e in events_out}
    other = [ev for ev in service_history() if ev.get("id") not in month_ids]
    history = planner.assignments(other, svc_names, SETTINGS, relevant) + month_history
    last_dates = planner.last_assignment_dates(
        [(d, p) for d, p in history if d < first], {p["id"] for p in persons})
    suggestions = planner.suggest_month(sim_events, persons, history, rules,
                                        suggestion_labels(relevant), intern_labels())
    return {
        "group_id": group_id, "month": month, "labels": labels, "events": events_out,
        "intern_services": sorted(intern_labels()),
        "persons": [{**p, "rule": (rules.get(p["id"]) or rules_mod.PersonRule(p["id"])).to_api(),
                     "last_before": last_dates[p["id"]].isoformat() if p["id"] in last_dates else None}
                    for p in sorted(persons, key=lambda x: x["name"].lower())],
        "suggestions": [{"event_id": e, "request_id": r, **v} for (e, r), v in suggestions.items()],
    }


@app.get("/api/month")
def api_month(group_id: int, month: str) -> dict:
    return build_month(group_id, month, {})


class MonthChange(Change):
    event_id: int


class MonthBody(BaseModel):
    group_id: int
    month: str
    changes: list[MonthChange] = []


@app.post("/api/month/suggest")
def api_month_suggest(body: MonthBody) -> dict:
    overrides = {(c.event_id, c.request_id): c.person_id for c in body.changes}
    return {"suggestions": build_month(body.group_id, body.month, overrides)["suggestions"]}


@app.post("/api/month/save")
def api_month_save(body: MonthBody) -> dict:
    by_event: dict[int, list[Change]] = {}
    for c in body.changes:
        by_event.setdefault(c.event_id, []).append(c)
    displays = {}
    try:
        displays = {e["id"]: e["display"] for e in build_month(body.group_id, body.month, {})["events"]}
    except ChurchToolsError:
        pass
    done, errors = 0, []
    for event_id, changes in sorted(by_event.items()):
        d, errs = save_changes(event_id, changes, prefix=f"{displays.get(event_id, event_id)}: ")
        done += d
        errors += errs
    return {"saved": done, "errors": errors, "view": build_month(body.group_id, body.month, {})}


@app.post("/api/reload")
def api_reload() -> dict:
    SESSION.invalidate()
    return {"ok": True}


def main() -> None:
    import uvicorn

    url = f"http://127.0.0.1:{CONFIG['port']}/"
    print(f"ChurchTools Planer {APP_VERSION}")
    print(f"Config-Ordner: {TOOL_DIR}")
    print(f"Oberflaeche:   {url}")
    if CONFIG.get("open_browser"):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=CONFIG["host"], port=int(CONFIG["port"]), log_level="warning")


if __name__ == "__main__":
    main()
