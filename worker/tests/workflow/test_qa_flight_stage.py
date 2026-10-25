"""The flight-stage QA brief on the real pipeline, offline. The four personas' messages
(verbatim) go through onboarding, read as the QA expects (tests/fixtures/qa/personas.json:
the ground truth a live run's Claude extraction is compared against). Planning then
works from fixture round-trip itineraries (tests/fixtures/qa/flight_stage.json) with the
default optimizer, and every publish is re-checked like the database does."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tandem_worker.backend import LocalBackend
from tandem_worker.contracts import BriefDraft, Candidate, IntakeExtraction, TripBrief
from tandem_worker.fixtures import ScriptedModel
from tandem_worker.intake_policy import open_topics
from tandem_worker.ports import FakeClock
from tandem_worker.runner import Worker

FIXTURES = Path(__file__).parents[1] / "fixtures"
PERSONAS = json.loads((FIXTURES / "qa" / "personas.json").read_text())
FLIGHTS = json.loads((FIXTURES / "qa" / "flight_stage.json").read_text())
PROFILES = json.loads((FIXTURES / "profiles" / "four_personas.answers.json").read_text())
QUESTIONS = json.loads((Path(__file__).parents[2] / "src" / "tandem_worker" / "profile_questions.json").read_text())
NAMES = tuple(t["name"] for t in PERSONAS["travellers"])
HNL, HOME = "Pacific/Honolulu", "America/Los_Angeles"


def traveller(name):
    return next(t for t in PERSONAS["travellers"] if t["name"] == name)


class QAModel(ScriptedModel):
    """Reads each message as the QA brief expects (no model call)."""
    def extract(self, context):
        p, items, settings = traveller(context["owner"]), [], None
        scripted = [*p["replies"], *p.get("changes", {}).values(),
                    *[c for c in PERSONAS["changes"].values() if c["who"] == context["owner"]]]
        for message in context["messages"]:
            if message["sender"] != "HUMAN":
                continue
            for reply in (r for r in scripted if r["says"] == message["body"]):
                items += [{**i, "source_message_id": message["id"]} for i in reply.get("items", [])]
                if reply.get("settings"):
                    settings = {**(settings or {}), **reply["settings"], "source_message_id": message["id"]}
        return IntakeExtraction(items=tuple(items), settings=settings)

    def ask(self, context):
        raise ValueError("onboarding uses its fixed wording")

    def brief(self, context):
        return BriefDraft(brief=TripBrief(**PERSONAS["trip"]["brief"]))


def at(local: str, zone: str) -> int:
    return int(datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(zone)).timestamp())


class QAResearch:
    """The QA's itineraries: a flexible-date search (a day either side of the date asked),
    filtered by the search's own max_stops like the live actor. Targeted searches find
    nothing new: the set is fixed."""
    def __init__(self, variant=None):
        self.variant, self.requests = variant, []

    def flights(self):
        table = {k: list(v) for k, v in FLIGHTS["flights"].items()}
        table.update(FLIGHTS["variants"].get(self.variant or "", {}).get("replace", {}))
        return table

    def search(self, request):
        self.requests.append(json.loads(json.dumps(request)))
        if request["purpose"] != "INITIAL":
            return []
        now, wanted = request["observed_at"], set(request.get("categories") or ())
        common = dict(observed_at=now, valid_until=now + 6 * 3600, run_ref="qa-fixture", mode="FIXTURE")
        found = []
        for route in request.get("routes") or () if "FLIGHT" in wanted else ():
            if route["owner"] not in request["target_owners"]:
                continue
            day = datetime.fromisoformat(route["date"]).date()
            for f in self.flights().get(route["origin"], []):
                if abs((datetime.fromisoformat(f["depart"]).date() - day).days) > 1:
                    continue
                if route.get("max_stops") is not None and f["stops"] > route["max_stops"]:
                    continue
                found.append(Candidate(id=f"{route['owner']}-{f['code']}", owner=route["owner"], total_cents=f["fare_cents"],
                    departure_epoch=at(f["depart"], HOME), departure_timezone=HOME, arrival_epoch=at(f["arrive"], HNL),
                    arrival_timezone=HNL, arrival_local=f["arrive"][11:16], stops=f["stops"],
                    return_departure_epoch=at(f["return_depart"], HNL), return_arrival_epoch=at(f["return_arrive"], HOME),
                    return_stops=f["return_stops"], baggage_included=f["bag"], flight_identity=f["code"] + f["depart"][:10],
                    cost_basis="COMPLETE_FLIGHT_TOTAL", title=f"{f['code']} {route['origin']}-HNL",
                    source_url=f"https://fixtures.invalid/qa/{f['code']}", **common))
        if "STAY" in wanted:
            found += [Candidate(id=f"stay-{i}", category="STAY", cost_basis="COMPLETE_STAY_TOTAL",
                                source_url=f"https://fixtures.invalid/qa/stay/{i}", **{**s, **common})
                      for i, s in enumerate(FLIGHTS["stays"])]
        found += [Candidate(id=f"idea-{i}", cost_basis="ESTIMATE_PER_PERSON", source_url=f"https://fixtures.invalid/qa/idea/{i}",
                            **{**idea, **common}) for i, idea in enumerate(FLIGHTS["ideas"]) if idea["category"] in wanted]
        return found


def by_question_id(answers: dict) -> dict:
    ids = {q["key"]: q["id"] for q in QUESTIONS["questions"]}
    return {ids[k]: v for k, v in answers.items() if v != "SKIPPED"}


def make_room(tmp_path, variant=None):
    clock = FakeClock()
    backend = LocalBackend(tmp_path / "backend.sqlite", clock)
    backend.seed_trip(NAMES, organizer=PERSONAS["organizer"])
    for user in PROFILES["users"]:
        backend.set_profile(user["answers"]["display_name"], by_question_id(user["answers"]))
    research = QAResearch(variant)
    return backend, Worker(backend, QAModel(), research, tmp_path / "checkpoints.sqlite", clock=clock), research


def drain(backend, worker, onboarding_only=True):
    for _ in range(60):
        jobs = [j for j in backend.eligible_jobs() if not (onboarding_only and j.startswith("job-"))]
        if not jobs:
            return
        for job in jobs:
            worker.run_one(job)
    raise AssertionError("jobs kept coming")


def onboard(backend, worker):
    backend.describe_trip(PERSONAS["trip"]["description"])
    drain(backend, worker)
    assert backend.trip_brief()["state"] == "PROPOSED"
    backend.confirm_trip_brief()
    drain(backend, worker)
    used = {n: set() for n in NAMES}
    for _ in range(8):
        for name in NAMES:
            intake = backend.intake_of(name)
            for patch in backend.patches_of(name):
                if patch["state"] == "PENDING" and patch["patch"].get("key") == "origin_airport":
                    backend.confirm_origin(name, patch["id"])
            if intake["state"] == "SUMMARY":
                prefs = backend.brief_of(name).preferences
                backend.confirm_intake(name, [p["id"] for p in backend.patches_of(name) if p["state"] == "PENDING"],
                    {"speak_up_for_me": prefs.speak_up_for_me, "ask_me_first": prefs.ask_me_first, "give_me_time": prefs.give_me_time})
            elif intake["state"] == "ASKING" and backend.conversation(name)[-1]["sender"] == "AGENT":
                spoke = any(m["sender"] == "HUMAN" for m in backend.conversation(name))
                asked = open_topics(intake["coverage"]) if spoke else ["open"]
                reply = next((r for r in traveller(name)["replies"] if r["when"] in asked and r["when"] not in used[name]), None)
                assert reply, f"{name} was asked about {asked}, which their QA message already answers"
                used[name].add(reply["when"])
                backend.say(name, reply["says"])
        drain(backend, worker)
        if all(backend.intake_of(n)["state"] == "CONFIRMED" for n in NAMES):
            return
    raise AssertionError({n: backend.intake_of(n) for n in NAMES})


def no_bag(name, question):
    """Nobody in the QA mentioned a bag: asked just in time, they say they don't need one."""
    return not question.prompt.endswith("Do you need one?")


