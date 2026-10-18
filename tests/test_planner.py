from datetime import date

import planner

S = planner.PlannerSettings(
    info_services=["Leitung", "Lobpreisleitung", "Predigt"],
    plannable_services=["Podcast", "Ton", "Bild", "Praktikant Podcast", "Praktikant Ton", "Praktikant Bild"],
    service_aliases={"Lobpreis": "Lobpreisleitung", "Praktikant II Podcast": "Praktikant Podcast",
                     "Praktikant Ton": "Praktikant Ton"},
    group_services={"Podcast": ["Podcast", "Praktikant Podcast"], "Ton": ["Ton", "Praktikant Ton"],
                    "Bild": ["Bild", "Praktikant Bild"]},
)
NAMES = {1: "Leitung", 2: "Lobpreis", 11: "Tontechnik", 13: "Praktikant Podcast", 14: "Praktikant Ton"}


def person(pid, name="X Y"):
    return {"domainIdentifier": str(pid), "title": name}


def test_canonical_labels_like_csharp():
    assert S.canonical_label("Tontechnik") == "Ton"
    assert S.canonical_label("Lobpreis") == "Lobpreisleitung"
    # Namen wie in ChurchTools ("Dienst zum Event hinzufuegen") und alte Namen der WPF-App
    assert S.canonical_label("Praktikant Ton") == "Praktikant Ton"
    assert S.canonical_label("Praktikant Podcast") == "Praktikant Podcast"
    assert S.canonical_label("Praktikant Bild") == "Praktikant Bild"
    assert S.canonical_label("Praktikant II Podcast") == "Praktikant Podcast"
    assert S.canonical_label("Praktikant Ton") == "Praktikant Ton"
    assert S.canonical_label("Praktikant Licht") is None and S.canonical_label("Licht") is None
    assert S.canonical_label("Kinderhort") is None


def test_group_relevance():
    assert S.relevant_labels_for_group("Tontechnik") == {"Ton", "Praktikant Ton"}
    assert S.relevant_labels_for_group("Bild-Technik") == {"Bild", "Praktikant Bild"}
    assert S.relevant_labels_for_group("Sonstiges") == set(S.plannable_services)


def test_invalid_requests_are_ignored():
    event = {"eventServices": [
        {"id": 5, "serviceId": 11, "personId": 7, "person": person(7, "Alt"), "isValid": False, "agreed": False},
        {"id": 6, "serviceId": 11, "personId": None, "person": None, "isValid": True},
    ]}
    info, services = planner.build_event_rows(event, NAMES, S, set())
    assert [(r["request_id"], r["person"]) for r in services] == [(6, None)]
    assert [r["label"] for r in info] == ["Leitung", "Lobpreisleitung", "Predigt"]


def test_slots_external_and_absent():
    event = {"eventServices": [
        {"id": 1, "serviceId": 11, "personId": 7, "person": person(7, "Beat"), "isValid": True, "agreed": True, "counter": 1},
        {"id": 2, "serviceId": 11, "personId": 8, "person": person(8, "Carla"), "isValid": True, "agreed": False, "counter": 2},
        {"id": 3, "serviceId": 2, "personId": None, "person": None, "name": "Gastband", "isValid": True},
    ]}
    info, services = planner.build_event_rows(event, NAMES, S, {8})
    assert [(r["label"], r["slot"], r["person"]["name"]) for r in services] == [("Ton", "1", "Beat"), ("Ton", "2", "Carla")]
    assert services[1]["person"]["absent"] and not services[1]["person"]["agreed"]
    lob = info[1]
    assert lob["person"]["external"] and lob["person"]["name"] == "Gastband"


def test_absences_end_midnight_excluded():
    a = [{"person": {"domainIdentifier": "9"}, "startDate": "2026-11-01", "endDate": "2026-11-02", "endTime": "2026-11-02T00:00:00Z"}]
    assert planner.absent_person_ids(a, date(2026, 11, 1)) == {9}
    assert planner.absent_person_ids(a, date(2026, 11, 2)) == set()


def test_stats_and_last_dates():
    hist = [(date(2026, 9, 6), 1), (date(2026, 10, 4), 1), (date(2026, 11, 1), 2), (date(2025, 12, 28), 1)]
    rows = planner.person_stats(hist, [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], date(2026, 10, 18))
    assert rows[0] == {"id": 1, "name": "A", "last_month": 1, "current_month": 1, "next_month": 0, "year_to_date": 2, "year_full": 2}
    assert rows[1]["next_month"] == 1 and rows[1]["year_to_date"] == 0
    assert planner.last_assignment_dates(hist, {1, 2}) == {1: date(2026, 10, 4), 2: date(2026, 11, 1)}


