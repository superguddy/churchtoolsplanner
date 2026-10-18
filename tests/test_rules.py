import json
from datetime import date

import rules


def test_legacy_only_sunday_is_migrated(tmp_path):
    p = tmp_path / "rules.json"
    p.write_text(json.dumps({"persons": [{"personId": 2453, "onlySunday": True}]}))
    r = rules.load_rules(p)[2453]
    assert r.blocked_weekdays == {0, 1, 2, 3, 4, 5}
    assert r.summary() == "nur So"
    assert r.blocks(date(2026, 10, 28))        # Mittwoch
    assert not r.blocks(date(2026, 11, 1))     # Sonntag


def test_summary_texts():
    assert rules.PersonRule(1, {2}).summary() == "nie Mi"
    assert rules.PersonRule(1, {0, 2}).summary() == "nie Mo, Mi"
    assert rules.PersonRule(1, {0, 1, 2, 3, 4}).summary() == "nur Sa, So"
    assert rules.PersonRule(1, set(range(7))).summary() == "nie"
    assert rules.PersonRule(1).summary() == ""


def test_roundtrip_drops_empty_rules(tmp_path):
    p = tmp_path / "rules.json"
    rules.save_rules(p, {1: rules.PersonRule(1, {6}, 2, " Notiz "), 2: rules.PersonRule(2)})
    data = json.loads(p.read_text())
    assert data == {"persons": [{"personId": 1, "blockedWeekdays": [6], "maxPerMonth": 2, "note": "Notiz"}]}
    back = rules.load_rules(p)[1]
    assert (back.blocked_weekdays, back.max_per_month, back.note) == ({6}, 2, "Notiz")


def test_broken_file_gives_no_rules(tmp_path):
    p = tmp_path / "rules.json"
    p.write_text("{kaputt")
    assert rules.load_rules(p) == {}