def live(report, decision_id=None):
    """Proposals awaiting review (of one decision), newest first."""
    found = [(pid, p) for pid, p in report["proposals"].items() if p["state"] == "AWAITING_REVIEW"
             and (decision_id is None or p.get("decision_id") == decision_id)]
    return sorted(found, key=lambda x: x[1]["generation"], reverse=True)


def plan(backend, worker, answer=no_bag, until=None):
    """Run planning until a proposal is waiting (or `until(report)`), or "no plan" is said."""
    events_before = len(backend.shared_report()["events"])
    for _ in range(12):
        drain(backend, worker, onboarding_only=False)
        report = backend.shared_report()
        no_plan = any(e["kind"] == "NO_FEASIBLE_PLAN" for e in report["events"][events_before:])
        if (until(report) if until else live(report)) or no_plan:
            return report
        for name in NAMES:
            for q in backend.owner_questions(name):
                if q.answer is None:
                    backend.answer_question(name, q.id, q.version, answer(name, q), "a-" + q.id)
    raise AssertionError("planning didn't settle")


def latest(report):
    return max(report["proposals"].values(), key=lambda p: p["generation"]) if report["proposals"] else None


def agree(backend, proposal_id):
    for name in NAMES:
        backend.respond(name, proposal_id, "ACCEPT")


