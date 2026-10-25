"""Profile onboarding on the worker side: one catalog everywhere, plain facts
from answers only, and room suggestions only from answers a trip can use."""
import json
from pathlib import Path

from tandem_worker.profile_policy import catalog, fact, facts, fallback_summary, summary_ok
from tandem_worker.profile_suggest import asks_first, suggestions

ROOT = Path(__file__).resolve().parents[3]


def test_every_copy_of_the_question_catalog_matches_the_source():
    source = json.loads((ROOT / "contracts" / "profile_questions.json").read_text())
    assert catalog() == source, "run scripts/sync-catalogs.py"
    for copy in ("spacetimedb/src/profile_catalog.ts", "web/lib/profile_catalog.ts"):
        text = (ROOT / copy).read_text()
        body = text[text.index("= ") + 2: text.rindex(" as const")]
        assert json.loads(body) == source, f"{copy} is stale: run scripts/sync-catalogs.py"


def test_answers_become_plain_facts_and_skips_say_nothing():
    stated = facts([
        {"question_id": "C01", "status": "ANSWERED", "value": "Sam"},
        {"question_id": "C02", "status": "ANSWERED", "value": {"selected": ["food", "other"], "other": "tide pools"}},
        {"question_id": "C03", "status": "ANSWERED", "value": {"ranked": ["affordability", "comfort"], "confirmed": True}},
        {"question_id": "C04", "status": "SKIPPED", "value": None},
        {"question_id": "T06", "status": "ANSWERED", "value": {"selected": "own_room", "strength": "REQUIRED"}},
        {"question_id": "N01", "status": "ANSWERED", "value": {"items": [{"food": "peanuts", "checks": ["cross_contact"]}]}},
        {"question_id": "A01", "status": "ANSWERED", "value": {"avatar": "otter"}},
    ])
    assert stated == [
        "What should we call you? Sam",
        "What do you most enjoy doing when you get away? Food and local restaurants, Other (tide pools)",
        "Priorities, most important first: Affordability, Comfort",
        "What sleeping arrangement usually works for you? I need my own room (a must)",
        "Must avoid: peanuts (Cross-contact precautions)",
    ]
    assert fallback_summary(stated).startswith("Here's what I know about you so far: What should we call you? Sam.")


def test_a_summary_is_short_plain_and_not_a_question():
    assert summary_ok("You love food and beaches, and peanuts are always off the table.")
    assert not summary_ok("Does this sound like you?")
    assert not summary_ok("x" * 1201)


def test_rooms_get_suggestions_only_from_answers_a_trip_can_use():
    profile = {"A06": {"city": "New York", "country": "USA", "airports": ["JFK", "LGA"]},
               "T06": {"selected": "own_room", "strength": "PREFERRED"},
               "T04": {"selected": "carry_on"}, "C09": {"selected": "ask_first"},
               "C02": {"selected": ["food"]}}
    offered = suggestions(profile)
    assert set(offered) == {"origin_airport", "own_bedroom"}
    assert offered["origin_airport"].patch() == {"key": "origin_airport", "value": "JFK", "hardness": "HARD", "unit": "",
                                                 "source_profile": "A06"}
    assert offered["own_bedroom"].hardness == "SOFT", "a preference stays a preference"
    assert asks_first(profile) is True and asks_first({"C09": {"selected": "ask_per_plan"}}) is True and asks_first({}) is None
    assert suggestions({}) == {}
