# CLAUDE.md – ChurchTools Planer (FEG Sulgen)

Kontextdatei für Claude Code. Mit dem Nutzer auf Deutsch kommunizieren. Am Ende einer Arbeitssitzung
den Abschnitt „Aktueller Stand“ aktualisieren.

## Was das Projekt ist
Planungswerkzeug für die Technik-Dienste (Ton, Bild, Podcast inkl. Praktikanten) der FEG Sulgen auf
ChurchTools (`https://feg-sulgen.church.tools`). Personen einer Gruppe werden per Ziehen oder Klick auf
Dienst-Anfragen eines Events eingeteilt; Abwesenheiten, Personen-Regeln, Rotation (letzter Einsatz)
und eine Einsatz-Statistik helfen bei der Auswahl.

Nachfolger der WPF/C#-App `ChurchToolsPlanner.UI` (nicht in diesem Repo). Technisch aufgebaut wie das
Schwesterprojekt ChurchTranslation (`superguddy/churchtranslation`): lokaler FastAPI/uvicorn-Server,
Oberfläche im Browser, Start über `.bat`-Dateien mit zentraler `.venv`.

## Aufbau
- `app.py` – FastAPI-App: Config, Sitzung/Cache, alle `/api/*`-Routen, Start (`main()`).
- `ct_api.py` – ChurchTools-REST-Client (httpx, Cookie-Login, Paging, CSRF-Token fail-open,
  automatische Neuanmeldung bei 401).
- `planner.py` – reine Planungslogik ohne Netzwerk (Dienst-Zuordnung, Abwesenheiten, Statistik, Sortierung).
- `rules.py` – Personen-Regeln (`rules.json`): gesperrte Wochentage, gesperrte Tage/Zeiträume, Monatslimit
  (weich oder `limitHard`), Notiz, `intern` (Praktikant/in). `block_reason(day)` liefert den Sperrgrund.
- `login_store.py` – gemerkter Login, Passwort per Windows-DPAPI (ctypes), übernimmt alten C#-Login.
- `static/` – `services_dialog.js` (Dialog „Dienste bearbeiten“, von Planung und Monat genutzt), `index.html`/`app.js` (Planung), `month.html`/`month.js` (Monat), `rules.html`/`rules.js` (Regeln), `style.css`.
- `config_defaults/` – Startwerte für `rules.json`/`hidden_persons.json` (nur beim ersten Start kopiert).
- `tests/` – pytest; `fake_ct.py` = simulierter ChurchTools-Server (Login test/test).

## Laufzeit und Pfade
- Laufzeit-Ordner `C:\ChurchToolsPlanner\Tool` (`TOOL_DIR`, überschreibbar mit `CT_PLANNER_TOOL_DIR`;
  außerhalb Windows `./Tool`). Dort: `.venv`, `config.json`, `rules.json`, `hidden_persons.json`, `login.json`.
  Diese Dateien werden nie aus dem Repo überschrieben; `Tool/` ist in `.gitignore`.
- Start: `start.bat` → `start_console.bat` (legt bei Bedarf `.venv` über `installieren.bat` an) → `python app.py`.
  Browser öffnet `http://127.0.0.1:8092/`. Config wird nur beim Start gelesen.
- Server lauscht nur auf 127.0.0.1, weil er die ChurchTools-Anmeldung hält. Middleware prüft den Host
  (DNS-Rebinding) und verlangt für schreibende `/api/*`-Aufrufe den Header `X-Planner: 1`.

## Wichtige Designentscheidungen (nicht zurückbauen)
- **Eine Ansicht pro Auswahl:** `GET /api/view?group_id&event_id` liefert Event, Dienste und Personen
  zusammen. Das Frontend nummeriert Anfragen (`viewSeq`) und verwirft veraltete Antworten; die rechte
  Spalte wird beim Event-Wechsel sofort geleert. Das behebt den Fehler der WPF-Version (alte
  Einteilungen eines anderen Events blieben rechts stehen).
- **`isValid=false`** in `eventServices` = abgesagte/ersetzte Anfrage → ignorieren (Anzeige + Statistik).
- **Speichern** schickt nur Änderungen, jeweils mit `expected_person_id`; weicht der aktuelle CT-Stand ab,
  wird diese Änderung nicht geschrieben, sondern als Fehler gemeldet.
- Regeln blockieren wie Abwesenheiten (Wochentag, Sperrtage, verbindliches Monatslimit); weiches
  Monatslimit und „schon im Event eingeteilt“ sind nur Rückfragen. Verbindliches Limit zählt nur Einsätze
  in ANDEREN Events des Monats (`limit_blocks(count - here)`), damit eine bestehende Einteilung im
  gewählten Event nicht sich selbst sperrt.
- **Praktikanten** (`intern` in rules.json, Dienste in config `intern_services`): Praktikanten nur in
  Praktikanten-Dienste (sonst Sperre im Frontend), andere dort nur mit Rückfrage. Vorschläge besetzen
  Praktikanten-Dienste nur mit Praktikanten und normale Dienste nur mit Nicht-Praktikanten;
  `suggestion_labels()` = `suggestion_services ∪ intern_services` (eigener Config-Schlüssel, weil ältere
  config.json-Dateien `suggestion_services` bereits fest enthalten).