def flies_with_share(flight_cents, share_cents, budget_cents):
    return flight_cents + share_cents <= budget_cents


def picks(proposal) -> dict:
    return {c["owner"]: c for c in proposal["terms"]["selected"]}


@pytest.fixture
def qa(tmp_path):
    backend, worker, research = make_room(tmp_path)
    onboard(backend, worker)
    return backend, worker, research


def test_qa_01_each_persona_is_stored_as_they_said_it(qa):
    backend, worker, research = qa
    confirmed = {n: {p["patch"].get("key") or json.dumps(p["patch"]["rule"]["attribute"]): p["patch"]
                     for p in backend.patches_of(n) if p["state"] == "CONFIRMED"} for n in NAMES}
    alex, bella, chris, dana = (confirmed[n] for n in NAMES)
    assert (alex["trip_budget_total"]["value"], alex["trip_budget_total"]["hardness"]) == (120000, "HARD")
    assert (alex["flight_budget_total"]["value"], alex["flight_budget_total"]["hardness"]) == (40000, "SOFT"), "a target, not a cap"
    assert alex["max_stops"]["hardness"] == "SOFT"
    assert (bella["trip_budget_total"]["value"], bella["trip_budget_total"]["hardness"]) == (250000, "HARD")
    assert (bella["flight_budget_total"]["hardness"], bella["max_stops"]["hardness"]) == ("SOFT", "SOFT"), "nonstop is a wish"
    assert backend.brief_of("Bella").preferences.ask_me_first is True
    assert (chris["flight_budget_total"]["value"], chris["flight_budget_total"]["hardness"]) == (60000, "HARD")
    assert chris['"departure_date"']["rule"] == {"category": "FLIGHT", "attribute": "departure_date", "operator": "gte",
                                                 "value": "2026-11-21", "kind": "hard"}
    assert chris['"return_arrival_date"']["rule"]["value"] == "2026-11-24"
    assert dana["trip_note"]["value"] == "Same flight as Alex if possible"
    # Each person is searched on their own dates; only firm statements would filter a search.
    plan(backend, worker)
    routes = {r["owner"]: r for r in research.requests[0]["routes"]}
    assert routes["Chris"]["date"] == "2026-11-21" and routes["Chris"]["return_date"] == "2026-11-24"
    assert routes["Alex"]["date"] == "2026-11-20" and all(r["max_stops"] is None for r in routes.values())


