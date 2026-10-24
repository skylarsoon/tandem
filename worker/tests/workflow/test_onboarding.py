"""Conversational onboarding with six scripted travellers: short, adaptive,
and nothing recorded that they didn't say."""
import json

import pytest

from tandem_worker.backend import LocalBackend
from tandem_worker.fixtures import PERSONAS, PersonaModel, ScriptedTraveller, persona
from tandem_worker.intake_policy import open_topics
from tandem_worker.ports import FakeClock
from tandem_worker.runner import Worker

NAMES = tuple(p["name"] for p in PERSONAS["travellers"])


@pytest.fixture
def trip(tmp_path):
    clock = FakeClock()
    backend = LocalBackend(tmp_path / "backend.sqlite", clock)
    backend.seed_trip(NAMES, organizer=PERSONAS["trip"]["organizer"])
    worker = Worker(backend, PersonaModel(), None, tmp_path / "checkpoints.sqlite", clock=clock)
    return backend, worker


def drain(backend, worker):
    """Run onboarding jobs only (planning is gated separately)."""
    for _ in range(50):
        jobs = [j for j in backend.eligible_jobs() if not j.startswith("job-")]
        if not jobs:
            return
        for job in jobs:
            worker.run_one(job)
    raise AssertionError("onboarding jobs kept coming")


def onboard(backend, worker, travellers=None):
    backend.describe_trip(PERSONAS["trip"]["description"])
    drain(backend, worker)
    assert backend.trip_brief()["state"] == "PROPOSED"
    backend.confirm_trip_brief()
    drain(backend, worker)
    travellers = travellers or {n: ScriptedTraveller(n) for n in NAMES}
    first = {n: True for n in NAMES}
    for _ in range(8):
        for name in NAMES:
            intake = backend.intake_of(name)
            for patch in backend.patches_of(name):
                if patch["state"] == "PENDING" and patch["patch"]["key"] == "origin_airport":
                    backend.confirm_origin(name, patch["id"])  # the one-tap "Flying from X, right?"
            if intake["state"] == "SUMMARY":
                prefs = backend.brief_of(name).preferences
                backend.confirm_intake(name, [p["id"] for p in backend.patches_of(name) if p["state"] == "PENDING"],
                    {"speak_up_for_me": prefs.speak_up_for_me, "ask_me_first": prefs.ask_me_first, "give_me_time": prefs.give_me_time})
            elif intake["state"] == "ASKING" and backend.conversation(name)[-1]["sender"] == "AGENT":
                asked = ["open"] if first[name] else open_topics(intake["coverage"])[:2]
                first[name] = False
                said = travellers[name].reply(asked)
                if said:
                    backend.say(name, said)
        drain(backend, worker)
        if all(backend.intake_of(n)["state"] == "CONFIRMED" for n in NAMES):
            return
    raise AssertionError({n: backend.intake_of(n) for n in NAMES})


def constraints(backend, name):
    return {p["patch"]["key"]: p["patch"] for p in backend.patches_of(name) if p["state"] == "CONFIRMED"}


def test_organizer_description_becomes_a_brief_and_everyone_gets_a_trip_aware_opener(trip):
    backend, worker = trip
    backend.describe_trip(PERSONAS["trip"]["description"])
    drain(backend, worker)
    assert backend.trip_brief()["brief"]["destination_airport"] == "HNL"
    assert "Honolulu (HNL), 2027-02-12 to 2027-02-16" in backend.conversation("sam")[-1]["body"]
    backend.confirm_trip_brief()
    drain(backend, worker)
    opener = backend.conversation("maya")[-1]["body"]
    assert opener.startswith("Hi maya! Honolulu, Feb 12–16 with jordan, priya, sam, luis and dana. Where would you fly from")


