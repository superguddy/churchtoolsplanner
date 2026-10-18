"""Simulierter ChurchTools-Server fuer Tests (nur die vom Planer genutzten Endpunkte).

Start: python tests/fake_ct.py [port]  – Login: test / test
Event 1001 antwortet absichtlich langsam (EVENT_DELAYS), um Wettlaeufe beim Event-Wechsel zu pruefen.
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI()
TODAY = date.today()
SERVICES = [
    {"id": 1, "name": "Leitung"}, {"id": 2, "name": "Lobpreis"}, {"id": 3, "name": "Predigt"},
    {"id": 10, "name": "Podcast"}, {"id": 11, "name": "Tontechnik"}, {"id": 12, "name": "Bild"},
    {"id": 13, "name": "Praktikant II Podcast"}, {"id": 14, "name": "Praktikant I Tontechnik"},
]
PEOPLE = {1: "Admin Konto", 2453: "Anna Sonntag", 3001: "Beat Ton", 3002: "Carla Ton",
          3003: "Dario Ton", 3010: "Pia Praktikantin", 3662: "Versteckt Person", 4001: "Eva Podcast", 5001: "Pastor Peter"}
GROUPS = [{"id": 41, "name": "Tontechnik"}, {"id": 32, "name": "Podcast"},
          {"id": 38, "name": "Bild-Technik"}, {"id": 99, "name": "Hauskreis"}]
MEMBERS = {41: [1, 2453, 3001, 3002, 3003, 3662, 3010], 32: [1, 4001, 3662], 38: [1, 3002]}
EVENT_DELAYS: dict[int, float] = {1001: 2.5}
WRITES: list[tuple] = []


def person_ref(pid: int) -> dict:
    first, last = PEOPLE[pid].split(" ", 1)
    return {"title": PEOPLE[pid], "domainType": "person", "domainIdentifier": str(pid),
            "apiUrl": f"/api/persons/{pid}", "domainAttributes": {"firstName": first, "lastName": last}}


def es(req_id: int, service_id: int, pid: int | None, agreed=True, valid=True, counter=1, name=None) -> dict:
    return {"id": req_id, "serviceId": service_id, "personId": pid,
            "person": person_ref(pid) if pid else None, "name": name if not pid else PEOPLE[pid],
            "agreed": agreed, "isValid": valid, "counter": counter}


def sunday_offset(weeks: int) -> date:
    return TODAY + timedelta(days=(6 - TODAY.weekday()) % 7 + 7 * weeks)


def make_events() -> dict[int, dict]:
    evs: dict[int, dict] = {}

    def add(eid: int, day: date, name: str, services: list[dict], hour: int = 8):
        start = datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc)
        evs[eid] = {"id": eid, "name": name, "startDate": start.isoformat().replace("+00:00", "Z"),
                    "eventServices": services}

    # A: naechster Sonntag, Ton = Beat, langsam
    add(1001, sunday_offset(1), "Gottesdienst A", [
        es(1, 1, 5001), es(2, 2, None, name="Gastband"), es(3, 3, 5001),
        es(10, 11, 3001), es(11, 10, 4001), es(12, 12, None, valid=True)])
    # B: uebernaechster Sonntag, Ton-Slot hat eine alte, ungueltige Anfrage (Dario hat abgesagt)
    add(1002, sunday_offset(2), "Gottesdienst B", [
        es(20, 1, 5001), es(21, 3, 5001),
        es(22, 11, 3003, agreed=False, valid=False, counter=1),
        es(23, 11, None, counter=1), es(24, 10, None), es(25, 13, None), es(26, 14, None)])
    # C: Mittwoch -> Regel "nur Sonntag" greift
    add(1003, sunday_offset(1) + timedelta(days=3), "Bibelabend", [es(30, 11, None)], hour=17)
    # Historie
    for i, pid in enumerate([3001, 3002, 3001, 2453]):
        add(900 + i, sunday_offset(-(i + 2)), f"Vergangen {i}", [es(800 + i, 11, pid)])
    return evs


EVENTS = make_events()


def reset() -> None:
    """Testdaten auf den Ausgangszustand zuruecksetzen (zwischen Testmodulen)."""
    EVENTS.clear()
    EVENTS.update(make_events())
    WRITES.clear()
    EVENT_DELAYS.clear()


SESSIONS: set[str] = set()
LOGINS: list[str] = []


@app.post("/api/login")
def login(username: str = Form(), password: str = Form()):
    if (username, password) != ("test", "test"):
        raise HTTPException(401, "bad")
    return {"data": {"personId": 1}}


@app.middleware("http")
async def cookie(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/login" and request.cookies.get("ChurchTools_ct") not in SESSIONS:
        return JSONResponse({"message": "Session expired"}, status_code=401)
    resp = await call_next(request)
    if path == "/api/login" and resp.status_code == 200:
        token = f"s{len(LOGINS) + 1}"
        LOGINS.append(token)
        SESSIONS.add(token)
        resp.set_cookie("ChurchTools_ct", token)
    return resp


@app.post("/test/expire")
def expire():
    """Simuliert eine abgelaufene CT-Sitzung."""
    SESSIONS.clear()
    return {"logins": len(LOGINS)}


@app.post("/test/assign/{eid}/{rid}/{pid}")
def external_change(eid: int, rid: int, pid: int):
    """Simuliert eine Aenderung, die jemand anderes direkt in ChurchTools macht."""
    for s in EVENTS[eid]["eventServices"]:
        if s["id"] == rid:
            s.update(personId=pid, person=person_ref(pid), name=PEOPLE[pid], agreed=True)
    return {"ok": True}


@app.get("/api/csrftoken")
def csrf():
    return {"data": "csrf123"}


@app.get("/api/services")
def services():
    return {"data": SERVICES}


@app.get("/api/groups")
def groups(page: int = 1):
    return {"data": GROUPS, "meta": {"pagination": {"current": page, "lastPage": 1}}}


@app.get("/api/groups/{gid}/members")
def members(gid: int, page: int = 1):
    return {"data": [{"personId": p, "person": person_ref(p)} for p in MEMBERS.get(gid, [])],
            "meta": {"pagination": {"current": page, "lastPage": 1}}}


@app.get("/api/groups/{gid}/absences")
def absences(gid: int):
    day = sunday_offset(1)
    return {"data": [{"id": 1, "person": person_ref(3002), "startDate": day.isoformat(),
                      "endDate": (day + timedelta(days=1)).isoformat(),
                      "endTime": f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z"}]}


@app.get("/api/events")
def events(request: Request, page: int = 1, limit: int = 50):
    q = request.query_params
    f, t = date.fromisoformat(q["from"]), date.fromisoformat(q["to"])
    out = []
    for ev in sorted(EVENTS.values(), key=lambda e: e["startDate"]):
        d = date.fromisoformat(ev["startDate"][:10])
        if f <= d < t:
            item = dict(ev)
            if q.get("include") != "eventServices":
                item.pop("eventServices")
            out.append(item)
    chunk = out[(page - 1) * limit: page * limit]
    return {"data": chunk, "meta": {"pagination": {"current": page, "lastPage": max(1, -(-len(out) // limit))}}}


@app.get("/api/events/{eid}")
def event(eid: int):
    time.sleep(EVENT_DELAYS.get(eid, 0))
    if eid not in EVENTS:
        raise HTTPException(404)
    return {"data": EVENTS[eid]}


@app.put("/api/events/{eid}/servicerequests/{rid}")
async def put_request(eid: int, rid: int, request: Request):
    body = await request.json()
    WRITES.append(("put", eid, rid, body, request.headers.get("csrf-token")))
    for s in EVENTS[eid]["eventServices"]:
        if s["id"] == rid:
            pid = body["personId"]
            s.update(personId=pid, person=person_ref(pid), agreed=bool(body["isAccepted"]), name=PEOPLE[pid])
    return {"data": {}}


@app.post("/api/events/{eid}/servicerequests/{rid}/decline")
def decline(eid: int, rid: int):
    WRITES.append(("decline", eid, rid))
    for s in EVENTS[eid]["eventServices"]:
        if s["id"] == rid:
            s.update(isValid=False, agreed=False)
    return {"data": {}}


@app.get("/test/writes")
def writes():
    return WRITES


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765, log_level="warning")
