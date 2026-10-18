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
    services = page.inner_text("#servicesList")
    assert "Beat Ton" not in services and "Eva Podcast" not in services and "Dario" not in services


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
    page.check("#onlyRules")
    assert page.locator("#rulesBody tr").count() >= 2   # Anna (nur So) + Beat (nie Sa)
    page.goto(planner_url)
    page.wait_for_selector("#mainView:not([hidden])")