def test_qa_01_a_feasible_flight_plan_keeps_every_hard_limit(qa):
    backend, worker, _ = qa
    report = plan(backend, worker)
    proposal = latest(report)
    assert proposal and proposal["decision_id"] == "flight" and proposal["scope"] == "FLIGHT_BUNDLE", "flights decided first"
    chosen = picks(proposal)
    assert set(chosen) == set(NAMES)
    chris = chosen["Chris"]
    assert chris["departure_date"] >= "2026-11-21" and chris["return_arrival_date"] <= "2026-11-24"
    assert chris["total_cents"] <= 60000 and not chris["flight_identity"].startswith(("HA27", "UA481", "AS1611", "AA77", "DL905", "HA31"))
    # Among what keeps his limits, his own preferences decide (time efficiency first in his ranking):
    # the nonstop that lands earliest on the 21st and takes least time, not the cheapest.
    assert chris["flight_identity"].startswith("HA29"), chris["flight_identity"]
    for flight in chosen.values():
        assert flight["arrival_date"] <= "2026-11-24", "nothing lands after the trip ends"
    # Separate arrivals were allowed: Chris lands a day after the others without breaking the plan.
    assert chosen["Alex"]["arrival_date"] == "2026-11-20" and chris["arrival_date"] == "2026-11-21"


def test_qa_03_a_date_that_misses_the_trip_has_no_plan_and_says_why(qa):
    backend, worker, _ = qa
    plan(backend, worker)
    change = traveller("Chris")["changes"]["QA-03"]
    backend.say("Chris", change["says"])
    drain(backend, worker)
    [pending] = [p for p in backend.patches_of("Chris") if p["state"] == "PENDING"]
    assert pending["patch"]["rule"]["value"] == "2026-11-25"
    assert "Here's the change I heard" in backend.conversation("Chris")[-1]["body"]
    backend.confirm_patch("Chris", pending["id"])
    report = plan(backend, worker)
    assert not any(p["state"] == "AWAITING_REVIEW" for p in report["proposals"].values()), "no plan is fabricated"
    events = [e for e in report["events"] if e["kind"] == "NO_FEASIBLE_PLAN"]
    assert events, "no plan, said plainly"
    assert "Chris" not in json.dumps(events) and "November 25" not in json.dumps(events), "the room hears counts, not whose dates"
    note = backend.conversation("Chris")[-1]["body"]
    assert "I can't depart until November 25 now" in note and "the trip ends Nov 24" in note, note


def test_the_organizers_order_flights_then_the_stay_then_each_activity(qa):
    backend, worker, _ = qa
    [(flight_id, flights)] = live(plan(backend, worker), "flight")
    agree(backend, flight_id)
    [(stay_id, stay)] = live(plan(backend, worker, until=lambda r: live(r, "stay")), "stay")
    assert stay["scope"] == "SINGLE_SHARED_BOOKING" and [c["category"] for c in stay["terms"]["selected"]] == ["STAY"]
    share = stay["terms"]["selected"][0]["total_cents"] / 4
    budgets = {"Alex": 120000, "Bella": 250000, "Chris": 180000, "Dana": 170000}
    for name, flight in picks(flights).items():
        assert flight["total_cents"] + share <= budgets[name], "with the agreed flight, the stay fits every budget"
    agree(backend, stay_id)
    chosen = []
    for n in (1, 2, 3):  # Nov 20-24: three full days, one group activity each
        decision = f"activity:{n}"
        [(activity_id, activity)] = live(plan(backend, worker, until=lambda r, d=decision: live(r, d)), decision)
        assert activity["scope"] == "ACTIVITY_DECISION" and len(activity["terms"]["selected"]) == 1
        chosen.append(activity["terms"]["selected"][0]["title"])
        agree(backend, activity_id)
    assert len(set(chosen)) == 3, "never the same activity twice"
    drain(backend, worker, onboarding_only=False)
    report = backend.shared_report()
    assert not live(report) and any(j["outcome"] == "ALL_DECIDED" for j in report["jobs"])