- **Vorschläge** (`planner.suggest_assignments`, Feld `suggestions` in `/api/view`): nur offene Dienste
  aus `relevant_labels_for_group ∩ suggestion_services`; Rangfolge Limit nicht erreicht → am längsten
  nicht dran → wenig Einsätze im Monat → Name; jede Person max. einmal pro Event. Das Frontend
  verwirft Vorschläge für Dienste/Personen mit vorgemerkten Änderungen (`activeSuggestions`) und
  übernimmt sie nur als Vormerkung (pending), nie direkt nach ChurchTools.
- **Monatsübersicht** (`build_month`, `/api/month`, `/api/month/suggest`, `/api/month/save`):
  Events des Monats direkt per `get_events(..., include_services=True)` (Cache-Schlüssel `month`),
  Abwesenheiten für den Monat separat. Vorschläge über `planner.suggest_month`: Events chronologisch,
  jeder Vorschlag zählt sofort als Einsatz; Rangfolge Limit → grösster Abstand zum nächsten Einsatz
  (vorher oder nachher) → wenig Einsätze im Monat → Name; vergangene Events nie. Vorgemerkte Änderungen
  werden für „neu berechnen“ als `overrides` mitgeschickt. Speichern läuft je Event über `save_changes`
  (gleiche Konfliktprüfung wie die Planung).
- **Dienstnamen in ChurchTools** (Screenshot Nutzer 18.10.2026, Dialog „Dienst zum Event hinzufügen“):
  Licht, Podcast, Bild, Ton, Praktikant Licht, Praktikant Bild, Praktikant Podcast, Praktikant Ton.
  Die alten WPF-Namen („Praktikant I Tontechnik“, „Praktikant II Podcast“) bleiben als `service_aliases`.
  `migrate_config()` stellt in einer bestehenden config.json Werte um, die noch exakt einem alten Standard
  entsprechen (`OLD_DEFAULTS`); von Hand geänderte Werte bleiben. Licht ist nicht planbar (keine Licht-Gruppe).
- Dienst-Zuordnung über Namen: längster passender Präfix aus `info_services`/`plannable_services`
  plus `service_aliases` (config.json). Welche Dienste für Statistik/Rotation einer Gruppe zählen:
  `group_services` (Gruppenname enthält Schlüssel).

## Tests
- `pip install -r requirements-dev.txt`, dann `python -m pytest tests`.
- Browser-Tests (`tests/test_e2e_browser.py`) brauchen `playwright` + Chromium, sonst übersprungen.
  In Cloud-Sessions: Chromium unter `/opt/pw-browsers/chromium`, kein `playwright install`.
- Gegen das echte ChurchTools kann aus der Cloud nicht getestet werden (kein Zugang/Login).

## Konventionen
- Versionsnummer `APP_VERSION` in `app.py`, Kopfzeile `requirements.txt` und Cache-Buster `?v=` in
  `static/*.html` gemeinsam anheben.
- Code-Kommentare und UI-Texte auf Deutsch (UI mit Umlauten, Code-Kommentare ohne).
- Neue Logik möglichst in `planner.py`/`rules.py` (testbar ohne Netzwerk), `app.py` nur verdrahten.
- Keine Zugangsdaten oder Inhalte aus `Tool/` in Ausgaben oder Commits übernehmen.

## Offene Punkte / zu prüfen am echten ChurchTools
- ~~„Entfernen“ = `POST .../servicerequests/{rid}/decline`~~ – vom Nutzer am echten ChurchTools bestätigt
  (wird als Absage übernommen, 18.10.2026).
- **Dienste zu einem Event hinzufügen/entfernen** (V2.6.0): keine REST-Route vorhanden. Die
  CT-Weboberfläche selbst schickt (Aufzeichnung Nutzer 18.10.2026, F12 → Netzwerk) `POST /?q=churchservice/ajax`
  als Formular: `func=addOrRemoveServiceToEvent`, `id=<eventId>`, je Dienst `colN=<serviceId>`,
  `valN=checked` (nur wenn aktiv) und `countN=<Anzahl>` (leer wenn nicht aktiv); sie schickt alle Dienste
  EINER Dienstgruppe. Wir schicken nur die geänderten Dienste (`ChurchToolsClient.set_event_services`),
  damit nichts versehentlich entfernt wird. RISIKO: ChurchTools schaltet `churchservice/ajax` schrittweise
  ab (Academy „Discontinued and deleted APIs“). Fällt die Funktion weg, Fehlermeldung beachten und die neue
  Anfrage der CT-Oberfläche erneut aufzeichnen lassen. Am echten CT noch nicht über den Planer getestet.