def test_month_bounds_across_year():
    assert planner.month_bounds(date(2026, 1, 15), -1) == (date(2025, 12, 1), date(2025, 12, 31))
    assert planner.month_bounds(date(2026, 12, 15), 1) == (date(2027, 1, 1), date(2027, 1, 31))


def test_sort_persons():
    ps = [{"name": "b", "absent": False, "rule_blocked": False, "last_date": "2026-10-01"},
          {"name": "a", "absent": True, "rule_blocked": False, "last_date": None},
          {"name": "c", "absent": False, "rule_blocked": False, "last_date": None}]
    assert [p["name"] for p in planner.sort_persons(ps)] == ["c", "b", "a"]


def _p(pid, name, last=None, **kw):
    return {"id": pid, "name": name, "absent": False, "rule_blocked": False, "last_date": last,
            "month_count": 0, "limit_reached": False, "in_event": [], **kw}


def test_suggestions_pick_longest_unused_and_skip_unavailable():
    services = [
        {"request_id": 1, "label": "Ton", "person": None},
        {"request_id": 2, "label": "Ton", "person": None},
        {"request_id": 3, "label": "Ton", "person": {"id": 9}},            # schon besetzt
        {"request_id": 4, "label": "Praktikant Ton", "person": None},  # nicht in labels
    ]
    persons = [
        _p(1, "Neu"),                                   # nie eingeteilt -> zuerst
        _p(2, "Alt", "2026-09-01"),
        _p(3, "Kuerzlich", "2026-10-10"),
        _p(4, "Abwesend", absent=True),
        _p(5, "Regel", rule_blocked=True),
        _p(6, "Schon dabei", in_event=["Predigt"]),
        _p(7, "Limit", limit_reached=True),
    ]
    sug = planner.suggest_assignments(services, persons, {"Ton"})
    assert {k: v["name"] for k, v in sug.items()} == {1: "Neu", 2: "Alt"}
    assert sug[1]["reason"] == "noch nie eingeteilt" and sug[2]["reason"] == "zuletzt 01.09.2026"


def test_suggestion_uses_limit_person_only_as_last_resort():
    services = [{"request_id": 1, "label": "Ton", "person": None}]
    sug = planner.suggest_assignments(services, [_p(7, "Limit", limit_reached=True)], {"Ton"})
    assert sug[1]["name"] == "Limit" and "Monatslimit" in sug[1]["reason"]
    assert planner.suggest_assignments(services, [], {"Ton"}) == {}


def test_suggest_month_spreads_and_respects_rules():
    import rules
    ton = lambda rid: {"request_id": rid, "label": "Ton", "person": None}  # noqa: E731
    events = [
        {"id": 1, "date": date(2026, 11, 1), "rows": [ton(11)], "absent": set(), "in_event": {}},
        {"id": 2, "date": date(2026, 11, 8), "rows": [ton(21)], "absent": {2}, "in_event": {}},
        {"id": 3, "date": date(2026, 11, 11), "rows": [ton(31)], "absent": set(), "in_event": {}},  # Mittwoch
        {"id": 4, "date": date(2026, 11, 15), "rows": [ton(41)], "absent": set(), "in_event": {}},
    ]
    persons = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}, {"id": 3, "name": "C"}]
    history = [(date(2026, 10, 25), 1)]                       # A war vor einer Woche dran
    rule_c = {3: rules.PersonRule(3, {2})}                      # C nie mittwochs
    sug = planner.suggest_month(events, persons, history, rule_c, {"Ton"})
    picks = {e: v["name"] for (e, _), v in sug.items()}
    # 01.11.: B (nie dran; A erst vor 7 Tagen) | 08.11.: C (B abwesend) | 11.11. Mi: A (C gesperrt,
    # A 17 Tage Abstand > B 10 Tage) | 15.11.: B (14 Tage Abstand, A 4, C 7)
    assert picks == {1: "B", 2: "C", 3: "A", 4: "B"}
    assert sug[(1, 11)]["reason"] == "noch nie eingeteilt" and sug[(4, 41)]["reason"] == "Abstand 14 Tage"


