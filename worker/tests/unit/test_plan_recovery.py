"""After someone says no to every option: what's remembered, what's offered, when it ends."""
from types import SimpleNamespace

import pytest

from tandem_worker.contracts import Declined, RecoveryAnswer
from tandem_worker.recovery import (MAX_ROUNDS, check_prompt, choices, lifted, option_key, paused, remember, rules_ref,
                                    still_declined)
from tandem_worker.rules import RoomRules, Rule, from_legacy

TRIP = ("2026-11-20", "2026-11-24")


def bella(**changed) -> RoomRules:
    """Bella's rules as the QA stores them: firm $2,500 trip, ~$800 flights, nonstop if possible."""
    rows = {"trip_budget_total": {"value": 250000, "hardness": "HARD"},
            "flight_budget_total": {"value": 80000, "hardness": "SOFT"},
            "max_stops": {"value": 0, "hardness": "SOFT"}}
    rows.update({k: {**rows.get(k, {}), **v} for k, v in changed.items()})
    rules = tuple(x for key, row in rows.items() for x in from_legacy("Bella", key, row))
    return RoomRules(rules=rules, ranking={"Bella": ("comfort", "experience", "time", "togetherness", "affordability")})


def flight(code, cents, stops, bag=True, id_=None):
    return {"id": id_ or f"Bella-{code}", "category": "FLIGHT", "owner": "Bella", "flight_identity": f"{code}2026-11-20",
            "title": f"{code} LAX-HNL", "total_cents": cents, "stops": stops, "baggage_included": bag}


HA1, AA31, UA2034, WN3302 = flight("HA1", 135000, 0), flight("AA31", 148000, 0, False), flight("UA2034", 43000, 1, False), \
    flight("WN3302", 35000, 2)


def said_no(rules, *options, seq=1):
    return [Declined(owner="Bella", reason="no", seq=seq, **remember(rules, "Bella", "flight", c)) for c in options]


def picked(rules, choice, seq=10, owner="Bella"):
    return RecoveryAnswer(owner=owner, decision_id="flight", rules_ref=rules_ref(rules, owner), choice=choice, seq=seq)


def test_the_same_option_from_a_new_search_is_the_same_option():
    again = {**HA1, "id": "Bella-HA1-second-search", "total_cents": 125000}
    assert option_key(again) == option_key(HA1) and option_key(HA1) != option_key(AA31)


def test_a_no_remembers_which_of_their_preferences_the_option_missed():
    [ha1, ua] = said_no(bella(), HA1, UA2034)
    assert ha1.unmet == ({"category": "FLIGHT", "attribute": "total_cents", "operator": "lte", "value": 80000},)
    assert ua.unmet == ({"category": "FLIGHT", "attribute": "stops", "operator": "lte", "value": 0},)


def test_a_declined_option_stays_out_until_something_changes_in_its_favour():
    rules = bella()
    declines = said_no(rules, HA1, AA31, UA2034, WN3302)
    assert still_declined(declines, [], [HA1, AA31, UA2034, WN3302], rules, "flight") == {c["id"] for c in (HA1, AA31, UA2034, WN3302)}
    # She'd now pay up to $1,350: only the option that held back on price comes back.
    raised = bella(flight_budget_total={"value": 135000})
    assert still_declined(declines, [], [HA1, AA31, UA2034, WN3302], raised, "flight") == {AA31["id"], UA2034["id"], WN3302["id"]}
    # Cheaper than when she said no is a reason; the same price isn't.
    assert lifted(declines[0], {**HA1, "total_cents": 120000}, rules, [])
    assert not lifted(declines[0], HA1, rules, [])
    # Another decision is another question.
    assert not still_declined(declines, [], [HA1], rules, "stay")


def test_taking_one_back_lifts_only_that_one_and_only_after_the_no():
    rules = bella()
    [ha1, ua] = said_no(rules, HA1, UA2034, seq=5)
    take = picked(rules, {"id": "accept-1", "kind": "accept", "option": option_key(HA1)}, seq=6)
    assert lifted(ha1, HA1, rules, [take]) and not lifted(ua, UA2034, rules, [take])
    earlier = take.model_copy(update={"seq": 4})  # she took it back, then said no again
    assert not lifted(ha1, HA1, rules, [earlier])


def test_the_ways_forward_are_the_smallest_change_per_preference_least_important_first():
    rules = bella()
    declined = [HA1, AA31, UA2034, WN3302]
    offered, left = choices("Bella", "flight", "FLIGHT", said_no(rules, *declined), [], declined,
                            {c["id"] for c in declined}, rules, *TRIP)
    assert left and [c["id"] for c in offered] == ["change-1", "change-2", "later", "wider", "pause"]
    budget, stops = offered[0], offered[1]
    # She ranked affordability last, so a higher fare comes before a connection; $1,350 not $1,480, 1 stop not 2.
    assert budget["patches"] == [{"key": "flight_budget_total", "value": 135000, "hardness": "SOFT", "unit": "USD_cents",
                                  "source_quote": budget["label"]}]
    assert budget["option"] == option_key(HA1)
    assert stops["patches"] == [{"key": "max_stops", "value": 1, "hardness": "SOFT", "unit": "", "source_quote": stops["label"]}]
    assert offered[2]["search"] == {"shift_days": 1} and offered[2]["label"] == "Look at leaving a day later (Nov 21)"