- **REST-Variante für „Dienste bearbeiten“** (V2.6.2): Laut vollständiger openapi.json gibt es
  `PUT /api/events/{id}/servicerequests` mit `{eventId, services:[{serviceId, count}]}` (nicht veraltet;
  `PUT .../eventservices` ist veraltet). Die Doku sagt nicht, ob fehlende Dienste entfernt werden – darum
  schickt `set_event_services_rest` immer den VOLLSTÄNDIGEN Soll-Stand aller Dienste des Events
  (`planner.event_service_counts` + Änderungen). Schalter config `service_setup_api`: `"ajax"` (Standard,
  wie CT-Oberfläche) oder `"rest"` (am echten CT noch ungetestet; erst an einem Test-Event prüfen).
  Restrisiko: Dienste aus Dienstgruppen, die der angemeldete Nutzer nicht sieht, fehlen im Soll-Stand.
- **GET /events** (laut Doku): from+to = ganzer Zeitraum, page/limit ignoriert, `to` inklusive (wird
  später exklusiv); from+direction = seitenweise, dann wird `to` ignoriert. Bis V2.6.1 schickten wir
  direction UND to → CT lieferte alle künftigen Events (bis 200 Seiten). Seit V2.6.2 nur from+to, eine
  Abfrage, Nachfilter auf startDate < Ende.
- Laut Doku bringt `GET /events/{id}/services/{sid}/possiblepersons` CT-eigene Daten (Abwesenheiten,
  `monthlyUtilization`, `serviceGroupPreferences.maxPerMonth`) – bisher nicht genutzt, möglicher Ersatz/
  Ergänzung für unsere Monatslimits.
- Login-Token: `GET /persons/{id}/logintoken` + Header `Authorization: Login <token>` – nicht umgesetzt.
- **Erkenntnisse aus der offiziellen REST-Doku** (Bereich „Person“, 18.10.2026; vollständige
  openapi.json kam danach, siehe oben):
  - `EventReadAssignment`: `isAccepted` (statt veraltet `agreed`), `index` (statt veraltet `counter`),
    `isValid=false` = abgesagt/ersetzt. Verborgene Person = Platzhalter mit `icon: "user-shield"`; dessen
    `domainIdentifier` ist NICHT die echte Personen-ID (`planner.is_concealed`).
  - Einteilen (PUT servicerequest) erzeugt einen neuen Datensatz mit neuer ID, der alte wird ungültig;
    Absagen erzeugt einen neuen leeren, gültigen Datensatz. Request-IDs daher nach jedem Speichern neu laden.
  - `GroupMember.groupMemberStatus`: active/requested/waiting/to_delete – nur `active` wird geplant.
  - 401 liefert Text „Session expired!“; Alternative zum Cookie-Login wäre `Authorization: Login <token>`.
- CSRF-Token wird mitgeschickt, wenn `/api/csrftoken` einen liefert; sonst ohne.
- Seitengrösse bei Gruppenmitgliedern/Abwesenheiten: wir folgen `meta.pagination`.

## Aktueller Stand (18.10.2026)
- V2.6.2: Events nur noch per from+to laden (vorher wurde `to` wegen direction ignoriert), REST-Variante
  für „Dienste bearbeiten“ (config `service_setup_api`, Standard weiter "ajax"), Absage schickt
  `{"comment": null}`. 48 Tests grün.
- V2.6.1: Anpassungen nach REST-Doku (verborgene Personen, isAccepted/index, nur aktive Gruppenmitglieder,
  verständliche Meldung bei veralteter Anfrage-ID). 46 Tests grün.
- V2.6.0: „Dienste bearbeiten“ (Dienste zum Event hinzufügen/entfernen/Anzahl) in Planung und Monat.
  44 Tests grün (inkl. 8 Browser-Tests).
- V2.5.1: Dienstnamen an ChurchTools angepasst (Praktikant Ton/Podcast/Bild), alte Namen als Aliase,
  automatische Umstellung alter config.json-Standardwerte. 42 Tests grün.
- V2.5.0: Regel „Praktikant/in“ (nur Praktikanten-Dienste) inkl. Vorschlägen für Praktikanten-Dienste.
  41 Tests grün (inkl. 7 Browser-Tests).
- V2.4.0: Gesperrte Tage je Person und verbindliches Monatslimit (Nutzerwunsch für Praktikanten:
  max. 1×/Monat, an bestimmten Tagen nicht verfügbar). 36 Tests grün (inkl. 6 Browser-Tests).
- V2.3.0: Monatsübersicht (mehrere Events einer Gruppe planen, verteilte Vorschläge, gemeinsames Speichern).
  32 Tests grün (inkl. 6 Browser-Tests).
- V2.2.0: Automatischer Vorschlag für offene Dienste (pro Dienst oder alle übernehmen). 27 Tests grün
  (inkl. 5 Browser-Tests).
- V2.1.0: Regel-Seite (Wochentage, Monatslimit, Notiz, Ausblenden je Gruppe), Hinweis bei Doppel-
  Einteilung, Konfliktprüfung beim Speichern, automatische Neuanmeldung. 23 Tests grün (inkl. 4 Browser-Tests).
- V2.0.0: Portierung von C#/WPF nach Python/FastAPI, Fix der veralteten Event-Anzeige.
- Am echten ChurchTools bisher nur „Entfernen“ bestätigt; übrige Funktionen nur gegen `fake_ct.py` getestet.
