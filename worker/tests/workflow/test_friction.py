"""Social friction: who pays to keep the group together, what happens when
someone says no, and how a plan that can't exist is explained without
exposing anyone."""
import json

import pytest

from tandem_worker.contracts import Payload
from tandem_worker.explain import room_conflict_event
from tandem_worker.contracts import Conflict


def answer_all(backend, owner, answer):
    for q in backend.owner_questions(owner):
        if q.answer is None:
            backend.answer_question(owner, q.id, q.version, answer, "answer:" + q.id)


def selected(backend):
    [proposal] = backend.shared_report()["proposals"].values()
    return sorted(c["id"].rsplit("-", 2)[0] for c in proposal["terms"]["selected"])


def assert_room_safe(backend):
    shared = json.dumps(backend.shared_report())
    assert "PRIVATE_CANARY" not in shared
    assert "your cheapest valid fare" not in shared, "personal trade-offs stay private"
    assert "limit" not in shared.replace("a private limit", ""), "caps are never stated to the room"


def test_paying_more_to_land_together_is_asked_first_and_explained_privately(setup_worker):
    backend, worker, job, *_ = setup_worker("group-arrival")
    assert worker.run_one(job)["outcome"] == "WAITING_FOR_INPUT"
    [question] = backend.owner_questions("p4")
    assert question.kind == "CONSULTATION"
    assert question.prompt == ("Your flight costs $525.00: $125.00 more than your cheapest valid fare ($400.00), "
                               "so everyone lands within 2h of each other. May your agent accept this on your behalf?")
    assert not any(m["sender_agent_id"] == "p4" for m in backend.shared_report()["messages"]), "no reply before consent"
    for owner in ("p1", "p2", "p3"):
        assert backend.owner_questions(owner) == []

    answer_all(backend, "p4", True)
    assert worker.reconcile()[0]["outcome"] == "AWAITING_REVIEW"
    assert selected(backend) == ["p1-noon", "p2-noon", "p3-noon", "p4-noon"]
    report = backend.shared_report()
    [proposal] = report["proposals"].values()
    assert proposal["summary"].startswith("Total $1,495.00. Everyone lands between 12:26 and 12:56 local time")
    assert "costs $125.00 more than everyone's cheapest fares, which would land 5h apart" in proposal["summary"]
    [reply] = [m for m in report["messages"] if m["sender_agent_id"] == "p4"]
    assert Payload.model_validate(reply["payload"]).concession is True
    assert "$525.00" in reply["utterance"] and "125" not in reply["utterance"]
    [p4_note] = backend.owner_view("p4")["impacts"].values()
    assert (p4_note["impact"]["extra_cents"], p4_note["impact"]["reason"]) == (12500, "ARRIVAL_WINDOW")
    assert [n["text"] for n in backend.owner_view("p1")["impacts"].values()] == ["Your flight costs $300.00, your cheapest valid fare."]
    assert_room_safe(backend)


def test_declining_the_concession_moves_the_trade_off_to_the_others(setup_worker):
    backend, worker, job, *_ = setup_worker("concession-declined")
    assert worker.run_one(job)["outcome"] == "WAITING_FOR_INPUT"
    answer_all(backend, "p4", False)
    assert worker.reconcile()[0]["outcome"] == "AWAITING_REVIEW"
    # p4 flies their cheapest fare; the other three each land later for $150 more,
    # which beats asking any one person to pay more than that.
    assert selected(backend) == ["p1-late", "p2-late", "p3-late", "p4-redeye"]
    extras = {o: next(iter(backend.owner_view(o)["impacts"].values()))["impact"]["extra_cents"] for o in ("p1", "p2", "p3", "p4")}
    assert extras == {"p1": 15000, "p2": 15000, "p3": 15000, "p4": 0}
    [proposal] = backend.shared_report()["proposals"].values()
    assert "The extra cost falls on 3 travellers" in proposal["summary"]
    assert_room_safe(backend)


def test_declined_concession_with_no_alternative_explains_the_arrival_gap(setup_worker):
    # group-arrival has no later fares for the others: once p4 says no, nothing fits.
    backend, worker, job, *_ = setup_worker("group-arrival")
    worker.run_one(job)
    answer_all(backend, "p4", False)
    assert worker.reconcile()[0]["outcome"] == "NO_FEASIBLE_CANDIDATE"
    [event] = backend.shared_report()["events"]
    assert event["payload"]["closest_spread_seconds"] == 5 * 3600
    assert "No combination of flights lands everyone within 2h; the closest is 5h apart." in event["payload"]["text"]
    assert backend.shared_report()["proposals"] == {}
    assert_room_safe(backend)