def test_a_flexible_limit_with_a_stated_top_keeps_it():
    rules = bella(flight_budget_total={"value": 80000, "authorized_range": {"max": 140000}})
    offered, _ = choices("Bella", "flight", "FLIGHT", said_no(rules, HA1), [], [HA1], {HA1["id"]}, rules, *TRIP)
    assert offered[0]["patches"][0]["authorized_range"] == {"max": 140000}


def test_a_rule_that_mirrors_no_named_limit_is_changed_as_a_rule():
    early = Rule(owner="Bella", category="FLIGHT", attribute="duration_minutes", operator="lte", value=400, kind="soft",
                 importance=4, tolerance={"limit": 900})
    rules = RoomRules(rules=(early,))
    long = {**UA2034, "duration_minutes": 550}
    offered, _ = choices("Bella", "flight", "FLIGHT", said_no(rules, long), [], [long], {long["id"]}, rules, *TRIP)
    assert offered[0]["patches"] == [{"rule": {"category": "FLIGHT", "attribute": "duration_minutes", "operator": "lte",
                                               "value": 550, "kind": "soft", "importance": 4, "tolerance": {"limit": 900}},
                                      "source_quote": offered[0]["label"]}]


def test_an_option_that_only_cost_more_can_be_taken_after_all():
    rules = bella()
    pricier = flight("HA3", 70000, 0)  # meets everything she said; she just didn't want to pay the difference
    offered, _ = choices("Bella", "flight", "FLIGHT", said_no(rules, pricier), [], [pricier], {pricier["id"]}, rules, *TRIP)
    assert offered[0] == {"id": "accept-1", "kind": "accept", "option": option_key(pricier),
                          "label": "Take HA3 LAX-HNL (nonstop, $700) after all"}


def test_a_search_already_tried_isnt_offered_again_until_her_rules_change():
    rules = bella()
    declines, pool = said_no(rules, HA1), [HA1]
    wider = picked(rules, {"id": "wider", "kind": "search", "search": {"wider": True}})
    offered, _ = choices("Bella", "flight", "FLIGHT", declines, [wider], pool, {HA1["id"]}, rules, *TRIP)
    assert "wider" not in [c["id"] for c in offered]
    changed = bella(max_stops={"value": 1})
    offered, _ = choices("Bella", "flight", "FLIGHT", declines, [wider], pool, {HA1["id"]}, changed, *TRIP)
    assert "wider" in [c["id"] for c in offered]


def test_leaving_later_is_offered_only_while_a_night_is_left():
    rules = bella()
    offered, _ = choices("Bella", "flight", "FLIGHT", [], [], [], set(), rules, "2026-11-23", "2026-11-24")
    assert [c["id"] for c in offered] == ["wider", "pause"]


def test_after_the_rounds_run_out_only_pausing_is_left():
    rules = bella()
    declined = [HA1, UA2034]
    answers = [picked(rules, {"id": "wider", "kind": "search", "search": {"wider": True}}, seq=n) for n in range(MAX_ROUNDS)]
    offered, left = choices("Bella", "flight", "FLIGHT", said_no(rules, *declined), answers, declined,
                            {c["id"] for c in declined}, rules, *TRIP)
    assert not left and [c["id"] for c in offered] == ["pause"]


def test_a_pause_holds_until_she_changes_something():
    rules = bella()
    pause = picked(rules, {"id": "pause", "kind": "pause"})
    assert paused([pause], rules, "Bella", "flight")
    assert not paused([pause], rules, "Bella", "stay")
    assert not paused([pause], bella(max_stops={"value": 1}), "Bella", "flight"), "her own change picks it up again"
    other = rules.model_copy(update={"rules": rules.rules + (Rule(owner="Alex", aggregate="my_cost", operator="lte", value=90000),)})
    assert paused([pause], other, "Bella", "flight"), "someone else's change doesn't"


@pytest.mark.parametrize("prompt,recommended", [("", None), ("Go up to $1,350.", None), ("Which one?", "invented")])
def test_the_agents_wording_is_used_only_if_it_asks_and_points_to_an_offered_choice(prompt, recommended):
    with pytest.raises(ValueError):
        check_prompt(SimpleNamespace(prompt=prompt, recommended=recommended), [{"id": "pause"}])
    assert check_prompt(SimpleNamespace(prompt="Pause for now?", recommended="pause"), [{"id": "pause"}]) == ("Pause for now?", "pause")
