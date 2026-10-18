"""Browser-Test der Oberflaeche (optional, braucht `pip install playwright` + Chromium).

Prueft u. a. den Fehler der WPF-Version: Nach schnellem Event-Wechsel duerfen rechts
keine Dienste des vorherigen Events stehen bleiben.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pw = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def planner_url(fake_ct_url, tmp_path_factory):
    import fake_ct
    fake_ct.reset()
    fake_ct.EVENT_DELAYS[1001] = 2.5   # Event A antwortet langsam
    tool = tmp_path_factory.mktemp("tool_e2e")
    port = _free_port()
    (tool / "config.json").write_text(json.dumps({"base_url": fake_ct_url, "open_browser": False, "port": port}))
    env = {**os.environ, "CT_PLANNER_TOOL_DIR": str(tool)}
    proc = subprocess.Popen([sys.executable, str(ROOT / "app.py")], env=env, cwd=str(tool))
    url = f"http://127.0.0.1:{port}/"
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=10)
    fake_ct.reset()


@pytest.fixture(scope="module")
def page(planner_url):
    with pw.sync_playwright() as p:
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
        try:
            browser = p.chromium.launch(executable_path=exe)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium nicht verfuegbar: {exc}")
        pg = browser.new_page(viewport={"width": 1600, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(planner_url)
        pg.fill("#loginUser", "test")
        pg.fill("#loginPass", "test")
        pg.click("#loginBtn")
        pg.wait_for_selector("#mainView:not([hidden])")
        yield pg
        assert not errors, errors
        browser.close()


def title_contains(pg, text, timeout=10000):
    pg.wait_for_function(f"document.getElementById('eventTitle').textContent.includes({text!r})", timeout=timeout)


def test_fast_event_switch_never_shows_old_services(page):
    page.click("#groupsList li:has-text('Tontechnik')")
    page.wait_for_selector("#personsList li .name")
    page.click("#eventsList li:has-text('Gottesdienst A')")
    time.sleep(0.4)
    page.click("#eventsList li:has-text('Gottesdienst B')")
    title_contains(page, "Gottesdienst B")
    time.sleep(3.0)  # jetzt trifft die Antwort fuer A ein und muss verworfen werden
    assert "Gottesdienst B" in page.inner_text("#eventTitle")
    # nur die eingeteilten Personen pruefen (Vorschlaege stehen in .suggestion)
    assigned = " | ".join(page.locator("#servicesList .who > span:first-child").all_inner_texts())
    assert "Beat Ton" not in assigned and "Eva Podcast" not in assigned and "Dario" not in assigned


def test_suggestion_fills_open_service(page):
    # Event B ist nach dem ersten Test gewaehlt: Ton ist offen, Vorschlag sichtbar
    page.wait_for_selector("#suggestBtn:not([hidden])")
    ton = page.locator("#servicesList li", has_text="Ton").first
    assert "Vorschlag:" in ton.inner_text()
    page.click("#suggestBtn")
    assert "Vorschlag:" not in ton.inner_text()
    assert page.is_hidden("#suggestBtn") and not page.is_disabled("#saveBtn")
    page.click("#discardBtn")
    page.wait_for_selector("#suggestBtn:not([hidden])")


def test_assign_save_and_rule_blocking(page):
    page.drag_and_drop("#personsList li:has-text('Carla Ton')", "#servicesList li:has-text('Ton')")
    assert "Carla Ton" in page.inner_text("#servicesList li:has-text('Ton')")
    page.click("#saveBtn")
    page.wait_for_function("document.getElementById('saveStatus').textContent.includes('Gespeichert')")
    page.click("#eventsList li:has-text('Bibelabend')")
    title_contains(page, "Bibelabend")
    anna = page.inner_text("#personsList li:has-text('Anna Sonntag')")
    assert "Regel: nur So" in anna
    msgs = []
    page.once("dialog", lambda d: (msgs.append(d.message), d.accept()))
    page.drag_and_drop("#personsList li:has-text('Anna Sonntag')", "#servicesList li:has-text('Ton')")
    assert msgs and "nur So" in msgs[0]


def test_context_menu_blocks_weekday(page):
    page.click("#personsList li:has-text('Beat Ton')", button="right")
    item = page.locator("#contextMenu [data-action=weekday]")
    assert "Nie am Mi einplanen" in item.inner_text()
    item.click()
    page.wait_for_function("document.querySelector('#personsList').innerText.includes('Regel: nie Mi')")


def test_rules_page(page, planner_url):
    page.goto(planner_url + "regeln")
    page.wait_for_selector("#rulesBody tr#p3001")
    row = page.locator("tr#p3001")
    assert "nie Mi" in row.inner_text()
    row.locator("button.day", has_text="Mi").click()   # Mittwoch wieder freigeben
    page.wait_for_function("document.getElementById('saveState').textContent.includes('Gespeichert')")
    row.locator("button.day", has_text="Sa").click()
    page.wait_for_function("document.querySelector('tr#p3001').innerText.includes('nie Sa')")
    # Sperrtag hinzufuegen und wieder entfernen
    row.locator("button", has_text="+ Sperrtag").click()
    row = page.locator("tr#p3001")
    row.locator(".dateadder input[type=date]").first.fill("2026-12-24")
    row.locator(".dateadder input.datenote").fill("Ferien")
    row.locator(".dateadder button", has_text="hinzufügen").click()
    page.wait_for_function("document.querySelector('tr#p3001 .datechip') && document.querySelector('tr#p3001 .datechip').innerText.includes('24.12.2026')")
    row.locator(".datechip button").click()
    page.wait_for_function("!document.querySelector('tr#p3001 .datechip')")
    page.check("#onlyRules")
    assert page.locator("#rulesBody tr").count() >= 2   # Anna (nur So) + Beat (nie Sa)
    page.goto(planner_url)
    page.wait_for_selector("#mainView:not([hidden])")


def test_month_page_apply_suggestions_and_save(page, planner_url):
    import fake_ct
    fake_ct.reset()
    page.request.post(planner_url + "api/reload", headers={"X-Planner": "1"})
    month = fake_ct.sunday_offset(1).strftime("%Y-%m")
    page.goto(f"{planner_url}monat?group=41&month={month}")
    page.wait_for_selector("table.month select")
    assert "Ton" in page.inner_text("#monthHead")
    page.wait_for_selector("#suggestAllBtn:not([hidden])")
    page.click("#suggestAllBtn")
    assert page.is_hidden("#suggestAllBtn") and not page.is_disabled("#saveBtn")
    page.click("#saveBtn")
    page.wait_for_function("document.getElementById('saveStatus').textContent.includes('Gespeichert')")
    # nach dem Speichern sind die Zellen besetzt (Auswahl != leer) und nichts mehr vorgemerkt
    assert page.locator(".mcell.changed").count() == 0
    selected = page.eval_on_selector_all("table.month select", "els => els.map(e => e.value)")
    assert any(v for v in selected)
    page.goto(planner_url)
    page.wait_for_selector("#mainView:not([hidden])")


def test_intern_flag_blocks_normal_service(page, planner_url):
    page.goto(planner_url + "regeln")
    page.wait_for_selector("tr#p3010")
    page.locator("tr#p3010 .internflag input").check()
    page.wait_for_function("document.getElementById('saveState').textContent.includes('Gespeichert')")
    page.goto(planner_url)
    page.wait_for_selector("#mainView:not([hidden])")
    page.click("#groupsList li:has-text('Tontechnik')")
    page.click("#eventsList li:has-text('Gottesdienst B')")
    title_contains(page, "Gottesdienst B")
    page.wait_for_selector("#personsList li:has-text('Pia Praktikantin') .badge.intern")
    msgs = []
    page.once("dialog", lambda d: (msgs.append(d.message), d.accept()))
    page.drag_and_drop("#personsList li:has-text('Pia Praktikantin')", "#servicesList li:has-text('Ton') >> nth=0")
    assert msgs and "Praktikanten-Dienste" in msgs[0]
    prak = page.locator("#servicesList li", has_text="Praktikant Ton")
    assert "Vorschlag: Pia Praktikantin" in prak.inner_text()


def test_service_setup_dialog(page, planner_url):
    page.goto(planner_url)
    page.wait_for_selector("#mainView:not([hidden])")
    page.click("#groupsList li:has-text('Bild-Technik')")
    page.click("#eventsList li:has-text('Bibelabend')")
    title_contains(page, "Bibelabend")
    assert "Bild" not in page.inner_text("#servicesList")
    page.click("#setupBtn")
    page.wait_for_selector("dialog.svcdialog tbody tr")
    page.locator("dialog.svcdialog tr", has_text="Bild").first.locator("input[type=checkbox]").check()
    page.click("dialog.svcdialog button:has-text('Speichern')")
    page.wait_for_selector("dialog.svcdialog", state="detached")
    page.wait_for_function("document.getElementById('servicesList').innerText.includes('Bild')")
    assert "Dienste in ChurchTools geändert" in page.inner_text("#saveStatus")
