# ChurchTools Planer (Python)

Nachfolger der WPF-App `ChurchToolsPlanner.UI`. Technisch aufgebaut wie ChurchTranslation:
lokaler FastAPI/uvicorn-Server, Oberfläche im Browser, Start über `start.bat`.

## Start (Windows)
1. Python x64 (3.10–3.14) installieren.
2. `start.bat` doppelklicken. Beim ersten Mal legt `installieren.bat` die Umgebung
   `C:\ChurchToolsPlanner\Tool\.venv` an.
3. Der Browser öffnet `http://127.0.0.1:8092/` automatisch.

## Konfiguration (`C:\ChurchToolsPlanner\Tool`)
Wird beim ersten Start angelegt und danach nie überschrieben:
- `config.json` – ChurchTools-URL, Port, erlaubte Gruppen, Dienstnamen, Zeiträume
- `rules.json` – Personen-Regeln (gesperrte Wochentage, Monatslimit, Notiz); alte „onlySunday“-Einträge
  werden automatisch als „nur So“ übernommen. Pflege über die Seite **Regeln** (`/regeln`).
- `hidden_persons.json` – ausgeblendete Personen je Gruppe (Format wie bisher, ebenfalls auf der Seite **Regeln**)
- `login.json` – gemerkter Login (Passwort per Windows-DPAPI verschlüsselt wie bisher;
  ein vorhandener Login der alten App wird übernommen)

Der Server lauscht nur auf `127.0.0.1`, weil er die ChurchTools-Anmeldung hält.

## Gegenüber der WPF-Version geändert
- **Fehler behoben:** Beim Event-Wechsel konnten rechts Einteilungen eines anderen Events stehen bleiben.
  Ursache 1: Nach „Speichern“ wurde das gespeicherte Event neu geladen, auch wenn inzwischen ein anderes
  ausgewählt war. Ursache 2: ChurchTools liefert abgesagte/ersetzte Anfragen mit `isValid=false` mit –
  diese wurden angezeigt und mitgezählt. Jetzt: eine zusammenhängende Ansicht pro Auswahl, veraltete
  Antworten werden verworfen, ungültige Anfragen ignoriert.
- Mehrere Slots desselben Dienstes erscheinen als eigene Zeilen (z. B. „Ton 1“, „Ton 2“).
- Nur geänderte Dienste werden gespeichert; „Verwerfen“-Knopf; Rückfrage bei ungespeicherten Änderungen.
- Personen: letzter Einsatz sichtbar; Einteilen per Ziehen oder Klick (Person, dann Dienst).
- Anfragen ohne Zusage sind als „angefragt“ markiert.

## Seit 2.1.0
- Seite **Regeln**: je Person Wochentage sperren („nie Mi“, „nur So“), Monatslimit, Notiz,
  Ausblenden je Gruppe. Gesperrte Tage werden in der Planung wie eine Abwesenheit markiert und blockiert.
  Rechtsklick auf eine Person: „Nie am <Wochentag> einplanen“ oder „Regeln bearbeiten …“.
- Hinweis, wenn eine Person im selben Event schon für einen anderen Dienst eingeteilt ist.
- Speichern überschreibt keine Änderungen, die inzwischen jemand direkt in ChurchTools gemacht hat.
- Abgelaufene ChurchTools-Sitzung: automatische Neuanmeldung (Passwort nur im Arbeitsspeicher).

## Seit 2.2.0
- **Vorschlag für offene Dienste:** Ist eine Gruppe und ein Event gewählt, steht bei jedem offenen Dienst
  der Gruppe ein Vorschlag („Vorschlag: Name (zuletzt …)“). Ausgewählt wird, wer am längsten nicht dran war;
  abwesende, regelgesperrte und im Event schon eingeteilte Personen nie, Personen mit erreichtem
  Monatslimit nur, wenn sonst niemand frei ist. „übernehmen“ bzw. „Alle Vorschläge übernehmen“ merkt nur
  vor – gespeichert wird erst mit „Speichern“. Für welche Dienste: `suggestion_services` in config.json
  (Standard Podcast, Ton, Bild; Praktikanten-Dienste bewusst nicht).

## Seit 2.3.0
- Seite **Monat** (`/monat`): Gruppe und Monat wählen, alle Events des Monats als Tabelle
  (Zeilen = Events, Spalten = Dienste der Gruppe). Pro Zelle Personen-Auswahl mit Markierungen
  (abwesend/Regel gesperrt, schon im Event, Anzahl Einsätze im Monat, Limit). Vorschläge werden über den
  ganzen Monat verteilt berechnet (grösster Abstand zum nächsten Einsatz) und lassen sich einzeln oder alle
  übernehmen; nach Handänderungen „Vorschläge neu berechnen“. Seitenleiste: Einsätze pro Person im Monat.
  „Speichern“ schreibt alle Events auf einmal, mit derselben Konfliktprüfung. Vergangene Events nur lesend.

## Seit 2.4.0 (z. B. für Praktikanten)
- **Gesperrte Tage** je Person (einzelne Daten oder Zeiträume mit Grund) auf der Seite Regeln über
  „+ Sperrtag“. Wirken wie eine Abwesenheit (Planung, Monat, Vorschläge), gelten aber nur im Planer.
  Einträge, die über ein Jahr zurückliegen, werden beim Speichern automatisch entfernt.
- **Monatslimit „verbindlich“**: Mit Häkchen ist die Person nach Erreichen des Limits für weitere Einsätze
  im Monat gesperrt (z. B. Praktikant max. 1×/Monat) und wird nicht mehr vorgeschlagen; ohne Häkchen wie
  bisher nur eine Rückfrage.

## Tests
`pip install -r requirements-dev.txt`, dann `python -m pytest tests`. Die Tests laufen gegen
`tests/fake_ct.py`, einen simulierten ChurchTools-Server (Login test/test). Die Browser-Tests
(`tests/test_e2e_browser.py`) laufen nur, wenn `playwright` + Chromium installiert sind, sonst werden sie übersprungen.
Zum Ausprobieren ohne echte Daten: `python tests/fake_ct.py` starten und in `config.json`
`base_url` auf `http://127.0.0.1:8765` stellen.