def test_budget_below_every_fare_gets_a_private_note_and_a_neutral_room_event(setup_worker):
    backend, worker, job, model, research, _ = setup_worker("budget-blocked")
    assert worker.run_one(job)["outcome"] == "NO_FEASIBLE_CANDIDATE"
    # Targeted searches go only to the blocked traveller, with the cause.
    assert [(r["purpose"], r["target_owners"], r["blocker_code"]) for r in research.requests] == [
        ("INITIAL", ["p1", "p2", "p3", "p4"], "NONE"), ("TARGETED", ["p1"], "HARD_LIMIT"), ("TARGETED", ["p1"], "HARD_LIMIT")]
    assert backend.owner_view("p1")["notes"] == [
        "No plan fits yet: the cheapest complete fare found for you is $290.00, above your $250.00 flight limit. "
        "Raising your limit to at least $290.00 would unblock the plan."]
    for owner in ("p2", "p3", "p4"):
        assert backend.owner_view(owner)["notes"] == []
    [event] = backend.shared_report()["events"]
    assert event["kind"] == "NO_FEASIBLE_PLAN"
    assert event["payload"]["blocked_travellers"] == {"HARD_LIMIT": 1}
    assert "p1" not in json.dumps(event) and "250" not in json.dumps(event) and "290" not in json.dumps(event)
    assert_room_safe(backend)


def test_impossible_arrival_window_reports_the_closest_spread(setup_worker):
    backend, worker, job, model, research, _ = setup_worker("arrival-impossible")
    assert worker.run_one(job)["outcome"] == "NO_FEASIBLE_CANDIDATE"
    assert research.requests[1]["blocker_code"] == "ARRIVAL_OVERLAP"
    [event] = backend.shared_report()["events"]
    assert (event["payload"]["closest_spread_seconds"], event["payload"]["required_seconds"]) == (3.5 * 3600, 7200)
    assert all(backend.owner_view(o)["notes"] == [] for o in ("p1", "p2", "p3", "p4"))


@pytest.mark.parametrize("scenario,answer", [("group-arrival", True), ("concession-declined", False), ("budget-blocked", None)])
def test_private_impacts_never_reach_coordinator_checkpoints(setup_worker, scenario, answer):
    backend, worker, job, *_ = setup_worker(scenario)
    worker.run_one(job)
    if answer is not None:
        answer_all(backend, "p4", answer)
        worker.reconcile()
    from langgraph.checkpoint.sqlite import SqliteSaver
    with SqliteSaver.from_conn_string(worker.checkpoint_path) as saver:
        for checkpoint in saver.list(None):
            text = json.dumps(checkpoint.checkpoint, default=str)
            assert "PRIVATE_CANARY" not in text and "extra_cents" not in text and "hard_cap" not in text


def test_room_conflict_text_counts_causes_without_names():
    payload = room_conflict_event((Conflict(code="HARD_LIMIT", owner="alice", cheapest_cents=29200),
                                   Conflict(code="HARD_LIMIT", owner="carol", cheapest_cents=43200),
                                   Conflict(code="BAGGAGE_UNAVAILABLE", owner="bob")))
    assert payload["text"] == ("No plan fits yet. 1 traveller: no fare found confirms a required checked bag. "
                               "2 travellers: every fare found is above a private limit. "
                               "Each affected traveller has a private note from their agent.")
    assert not {"alice", "bob", "carol", "292"} & set(json.dumps(payload).replace(".", " ").split())


# ---------- whole trips: shared stay and transfer, split equally ----------

def settle(backend, worker, answers, rounds=4):
    """Answer each owner's open questions until the run stops waiting."""
    result = None
    for _ in range(rounds):
        for owner, answer in answers.items():
            answer_all(backend, owner, answer)
        result = worker.reconcile()[-1]
        if result["outcome"] != "WAITING_FOR_INPUT":
            return result
    return result


def test_trip_plan_splits_the_stay_and_transfer_and_explains_each_share(setup_worker):
    backend, worker, job, model, research, _ = setup_worker("trip-happy")
    assert worker.run_one(job)["outcome"] == "AWAITING_REVIEW"
    assert research.requests[0]["categories"] == ["FLIGHT", "STAY", "TRANSFER", "ACTIVITY", "DINNER"]
    assert selected(backend) == ["p1-noon", "p2-noon", "p3-noon", "p4-noon", "stay-condo", "transfer-van"]
    [proposal] = backend.shared_report()["proposals"].values()
    assert "Stay: Condo, $1,200.00 in total. Airport transfer: Airport van, $120.00." in proposal["summary"]
    assert "Shared costs are split equally: $330.00 each." in proposal["summary"]
    assert "Things to do: Sunrise snorkel tour (about $89.00 per person); Diamond Head hike." in proposal["summary"]
    [note] = backend.owner_view("p4")["impacts"].values()
    assert note["text"] == ("Your trip costs $730.00: your $400.00 flight plus a $330.00 equal share of the stay and transfer. "
                            "That's the cheapest trip available to you.")
    first = backend.shared_report()["messages"][0]
    assert first["type"] == "PROPOSE" and "The stay costs $1,200.00 in total, taxes included." in first["utterance"]
    assert any(ref.endswith("#rooms") for ref in first["claim_refs"])
    assert_room_safe(backend)


