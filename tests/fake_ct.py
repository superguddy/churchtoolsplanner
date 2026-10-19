"""Simulierter ChurchTools-Server fuer Tests (nur die vom Planer genutzten Endpunkte).

Start: python tests/fake_ct.py [port]  – Login: test / test
Event 1001 antwortet absichtlich langsam (EVENT_DELAYS), um Wettlaeufe beim Event-Wechsel zu pruefen.
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI()
TODAY = date.today()
SERVICES = [
    {"id": 1, "name": "Leitung", "serviceGroupId": 1, "sortKey": 1},
    {"id": 2, "name": "Lobpreis", "serviceGroupId": 1, "sortKey": 2},
    {"id": 3, "name": "Predigt", "serviceGroupId": 1, "sortKey": 3},
    {"id": 15, "name": "Licht", "serviceGroupId": 2, "sortKey": 1},
    {"id": 10, "name": "Podcast", "serviceGroupId": 2, "sortKey": 2},
    {"id": 12, "name": "Bild", "serviceGroupId": 2, "sortKey": 3},
    {"id": 11, "name": "Tontechnik", "serviceGroupId": 2, "sortKey": 4},
    {"id": 16, "name": "Praktikant Licht", "serviceGroupId": 2, "sortKey": 5},
    {"id": 13, "name": "Praktikant Podcast", "serviceGroupId": 2, "sortKey": 6},
    {"id": 14, "name": "Praktikant Ton", "serviceGroupId": 2, "sortKey": 7},
]
PEOPLE = {1: "Admin Konto", 2453: "Anna Sonntag", 3001: "Beat Ton", 3002: "Carla Ton",
          3003: "Dario Ton", 3010: "Pia Praktikantin", 3662: "Versteckt Person", 4001: "Eva Podcast", 5001: "Pastor Peter"}
GROUPS = [{"id": 41, "name": "Tontechnik"}, {"id": 32, "name": "Podcast"},
          {"id": 38, "name": "Bild-Technik"}, {"id": 99, "name": "Hauskreis"}]
MEMBERS = {41: [1, 2453, 3001, 3002, 3003, 3662, 3010], 32: [1, 4001, 3662], 38: [1, 3002]}
EVENT_DELAYS: dict[int, float] = {1001: 2.5}
WRITES: list[tuple] = []
EVENT_QUERIES: list[dict] = []


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
    EVENT_QUERIES.clear()
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
def events(request: Request, page: int = 1, limit: int = 10):
    """Wie laut REST-Doku: from+to = ganzer Zeitraum (to inklusive, page/limit ignoriert);
    from+direction = seitenweise, `to` wird dann ignoriert."""
    q = request.query_params
    EVENT_QUERIES.append(dict(q))
    f = date.fromisoformat(q["from"])
    paged = "direction" in q
    t = None if paged else date.fromisoformat(q["to"])
    out = []
    for ev in sorted(EVENTS.values(), key=lambda e: e["startDate"]):
        d = date.fromisoformat(ev["startDate"][:10])
        if f <= d and (t is None or d <= t):
            item = dict(ev)
            if q.get("include") != "eventServices":
                item.pop("eventServices")
            out.append(item)
    if not paged:
        return {"data": out, "meta": {"count": len(out)}}
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


@app.post("/")
async def legacy_ajax(request: Request):
    """Alte Schnittstelle ?q=churchservice/ajax, hier nur func=addOrRemoveServiceToEvent."""
    if request.query_params.get("q") != "churchservice/ajax":
        raise HTTPException(404)
    if request.cookies.get("ChurchTools_ct") not in SESSIONS:
        return {"status": "fail", "message": "Sitzung abgelaufen"}
    form = dict(await request.form())
    if form.get("func") != "addOrRemoveServiceToEvent":
        return {"status": "error", "message": "unbekannte Funktion"}
    eid = int(form["id"])
    WRITES.append(("services", eid, form))
    i = 0
    while f"col{i}" in form:
        # Wie beobachtet am echten CT: entfernt wird nur mit Anzahl "0"; leere Anzahl ohne val
        # (Dienst war nie im Event) aendert nichts.
        if form.get(f"val{i}") == "checked":
            set_service_count(eid, int(form[f"col{i}"]), int(form.get(f"count{i}") or 0))
        elif form.get(f"count{i}") == "0":
            set_service_count(eid, int(form[f"col{i}"]), 0)
        i += 1
    return {"status": "success", "data": None}


@app.put("/api/events/{eid}/servicerequests")
async def put_service_counts(eid: int, request: Request):
    """REST-Variante. Strengste Auslegung: nicht genannte Dienste werden entfernt."""
    body = await request.json()
    WRITES.append(("services_rest", eid, body))
    wanted = {int(s["serviceId"]): int(s["count"]) for s in body["services"]}
    for sid in {e["serviceId"] for e in EVENTS[eid]["eventServices"]} | set(wanted):
        set_service_count(eid, sid, wanted.get(sid, 0))
    return Response(status_code=204)


def set_service_count(eid: int, sid: int, want: int) -> None:
    entries = EVENTS[eid]["eventServices"]
    current = [e for e in entries if e["serviceId"] == sid and e.get("isValid", True)]
    for _ in range(want - len(current)):
        new_id = max(e["id"] for ev in EVENTS.values() for e in ev["eventServices"]) + 1
        entries.append(es(new_id, sid, None, counter=len(current) + 1))
    if want < len(current):
        for e in sorted(current, key=lambda e: (e["personId"] is not None, -e["counter"]))[:len(current) - want]:
            e["isValid"] = False


@app.get("/test/event_queries")
def event_queries():
    return EVENT_QUERIES


@app.get("/test/writes")
def writes():
    return WRITES


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765, log_level="warning")