def test_qa_02_a_lower_budget_replans_only_what_isnt_agreed(qa):
    backend, worker, _ = qa
    [(flight_id, flights)] = live(plan(backend, worker), "flight")
    agree(backend, flight_id)
    [(old_stay_id, _)] = live(plan(backend, worker, until=lambda r: live(r, "stay")), "stay")
    change = PERSONAS["changes"]["QA-02"]
    backend.say("Alex", change["says"])
    drain(backend, worker)
    [pending] = [p for p in backend.patches_of("Alex") if p["state"] == "PENDING"]
    assert (pending["patch"]["key"], pending["patch"]["value"]) == ("trip_budget_total", 90000)
    backend.confirm_patch("Alex", pending["id"])
    report = plan(backend, worker, until=lambda r: live(r, "stay"))
    [(stay_id, stay)] = live(report, "stay")
    proposals = report["proposals"]
    assert stay_id != old_stay_id and proposals[old_stay_id]["state"] == "STALE", "the pending stay was out of date"
    assert proposals[flight_id]["state"] == "AGREED", "the agreed flights still fit $900, so they stand"
    alex_flight = picks(flights)["Alex"]["total_cents"]
    assert alex_flight + stay["terms"]["selected"][0]["total_cents"] / 4 <= 90000


def test_qa_05_what_is_saved_matches_what_is_true_now(qa):
    backend, worker, _ = qa
    [(flight_id, _)] = live(plan(backend, worker), "flight")
    agree(backend, flight_id)
    [(stay_id, _)] = live(plan(backend, worker, until=lambda r: live(r, "stay")), "stay")
    backend.respond("Bella", stay_id, "ACCEPT")  # one approval, then the room changes
    backend.say("Alex", PERSONAS["changes"]["QA-02"]["says"])
    drain(backend, worker)
    backend.confirm_patch("Alex", next(p["id"] for p in backend.patches_of("Alex") if p["state"] == "PENDING"))
    report = plan(backend, worker, until=lambda r: live(r, "stay"))
    proposals = report["proposals"]
    # One live version per decision; old versions don't count; approvals never carry over.
    assert len(live(report, "stay")) == 1 and proposals[stay_id]["state"] == "STALE"
    [(new_stay_id, new_stay)] = live(report, "stay")
    assert not new_stay.get("responses"), "Bella's yes was for the old version"
    # The commitments are exactly the agreed decisions, with their picks.
    agreed = {p["decision_id"]: {c["id"] for c in p["terms"]["selected"]} for p in proposals.values() if p["state"] == "AGREED"}
    assert agreed == {"flight": {c["id"] for c in proposals[flight_id]["terms"]["selected"]}}
    assert {c["owner"] for c in proposals[flight_id]["terms"]["selected"]} == set(NAMES), "one flight each, to its owner"


def test_qa_04_bellas_nonstop_is_weighed_as_a_preference_and_she_is_asked_first(tmp_path):
    backend, worker, research = make_room(tmp_path, variant="bella_pricey_nonstop")
    onboard(backend, worker)
    asked = []

    def answer(name, question):
        asked.append((name, question.kind, question.prompt))
        return no_bag(name, question)
    report = plan(backend, worker, answer=answer)
    [(_, flights)] = live(report, "flight")
    bella = picks(flights)["Bella"]
    # A wish, not a requirement: her connecting flights were searched and weighed.
    [route] = [r for r in research.requests[0]["routes"] if r["owner"] == "Bella"]
    assert route["max_stops"] is None
    # She ranked comfort first and price last: the $1,350 nonstop beats the $430 one-stop for her.
    assert bella["stops"] == 0 and bella["flight_identity"].startswith(("HA1", "AA31"))
    # It costs her far more than her cheapest fare, and she asked to be checked with first.
    bella_asked = [a for a in asked if a[0] == "Bella" and a[1] == "CONSULTATION"]
    assert bella_asked, asked
    assert not any(a[0] != "Bella" and "Bella" in a[2] for a in asked), "nobody else hears about her choice"
