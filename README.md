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
- `rules.json` – „Nur Sonntag“-Regeln (gleiches Format wie bisher, Startwert aus `config_defaults/`)
- `hidden_persons.json` – ausgeblendete Personen je Gruppe (gleiches Format wie bisher)
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

## Tests
`python -m pytest tests` (Planungslogik). `tests/fake_ct.py` ist ein simulierter ChurchTools-Server
zum Ausprobieren ohne echte Daten (Login test/test, `base_url` in config.json auf `http://127.0.0.1:8765`).