def test_own_room_requests_raise_the_stay_for_everyone_and_say_so_without_names(setup_worker):
    backend, worker, job, *_ = setup_worker("own-room")
    assert worker.run_one(job)["outcome"] == "AWAITING_REVIEW"
    assert "stay-beach-house" in selected(backend)
    [proposal] = backend.shared_report()["proposals"].values()
    assert "The group's room needs add $400.00 over the cheapest place that sleeps everyone." in proposal["summary"]
    assert not {"p1", "p2"} & set(proposal["summary"].replace(".", " ").split())
    assert_room_safe(backend)


def test_room_request_that_breaks_someones_limit_is_negotiated_privately(setup_worker):
    backend, worker, job, *_ = setup_worker("own-room-squeeze")
    assert worker.run_one(job)["outcome"] == "WAITING_FOR_INPUT"
    for owner in ("p1", "p2"):
        [question] = backend.owner_questions(owner)
        assert question.kind == "CONSULTATION" and question.action_ref == "relax-bedrooms-r1"
        assert question.prompt == ("Your own-room request: the stay that fits it costs enough to put the trip over another "
                                   "traveller's limit. May your agent drop it for this trip so you share a room? "
                                   "You'll still review the final plan.")
    assert backend.owner_questions("p4") == [] and backend.shared_report()["messages"] == []
    # Both agree (each yes restarts planning, which may ask again): the condo fits everyone.
    assert settle(backend, worker, {"p1": True, "p2": True})["outcome"] == "AWAITING_REVIEW"
    assert "stay-condo" in selected(backend)
    [note] = backend.owner_view("p4")["impacts"].values()
    assert note["impact"]["price_cents"] == 73000, "within p4's $750 trip limit"
    assert_room_safe(backend)


def test_refused_room_request_explains_the_clash_to_both_sides_only(setup_worker):
    backend, worker, job, *_ = setup_worker("own-room-squeeze")
    worker.run_one(job)
    assert settle(backend, worker, {"p1": False, "p2": False})["outcome"] == "NO_FEASIBLE_CANDIDATE"
    assert backend.owner_view("p4")["notes"] == [
        "No plan fits yet: the cheapest trip found for you is $830.00 (your flight plus an equal share of the stay and "
        "transfer), above your $750.00 trip limit. Raising your limit to at least $830.00 would unblock the plan. "
        "The group's room requirements raise the stay; your agent has asked privately whether they can be relaxed."]
    for owner in ("p1", "p2"):
        assert backend.owner_view(owner)["notes"] == [
            "No plan fits yet: the stay that meets your own-room request costs enough that the trip goes over another "
            "traveller's limit. Sharing a room would unblock the plan."]
    assert backend.owner_view("p3")["notes"] == []
    [event] = backend.shared_report()["events"]
    assert event["payload"]["text"] == ("No plan fits yet. 1 traveller: the cheapest trip found is above a private limit. "
                                        "Each affected traveller has a private note from their agent.")
    assert_room_safe(backend)


@pytest.mark.parametrize("answer,outcome", [(True, "AWAITING_REVIEW"), (False, "NO_FEASIBLE_CANDIDATE")])
def test_unmeetable_rating_asks_only_the_person_who_set_it(setup_worker, answer, outcome):
    backend, worker, job, model, research, _ = setup_worker("stay-rating")
    assert worker.run_one(job)["outcome"] == "WAITING_FOR_INPUT"
    [question] = backend.owner_questions("p3")
    assert question.prompt.startswith("Your minimum stay rating of 4.95: no stay found meets it.")
    assert all(backend.owner_questions(o) == [] for o in ("p1", "p2", "p4"))
    assert settle(backend, worker, {"p3": answer})["outcome"] == outcome
    if answer:
        assert "stay-condo" in selected(backend)
    else:
        assert backend.owner_view("p3")["notes"] == [
            "No stay found meets your minimum rating of 4.95. Lowering it, or another search, would unblock the plan."]
        assert any(r["categories"] == ["STAY"] for r in research.requests[1:]), "targeted searches look for other stays"
        [event] = backend.shared_report()["events"]
        assert "No stay found meets the group's minimum rating." in event["payload"]["text"]
    assert_room_safe(backend)
