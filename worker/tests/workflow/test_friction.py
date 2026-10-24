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
