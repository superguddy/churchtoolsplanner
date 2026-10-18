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


def test_blocked_dates_and_reason(tmp_path):
    r = rules.PersonRule(5, {2}, 1, "", True, rules.parse_blocked_dates(
        [{"from": "2026-11-12", "to": "2026-11-14", "note": "Prüfung"}, {"from": "2026-12-24"}, {"from": "kaputt"}],
        today=date(2026, 10, 18)))
    assert r.block_reason(date(2026, 11, 13)) == "gesperrt 12.11.–14.11.2026 (Prüfung)"
    assert r.block_reason(date(2026, 12, 24)) == "gesperrt 24.12.2026"
    assert r.block_reason(date(2026, 11, 11)) == "nie Mi"           # Wochentag-Regel
    assert r.block_reason(date(2026, 11, 15)) is None
    p = tmp_path / "rules.json"
    rules.save_rules(p, {5: r})
    back = rules.load_rules(p)[5]
    assert back.limit_hard and len(back.blocked_dates) == 2 and back.blocked_dates[0].note == "Prüfung"


def test_hard_limit_and_old_dates_pruned():
    r = rules.PersonRule(5, max_per_month=1, limit_hard=True)
    assert r.limit_blocks(1) and not r.limit_blocks(0)
    assert not rules.PersonRule(5, max_per_month=1).limit_blocks(3)  # weich: sperrt nie
    old = rules.parse_blocked_dates([{"from": "2024-01-01"}], today=date(2026, 10, 18))
    assert old == []
