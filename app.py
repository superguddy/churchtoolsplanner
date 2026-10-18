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
from pydantic import BaseModel

import planner
from ct_api import ChurchToolsClient, ChurchToolsError
from login_store import LoginStore, dpapi_available

APP_VERSION = "2.0.0"
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
    "plannable_services": ["Podcast", "Ton", "Bild", "Praktikant II Podcast", "Praktikant I Tontechnik"],
    "service_aliases": {"Lobpreis": "Lobpreisleitung"},
    # Gruppenname enthaelt Schluessel -> diese Dienste zaehlen fuer Statistik/Rotation
    "group_services": {
        "Podcast": ["Podcast", "Praktikant II Podcast"],
        "Ton": ["Ton", "Praktikant I Tontechnik"],
        "Bild": ["Bild"],
    },
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


def load_config() -> dict[str, Any]:
    path = _seed("config.json", DEFAULT_CONFIG)
    cfg = dict(DEFAULT_CONFIG)
    user = _read_json(path, {})
    if isinstance(user, dict):
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


def load_rules() -> dict[int, dict]:
    data = _read_json(RULES_PATH, {})
    persons = (data.get("persons") or data.get("Persons") or []) if isinstance(data, dict) else []
    out: dict[int, dict] = {}
    for r in persons:
        r = {str(k).lower(): v for k, v in r.items()}
        if r.get("personid"):
            out[int(r["personid"])] = {"only_sunday": bool(r.get("onlysunday"))}
    return out


def save_rule(person_id: int, only_sunday: bool) -> None:
    rules = load_rules()
    rules.setdefault(person_id, {})["only_sunday"] = only_sunday
    data = {"persons": [{"personId": pid, "onlySunday": r["only_sunday"]} for pid, r in sorted(rules.items())]}
    RULES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def hidden_ids(group_id: int) -> set[int]:
    """hidden_persons.json: {"<groupId>": [personId, ...]} – wird bei jedem Aufruf neu gelesen."""
    data = _read_json(HIDDEN_PATH, {})
    if not isinstance(data, dict):
        return set()
    return {int(i) for i in (data.get(str(group_id)) or []) if str(i).isdigit() and int(i) > 0}


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


def today_local() -> date:
    return datetime.now(SETTINGS.tz).date()


def service_names() -> dict[int, str]:
    client = SESSION.require()
    return SESSION.cached("services", lambda: {s["id"]: s.get("name") or "" for s in client.get_services()},
                          ttl=3600)


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


def group_persons(group_id: int) -> list[dict]:
    client = SESSION.require()
    members = SESSION.cached(("members", group_id), lambda: client.get_group_members(group_id))
    hidden = hidden_ids(group_id)
    return [p for p in planner.members_to_persons(members) if p["id"] not in hidden]


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

    view: dict[str, Any] = {"group_id": group_id, "event_id": event_id, "event": None,
                            "info": [], "services": [], "persons": []}
    if event:
        info, services = planner.build_event_rows(event, service_names(), SETTINGS, absent)
        view["event"] = {"id": event_id, "display": planner.event_display(event, SETTINGS.tz),
                         "date": day.isoformat() if day else None,
                         "is_sunday": bool(day and day.weekday() == 6)}
        view["info"], view["services"] = info, services

    if group_id:
        group = group_or_404(group_id)
        persons = group_persons(group_id)
        relevant = SETTINGS.relevant_labels_for_group(group.get("name"))
        history = planner.assignments(service_history(), service_names(), SETTINGS, relevant)
        last = planner.last_assignment_dates(history, {p["id"] for p in persons})
        rules = load_rules()
        is_sunday = bool(day and day.weekday() == 6)
        rows = []
        for p in persons:
            only_sunday = rules.get(p["id"], {}).get("only_sunday", False)
            rows.append({
                **p,
                "absent": p["id"] in absent,
                "only_sunday": only_sunday,
                "rule_blocked": bool(only_sunday and day and not is_sunday),
                "last_date": last[p["id"]].isoformat() if p["id"] in last else None,
            })
        view["persons"] = planner.sort_persons(rows)
        view["relevant_services"] = sorted(relevant)
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


class RuleBody(BaseModel):
    only_sunday: bool


@app.post("/api/rules/{person_id}")
def api_rule(person_id: int, body: RuleBody) -> dict:
    save_rule(person_id, body.only_sunday)
    return {"person_id": person_id, "only_sunday": body.only_sunday}


class Change(BaseModel):
    request_id: int
    person_id: int | None = None  # None = austragen (absagen)


class SaveBody(BaseModel):
    group_id: int | None = None
    changes: list[Change]


@app.post("/api/events/{event_id}/save")
def api_save(event_id: int, body: SaveBody) -> dict:
    client = SESSION.require()
    current = event_detail(event_id)
    valid_ids = {es.get("id") for es in current.get("eventServices") or [] if planner.service_entry_is_current(es)}
    errors: list[str] = []
    done = 0
    for ch in body.changes:
        if ch.request_id not in valid_ids:
            errors.append(f"Anfrage {ch.request_id} gehoert nicht (mehr) zu diesem Event.")
            continue
        try:
            if ch.person_id:
                client.assign_service_request(event_id, ch.request_id, ch.person_id)
            else:
                client.decline_service_request(event_id, ch.request_id)
            done += 1
        except ChurchToolsError as exc:
            errors.append(str(exc))
    SESSION.invalidate("event", "history", "events")
    view = build_view(body.group_id, event_id)
    return {"saved": done, "errors": errors, "view": view}


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