@pytest.mark.parametrize("name", NAMES)
def test_each_traveller_is_onboarded_briefly_and_only_from_what_they_said(trip, name):
    backend, worker = trip
    onboard(backend, worker)
    truth, got = persona(name)["truth"], constraints(backend, name)
    for key, expected in truth.items():
        if key in ("speak_up_for_me", "ask_me_first"):
            assert getattr(backend.brief_of(name).preferences, key) == expected, key
            continue
        patch = got.pop(key)
        if isinstance(expected, dict) and "hardness" in expected:
            assert (patch["value"], patch["hardness"]) == (expected["value"], expected["hardness"]), key
            assert (patch.get("authorized_range") or {}).get("max") == expected.get("max"), key
        else:
            assert patch["value"] == expected, key
    assert set(got) <= {"dates_ok"}, f"recorded something not in the truth: {got}"
    said = " ".join(m["body"] for m in backend.conversation(name) if m["sender"] == "HUMAN")
    assert all(p["source_quote"] in said for p in constraints(backend, name).values()), "every item quotes them"
    questions = backend.intake_of(name)["question_count"]
    assert questions <= 3, f"{name} was asked {questions} questions"


def test_whatever_works_is_asked_again_not_turned_into_a_number(trip):
    backend, worker = trip
    onboard(backend, worker)
    convo = backend.conversation("jordan")
    vague = next(i for i, m in enumerate(convo) if "whatever works" in m["body"])
    follow_up = convo[vague + 1]["body"]
    assert follow_up.startswith("It stays between us: is there an amount you'd rather not go above"), follow_up
    assert "$" not in follow_up


def test_summary_shows_their_words_and_readings_and_keeps_reasons_private(trip):
    backend, worker = trip
    onboard(backend, worker)
    summary = next(m["body"] for m in backend.conversation("maya") if m["body"].startswith("Here's what I've got"))
    assert "Around $900 for the whole trip, up to $1,000 if it helps (you said “Around $900 for everything" in summary
    assert "Land between 07:00 and 22:00 (my reading of “please no red-eye”)" in summary
    assert "I'll tell only you when a plan breaks one of your limits" in summary
    assert constraints(backend, "jordan")["trip_budget_total"]["private_rationale"] == "just got laid off"
    assert "laid off" not in json.dumps(backend.shared_report())
    dana = backend.brief_of("dana")
    assert dana.notes == ("Home by Sunday afternoon",)


def test_from_onboarding_to_a_plan_with_just_in_time_questions(tmp_path):
    """Six people onboard by conversation, then the planner works from their answers:
    searches from their airports, asks only what the plan depends on, and publishes."""
    from tandem_worker.fixtures import FixtureResearch
    clock = FakeClock()
    backend = LocalBackend(tmp_path / "backend.sqlite", clock)
    backend.seed_trip(NAMES, organizer=PERSONAS["trip"]["organizer"])
    research = FixtureResearch(clock, "onboarded")
    worker = Worker(backend, PersonaModel(), research, tmp_path / "checkpoints.sqlite", clock=clock)
    onboard(backend, worker)
    asked = {}
    for _ in range(12):
        for job in backend.eligible_jobs():
            worker.run_one(job)
        if backend.shared_report()["proposals"]:
            break
        for name in NAMES:
            for q in backend.owner_questions(name):
                if q.answer is None:
                    asked.setdefault(name, []).append(q.prompt)
                    # Sam doesn't need a bag; everyone agrees to everything else.
                    backend.answer_question(name, q.id, q.version, not q.prompt.endswith("Do you need one?"), "a-" + q.id)
    [proposal] = backend.shared_report()["proposals"].values()
    chosen = {c.get("owner") or c["category"]: c for c in proposal["terms"]["selected"]}
    assert chosen["maya"]["arrival_local"] == "21:00", "her 'no red-eye' rules out the 01:10 fare"
    assert chosen["sam"]["stops"] == 0, "nonstop only"
    assert chosen["STAY"]["title"] == "Downtown 3-bedroom condo"
    assert chosen["TRANSFER"]["title"] == "Airport van, pickup 22:30", "booked for the group's latest landing (22:10)"
    # Priya alone was asked to share, because her own-room request pushed someone past a private limit.
    assert any(p.startswith("Your own-room request") for p in asked["priya"])
    assert not any(p.startswith("Your own-room request") for n in NAMES if n != "priya" for p in asked.get(n, []))
    # Just in time, once: rooming for those who never said, a bag for the one fare without one.
    assert all(sum("OK to share a room?" in p for p in asked[n]) == 1 for n in NAMES if n != "priya")
    assert sum("Do you need one?" in p for p in asked["sam"]) == 1
    assert research.requests[0]["routes"][0]["origin"] == "SEA" and "TRANSFER" not in research.requests[0]["categories"]
    assert "laid off" not in json.dumps(backend.shared_report())
