"""Planer-API gegen den simulierten ChurchTools-Server (tests/fake_ct.py)."""
import importlib
import json
import os

import pytest

H = {"X-Planner": "1"}


@pytest.fixture(scope="module")
def client(fake_ct_url, tmp_path_factory):
    from fastapi.testclient import TestClient

    tool = tmp_path_factory.mktemp("tool")
    (tool / "config.json").write_text(json.dumps({"base_url": fake_ct_url, "open_browser": False}))
    os.environ["CT_PLANNER_TOOL_DIR"] = str(tool)
    import fake_ct
    fake_ct.reset()
    app_mod = importlib.import_module("app")
    importlib.reload(app_mod)
    with TestClient(app_mod.app, base_url="http://127.0.0.1") as c:
        r = c.post("/api/login", json={"username": "test", "password": "test"}, headers=H)
        assert r.status_code == 200, r.text
        yield c


def test_foreign_host_and_missing_header_are_rejected(client):
    assert client.get("/api/session", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/reload").status_code == 403


def test_invalid_requests_hidden_and_legacy_rule(client):
    v = client.get("/api/view", params={"group_id": 41, "event_id": 1003}).json()
    names = {p["name"]: p for p in v["persons"]}
    assert "Admin Konto" not in names                      # hidden_persons.json
    anna = names["Anna Sonntag"]                           # onlySunday aus alter rules.json
    assert anna["rule_blocked"] and anna["rule_reason"] == "nur So"
    v2 = client.get("/api/view", params={"group_id": 41, "event_id": 1002}).json()
    assert all(r["person"] is None for r in v2["services"])  # ungueltige Dario-Anfrage nicht angezeigt


def test_weekday_rule_marks_like_absence(client):
    r = client.put("/api/rules/3001", json={"blocked_weekdays": [6], "max_per_month": None, "note": "Chor"}, headers=H)
    assert r.json()["rule"]["summary"] == "nie So"
    v = client.get("/api/view", params={"group_id": 41, "event_id": 1001}).json()
    beat_person = next(p for p in v["persons"] if p["id"] == 3001)
    assert beat_person["rule_blocked"] and beat_person["rule"]["note"] == "Chor"
    ton = next(s for s in v["services"] if s["label"] == "Ton")
    assert ton["person"]["rule_reason"] == "nie So"        # auch eingeteilte Person markiert
    assert beat_person["in_event"] == ["Ton"]
    client.put("/api/rules/3001", json={"blocked_weekdays": []}, headers=H)


def test_rules_page_lists_hidden_and_toggles(client):
    data = client.get("/api/rules").json()
    admin = next(p for p in data["persons"] if p["id"] == 1)
    assert 41 in admin["hidden_in"]
    client.post("/api/hidden", json={"group_id": 41, "person_id": 1, "hidden": False}, headers=H)
    v = client.get("/api/view", params={"group_id": 41}).json()
    assert any(p["id"] == 1 for p in v["persons"])
    client.post("/api/hidden", json={"group_id": 41, "person_id": 1, "hidden": True}, headers=H)


def test_month_limit(client):
    client.put("/api/rules/3001", json={"max_per_month": 1}, headers=H)
    v = client.get("/api/view", params={"group_id": 41, "event_id": 1001}).json()
    p = next(p for p in v["persons"] if p["id"] == 3001)
    assert p["month_count"] >= 1 and p["limit_reached"]
    client.put("/api/rules/3001", json={}, headers=H)


def test_save_detects_external_change(client, fake_ct_url):
    import httpx
    httpx.post(f"{fake_ct_url}/test/assign/1002/24/4001")   # jemand traegt in CT Eva fuer Podcast ein
    r = client.post("/api/events/1002/save", headers=H, json={"group_id": 41, "changes": [
        {"request_id": 24, "person_id": 3002, "expected_person_id": None},
        {"request_id": 23, "person_id": 3002, "expected_person_id": None},
    ]}).json()
    assert r["saved"] == 1 and "inzwischen" in r["errors"][0]
    rows = {s["label"]: s["person"] for s in r["view"]["services"]}
    assert rows["Podcast"]["name"] == "Eva Podcast" and rows["Ton"]["name"] == "Carla Ton"


def test_relogin_after_session_expired(client, fake_ct_url):
    import httpx
    before = httpx.post(f"{fake_ct_url}/test/expire").json()["logins"]
    client.post("/api/reload", headers=H)
    r = client.get("/api/events")
    assert r.status_code == 200 and r.json()
    after = httpx.post(f"{fake_ct_url}/test/expire").json()["logins"]
    assert after == before + 1
    client.post("/api/reload", headers=H)
    assert client.get("/api/groups").status_code == 200


def test_view_contains_suggestions(client):
    v = client.get("/api/view", params={"group_id": 41, "event_id": 1002}).json()
    ton = next(s for s in v["services"] if s["label"] == "Ton")
    sug = v["suggestions"].get(str(ton["request_id"]))
    if ton["person"] is None:
        assert sug and sug["person_id"] in {p["id"] for p in v["persons"]}
        chosen = next(p for p in v["persons"] if p["id"] == sug["person_id"])
        assert not chosen["absent"] and not chosen["rule_blocked"]
    # Podcast gehoert nicht zur Tontechnik-Gruppe -> kein Vorschlag
    podcast = next(s for s in v["services"] if s["label"] == "Podcast")
    assert str(podcast["request_id"]) not in v["suggestions"]


def _month_of(client, event_id):
    ev = next(e for e in client.get("/api/events").json() if e["id"] == event_id)
    return ev["start"][:7]


def test_month_overview_and_save(client):
    month = _month_of(client, 1003)
    m = client.get("/api/month", params={"group_id": 41, "month": month}).json()
    assert m["labels"] == ["Ton", "Praktikant I Tontechnik"]
    ev = next(e for e in m["events"] if e["id"] == 1003)
    assert "2453" in ev["blocked"]                              # Anna: nur So, Event ist Mittwoch
    sug = [s for s in m["suggestions"] if s["event_id"] == 1003]
    assert sug and sug[0]["person_id"] != 2453
    # Vorgemerkte Aenderung fliesst in neue Vorschlaege ein: Person nicht doppelt im selben Event
    pid = sug[0]["person_id"]
    again = client.post("/api/month/suggest", headers=H, json={"group_id": 41, "month": month, "changes": [
        {"event_id": 1003, "request_id": 30, "person_id": pid, "expected_person_id": None}]}).json()
    assert not [s for s in again["suggestions"] if s["event_id"] == 1003]
    res = client.post("/api/month/save", headers=H, json={"group_id": 41, "month": month, "changes": [
        {"event_id": 1003, "request_id": 30, "person_id": pid, "expected_person_id": None}]}).json()
    assert res["saved"] == 1 and not res["errors"]
    row = next(e for e in res["view"]["events"] if e["id"] == 1003)["rows"][0]
    assert row["person"]["id"] == pid


def test_month_rejects_bad_month(client):
    assert client.get("/api/month", params={"group_id": 41, "month": "2026-13"}).status_code == 400