def test_suggest_month_skips_past_events():
    ev = {"id": 1, "date": date(2026, 10, 4), "rows": [{"request_id": 5, "label": "Ton", "person": None}],
          "absent": set(), "in_event": {}, "past": True}
    assert planner.suggest_month([ev], [{"id": 1, "name": "A"}], [], {}, {"Ton"}) == {}


def test_suggest_month_respects_hard_limit_and_blocked_dates():
    import rules
    ton = lambda rid: {"request_id": rid, "label": "Ton", "person": None}  # noqa: E731
    events = [
        {"id": 1, "date": date(2026, 11, 1), "rows": [ton(11)], "absent": set(), "in_event": {}},
        {"id": 2, "date": date(2026, 11, 8), "rows": [ton(21)], "absent": set(), "in_event": {}},
        {"id": 3, "date": date(2026, 11, 15), "rows": [ton(31)], "absent": set(), "in_event": {}},
    ]
    persons = [{"id": 1, "name": "Praktikant"}]
    r = rules.PersonRule(1, max_per_month=1, limit_hard=True,
                         blocked_dates=rules.parse_blocked_dates([{"from": "2026-11-01"}], today=date(2026, 10, 1)))
    sug = planner.suggest_month(events, persons, [], {1: r}, {"Ton"})
    assert list(sug) == [(2, 21)]   # 01.11. gesperrt, 08.11. eingeplant, danach Limit 1/Monat erreicht


def test_intern_only_for_intern_services():
    services = [{"request_id": 1, "label": "Ton", "person": None},
                {"request_id": 2, "label": "Praktikant Ton", "person": None}]
    persons = [_p(1, "Pia", intern=True), _p(2, "Beat", "2026-10-01")]
    labels = {"Ton", "Praktikant Ton"}
    sug = planner.suggest_assignments(services, persons, labels, {"Praktikant Ton"})
    assert sug[1]["name"] == "Beat" and sug[2]["name"] == "Pia"   # Pia (nie dran) trotzdem nicht fuer Ton
    # ohne Praktikanten bleibt der Praktikanten-Dienst ohne Vorschlag
    sug = planner.suggest_assignments(services, [_p(2, "Beat")], labels, {"Praktikant Ton"})
    assert list(sug) == [1]


def test_suggest_month_intern():
    import rules
    ev = {"id": 1, "date": date(2026, 11, 1), "absent": set(), "in_event": {},
          "rows": [{"request_id": 1, "label": "Ton", "person": None},
                   {"request_id": 2, "label": "Praktikant Ton", "person": None}]}
    persons = [{"id": 1, "name": "Pia"}, {"id": 2, "name": "Beat"}]
    sug = planner.suggest_month([ev], persons, [], {1: rules.PersonRule(1, intern=True)},
                                {"Ton", "Praktikant Ton"}, {"Praktikant Ton"})
    assert sug[(1, 1)]["name"] == "Beat" and sug[(1, 2)]["name"] == "Pia"


def test_concealed_person_and_new_field_names():
    shield = {"domainIdentifier": "999", "title": "Verborgene Person", "icon": "user-shield"}
    event = {"eventServices": [
        {"id": 1, "serviceId": 11, "personId": 999, "person": shield, "isValid": True, "isAccepted": True, "index": 2},
        {"id": 2, "serviceId": 11, "personId": 7, "person": person(7, "Beat"), "isValid": True, "isAccepted": False,
         "index": 1},
    ]}
    _, services = planner.build_event_rows(event, NAMES, S, set())
    assert [r["request_id"] for r in services] == [2, 1]          # nach index sortiert
    hidden = services[1]["person"]
    assert hidden["id"] is None and hidden["concealed"] and hidden["name"] == "Verborgene Person"
    assert services[0]["person"]["agreed"] is False                 # isAccepted statt agreed
    assert planner.person_id_from_ref(shield) is None


def test_only_active_members():
    members = [{"person": person(1, "Aktiv"), "groupMemberStatus": "active"},
               {"person": person(2, "Wartend"), "groupMemberStatus": "waiting"},
               {"person": person(3, "Angefragt"), "groupMemberStatus": "requested"},
               {"person": person(4, "Alt")}]                        # ohne Status (aeltere Versionen)
    assert [p["name"] for p in planner.members_to_persons(members)] == ["Aktiv", "Alt"]
