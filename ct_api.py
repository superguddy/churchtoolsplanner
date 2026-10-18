"""Schlanker ChurchTools-REST-Client (Ersatz fuer ChurchToolsPlanner.Api/ChurchToolsApiClient.cs).

Nur die Endpunkte, die der Planer wirklich braucht. Alle Antworten werden als rohe
dicts zurueckgegeben; die Auswertung passiert in planner.py (testbar ohne Netzwerk).
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

import httpx


class ChurchToolsError(Exception):
    pass


class ChurchToolsClient:
    EVENTS_PAGE_LIMIT = 50  # CT beantwortet je nach Instanz groessere Limits mit 400
    MAX_PAGES = 200  # Sicherheitsbremse wie im C#-Original

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, follow_redirects=True)
        self._csrf_token: str | None = None
        self._lock = threading.Lock()
        self._credentials: tuple[str, str] | None = None  # nur im Arbeitsspeicher, fuer Neuanmeldung
        self.person_id: int | None = None

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ Basis
    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ChurchToolsError(f"Verbindung zu ChurchTools fehlgeschlagen: {exc}") from exc

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = self._send(method, path, **kwargs)
        # CT-Sitzungen laufen nach einigen Stunden ab: einmal still neu anmelden und wiederholen.
        if resp.status_code == 401 and self._credentials:
            self.login(*self._credentials)
            if "headers" in kwargs and "CSRF-Token" in kwargs["headers"]:
                kwargs["headers"] = {**kwargs["headers"], **self._write_headers()}
            resp = self._send(method, path, **kwargs)
        return resp

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict:
        resp = self._request("GET", path, params=params)
        if resp.status_code >= 400:
            raise ChurchToolsError(f"GET {path} fehlgeschlagen ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    def _write_headers(self) -> dict[str, str]:
        # CT verlangt bei Cookie-Login je nach Instanz einen CSRF-Token fuer schreibende Aufrufe.
        # Das C#-Original kam ohne aus; wir senden ihn, wenn er sich holen laesst (fail-open).
        if self._csrf_token is None:
            try:
                data = self._get_json("/api/csrftoken")
                token = data.get("data")
                self._csrf_token = token if isinstance(token, str) else ""
            except ChurchToolsError:
                self._csrf_token = ""
        return {"CSRF-Token": self._csrf_token} if self._csrf_token else {}

    def _get_paged(self, path: str, params: dict[str, Any], limit: int | None = None) -> list[dict]:
        """Laedt alle Seiten. Nutzt meta.pagination, sonst die 'Seite kleiner als Limit'-Heuristik."""
        items: list[dict] = []
        page = 1
        while page <= self.MAX_PAGES:
            p = dict(params)
            p["page"] = page
            if limit:
                p["limit"] = limit
            data = self._get_json(path, p)
            chunk = data.get("data") or []
            items.extend(chunk)
            pagination = (data.get("meta") or {}).get("pagination") or {}
            last_page = pagination.get("lastPage")
            if last_page is not None:
                if page >= int(last_page):
                    break
            elif not limit or len(chunk) < limit:
                break
            page += 1
        return items

    # ------------------------------------------------------------------ Login
    def login(self, username: str, password: str) -> None:
        with self._lock:
            self._client.cookies.clear()
            self._csrf_token = None
            resp = self._send("POST", "/api/login", data={"username": username, "password": password})
            if resp.status_code >= 400:
                self._credentials = None
                raise ChurchToolsError("Login fehlgeschlagen. Bitte Benutzername/Passwort pruefen.")
            self._credentials = (username, password)
            try:
                data = resp.json().get("data") or {}
                self.person_id = data.get("personId")
            except ValueError:
                self.person_id = None

    # ------------------------------------------------------------------ Lesen
    def get_groups(self) -> list[dict]:
        return self._get_paged("/api/groups", {"only_my_groups": "true"}, limit=100)

    def get_group_members(self, group_id: int) -> list[dict]:
        return self._get_paged(f"/api/groups/{group_id}/members", {"include": "members.person"})

    def get_group_absences(self, group_id: int, from_day: date, to_day: date) -> list[dict]:
        return self._get_paged(
            f"/api/groups/{group_id}/absences",
            {"from": from_day.isoformat(), "to": to_day.isoformat()},
        )

    def get_services(self) -> list[dict]:
        return self._get_json("/api/services").get("data") or []

    def get_event(self, event_id: int) -> dict:
        return self._get_json(f"/api/events/{event_id}").get("data") or {}

    def get_events(self, from_day: date, to_exclusive: date, include_services: bool) -> list[dict]:
        base = {"from": from_day.isoformat(), "to": to_exclusive.isoformat()}
        if include_services:
            base["include"] = "eventServices"
        try:
            return self._get_paged("/api/events", {**base, "direction": "forward", "canceled": "0"},
                                   limit=self.EVENTS_PAGE_LIMIT)
        except ChurchToolsError as exc:
            # Manche Instanzen moegen direction/canceled nicht -> ohne nochmal versuchen (wie C#-Original)
            if "(400)" not in str(exc):
                raise
            return self._get_paged("/api/events", base, limit=self.EVENTS_PAGE_LIMIT)

    # ------------------------------------------------------------------ Schreiben
    def assign_service_request(self, event_id: int, request_id: int, person_id: int) -> None:
        payload = {"comment": None, "isAccepted": True, "name": None, "personId": person_id}
        resp = self._request("PUT", f"/api/events/{event_id}/servicerequests/{request_id}",
                             json=payload, headers=self._write_headers())
        if resp.status_code >= 400:
            raise ChurchToolsError(f"Speichern fehlgeschlagen ({resp.status_code}): {resp.text[:500]}")

    def set_event_services(self, event_id: int, changes: list[tuple[int, int]]) -> None:
        """Dienste eines Events hinzufuegen/entfernen bzw. Anzahl setzen: [(serviceId, anzahl)], 0 = entfernen.

        Es gibt dafuer keine REST-Route; die ChurchTools-Weboberflaeche (Dialog "Dienst zum Event hinzufuegen
        oder entfernen") nutzt die alte Schnittstelle churchservice/ajax, func addOrRemoveServiceToEvent
        (Aufzeichnung vom 18.10.2026). Wir schicken bewusst NUR die geaenderten Dienste mit, so wie die
        Weboberflaeche Dienste anderer Dienstgruppen auch nicht mitschickt.
        """
        if not changes:
            return
        data: dict[str, str] = {"func": "addOrRemoveServiceToEvent", "id": str(event_id)}
        for i, (service_id, count) in enumerate(changes):
            data[f"col{i}"] = str(service_id)
            data[f"count{i}"] = str(count) if count > 0 else ""
            if count > 0:
                data[f"val{i}"] = "checked"
        resp = self._request("POST", "/", params={"q": "churchservice/ajax"}, data=data, headers=self._write_headers())
        if resp.status_code >= 400:
            raise ChurchToolsError(f"Dienste aendern fehlgeschlagen ({resp.status_code}): {resp.text[:500]}")
        try:
            body = resp.json()
        except ValueError:
            raise ChurchToolsError(f"Dienste aendern: unerwartete Antwort von ChurchTools: {resp.text[:300]}") from None
        if isinstance(body, dict) and body.get("status") not in (None, "success"):
            raise ChurchToolsError(f"Dienste aendern fehlgeschlagen: {body.get('message') or body.get('data') or body}")

    def decline_service_request(self, event_id: int, request_id: int) -> None:
        resp = self._request("POST", f"/api/events/{event_id}/servicerequests/{request_id}/decline",
                             json={}, headers=self._write_headers())
        if resp.status_code >= 400:
            raise ChurchToolsError(f"Absagen fehlgeschlagen ({resp.status_code}): {resp.text[:500]}")

