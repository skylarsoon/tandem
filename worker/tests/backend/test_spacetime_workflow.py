"""The real worker (graphs, solver, validator, scripted model/research) running
end to end against the real SpacetimeDB module."""
import json

import pytest

from tandem_worker.contracts import Act, Decision, Preferences
from tandem_worker.fixtures import FixtureResearch, ScriptedModel, scenario_inputs
from tandem_worker.ports import SystemClock
from tandem_worker.runner import Worker

PEOPLE = ("p1", "p2", "p3", "p4")


def make_worker(room, tmp_path, scenario, model=None):
    clock = SystemClock()
    research = FixtureResearch(clock, scenario)
    worker = Worker(room.backend, model or ScriptedModel(scenario), research, tmp_path / "checkpoints.sqlite",
                    clock=clock, limits=room.backend.limits)
    return worker, research


def shared_view(room, name):
    return room.view("p3", name)


@pytest.mark.parametrize("scenario,research_count", [("happy", 1), ("targeted-budget", 2), ("agent-objection", 2)])
def test_worker_publishes_without_consent_then_people_agree(stdb_room, tmp_path, scenario, research_count):
    room = stdb_room(scenario)
    worker, research = make_worker(room, tmp_path, scenario)
    result = worker.run_one(room.plan_job())
    assert result["outcome"] == "AWAITING_REVIEW", result
    assert len(research.requests) == research_count

    proposals = shared_view(room, "room_proposals")
    assert [(p["state"], p["reviewer_count"]) for p in proposals] == [("AWAITING_REVIEW", 4)]
    counts = shared_view(room, "room_consent_counts")[0]
    assert (counts["accepted"], counts["pending"]) == (0, 4), "the worker never approves for anyone"

    # The room sees the agents' conversation, never anyone's private reason.
    chat = shared_view(room, "room_messages")
    assert chat and all(m["sanitization_status"] in {"VALIDATED", "TEMPLATE_FALLBACK"} for m in chat)
    assert "PRIVATE_CANARY" not in json.dumps(chat + proposals)
    if scenario == "agent-objection":
        agents = {m["agent_id"]: m["display_name"] for m in shared_view(room, "room_members")}
        assert any(m["type"] == "COUNTERPROPOSE" and agents.get(m["sender_agent_id"]) == "p4" for m in chat)
        assert "p2-targeted" in proposals[0]["terms_json"]

    proposal = proposals[0]
    for owner in PEOPLE:
        room.backend.http.call(room.tokens[owner], "respond_to_proposal", proposal_id=proposal["id"],
                               version=proposal["version"], terms_digest=proposal["terms_digest"], response="ACCEPT", note=None)
    assert shared_view(room, "room_proposals")[0]["state"] == "AGREED"


def test_clarification_parks_then_resumes_after_owner_answers(stdb_room, tmp_path):
    room = stdb_room("clarification-restart")
    worker, _ = make_worker(room, tmp_path, "clarification-restart")
    job = room.plan_job()
    waiting = worker.run_one(job)
    assert waiting["outcome"] == "WAITING_FOR_INPUT"
    assert room.backend.eligible_jobs() == [], "a waiting job holds no lease and isn't claimable"
    question = room.view("p1", "my_questions")[0]
    assert question["kind"] == "MISSING_FACT" and "PRIVATE_CANARY" not in question["prompt"]
    room.backend.answer_question("p1", question["request_key"], question["version"], True, "answer-1")
    assert room.backend.eligible_jobs() == [job], "answering resumes the same generation"
    # A fresh worker process resumes from the same LangGraph checkpoint.
    fresh, _ = make_worker(room, tmp_path, "clarification-restart")
    assert fresh.reconcile()[0]["outcome"] == "AWAITING_REVIEW"


def test_unconfirmed_preferences_wait_before_any_visible_message(stdb_room, tmp_path):
    _, briefs = scenario_inputs("happy")
    briefs[0] = briefs[0].model_copy(update={"preferences": Preferences()})
    room = stdb_room(briefs=briefs)
    worker, _ = make_worker(room, tmp_path, "happy")
    assert worker.run_one(room.plan_job())["outcome"] == "WAITING_FOR_INPUT"
    assert shared_view(room, "room_messages") == []
    question = room.view("p1", "my_questions")[0]
    assert question["kind"] == "PARTICIPATION_CONFIRMATION"
    room.backend.answer_question("p1", question["request_key"], question["version"], True, "confirm")
    assert worker.reconcile()[0]["outcome"] == "AWAITING_REVIEW"


class ObjectingModel(ScriptedModel):
    def decide(self, context):
        if context["owner"] == "p1":
            return Decision(act=Act.OBJECT, evaluation="INELIGIBLE", blocker="HARD_LIMIT")
        return super().decide(context)


def test_private_refusal_keeps_blocker_without_public_objection(stdb_room, tmp_path):
    _, briefs = scenario_inputs("happy")
    briefs[0] = briefs[0].model_copy(update={"preferences": Preferences(confirmed=True)})
    room = stdb_room(briefs=briefs)
    worker, _ = make_worker(room, tmp_path, "happy", model=ObjectingModel())
    assert worker.run_one(room.plan_job())["outcome"] == "WAITING_FOR_INPUT"
    assert not any(m["type"] == "OBJECT" for m in shared_view(room, "room_messages"))
    question = room.view("p1", "my_questions")[0]
    assert question["kind"] == "OBJECTION_DISCLOSURE"
    room.backend.answer_question("p1", question["request_key"], question["version"], False, "decline")
    assert worker.reconcile()[0]["outcome"] == "OWNER_DECLINED"
    assert shared_view(room, "room_proposals") == []


def test_granted_disclosure_restarts_planning_and_objection_goes_public(stdb_room, tmp_path):
    _, briefs = scenario_inputs("happy")
    briefs[0] = briefs[0].model_copy(update={"preferences": Preferences(confirmed=True)})
    room = stdb_room(briefs=briefs)
    worker, _ = make_worker(room, tmp_path, "happy", model=ObjectingModel())
    first_job = room.plan_job()
    assert worker.run_one(first_job)["outcome"] == "WAITING_FOR_INPUT"
    question = room.view("p1", "my_questions")[0]
    room.backend.answer_question("p1", question["request_key"], question["version"], True, "grant")
    jobs = room.backend.eligible_jobs()
    assert jobs and first_job not in jobs, "a granted permission starts a new generation"
    results = worker.reconcile()
    # p1 still can't accept the option, but the objection may now be voiced.
    assert results[0]["outcome"] in {"UNRESOLVED_AGENT", "NO_PROGRESS"}
    assert any(m["type"] == "OBJECT" for m in shared_view(room, "room_messages"))
    assert shared_view(room, "room_proposals") == []


def answer_mine(room, owner, granted):
    for q in room.view(owner, "my_questions"):
        if q["state"] == "OPEN":
            room.backend.answer_question(owner, q["request_key"], q["version"], granted, "answer-" + q["request_key"])


def impact_of(room, owner):
    [row] = room.view(owner, "my_proposal_impacts")
    return json.loads(row["impact_json"])


def test_concession_is_consulted_then_explained_privately(stdb_room, tmp_path):
    room = stdb_room("group-arrival")
    worker, _ = make_worker(room, tmp_path, "group-arrival")
    assert worker.run_one(room.plan_job())["outcome"] == "WAITING_FOR_INPUT"
    [question] = room.view("p4", "my_questions")
    assert question["kind"] == "CONCESSION_CONSULTATION"
    assert question["prompt"].startswith("Your flight costs $525.00: $125.00 more than your cheapest valid fare")
    assert all(room.view(o, "my_questions") == [] for o in ("p1", "p2", "p3"))

    answer_mine(room, "p4", True)
    assert worker.reconcile()[-1]["outcome"] == "AWAITING_REVIEW"
    [proposal] = shared_view(room, "room_proposals")
    summary = json.loads(proposal["shared_summary_json"])
    assert "Landing together costs $125.00 more than everyone's cheapest fares" in summary["text"]
    assert summary["group"]["travellers_paying_extra"] == 1
    p4 = impact_of(room, "p4")
    assert (p4["extra_cents"], p4["reason"]) == (12500, "ARRIVAL_WINDOW") and "$125.00 more" in p4["text"]
    assert impact_of(room, "p1")["extra_cents"] == 0
    chat = shared_view(room, "room_messages")
    # Field-level citations resolve server-side (`<candidate>#price`).
    assert any(ref.endswith("#price") for m in chat for ref in m["claim_refs"])
    assert "your cheapest valid fare" not in json.dumps(chat + [proposal])


def test_declined_concession_shifts_the_trade_off(stdb_room, tmp_path):
    room = stdb_room("concession-declined")
    worker, _ = make_worker(room, tmp_path, "concession-declined")
    assert worker.run_one(room.plan_job())["outcome"] == "WAITING_FOR_INPUT"
    answer_mine(room, "p4", False)
    assert worker.reconcile()[-1]["outcome"] == "AWAITING_REVIEW"
    [proposal] = shared_view(room, "room_proposals")
    assert all(f"{o}-late" in proposal["terms_json"] for o in ("p1", "p2", "p3")) and "p4-redeye" in proposal["terms_json"]
    assert [impact_of(room, o)["extra_cents"] for o in PEOPLE] == [15000, 15000, 15000, 0]


def test_budget_blocked_room_gets_a_neutral_event_and_the_owner_a_private_note(stdb_room, tmp_path):
    room = stdb_room("budget-blocked")
    worker, _ = make_worker(room, tmp_path, "budget-blocked")
    assert worker.run_one(room.plan_job())["outcome"] == "NO_FEASIBLE_CANDIDATE"
    [event] = [e for e in shared_view(room, "room_events") if e["type"] == "NO_FEASIBLE_PLAN"]
    payload = json.loads(event["safe_payload_json"])
    assert payload["blocked_travellers"] == {"HARD_LIMIT": 1} and "250" not in event["safe_payload_json"]
    notes = [m["body"] for m in room.view("p1", "my_private_messages") if m["sender"] == "AGENT"]
    assert notes == ["No plan fits yet: the cheapest complete fare found for you is $290.00, above your $250.00 flight "
                     "limit. Raising your limit to at least $290.00 would unblock the plan."]
    assert [m for m in room.view("p2", "my_private_messages") if m["sender"] == "AGENT"] == []


def test_trip_plan_publishes_shared_stay_and_transfer_with_server_checks(stdb_room, tmp_path):
    room = stdb_room("trip-happy")
    worker, _ = make_worker(room, tmp_path, "trip-happy")
    assert worker.run_one(room.plan_job())["outcome"] == "AWAITING_REVIEW"
    [proposal] = shared_view(room, "room_proposals")
    terms = json.loads(proposal["terms_json"])
    assert terms["scope"] == "TRIP"
    assert sorted(c["category"] for c in terms["selected"]) == ["FLIGHT"] * 4 + ["STAY", "TRANSFER"]
    summary = json.loads(proposal["shared_summary_json"])
    assert "Shared costs are split equally: $330.00 each." in summary["text"]
    assert [s["category"] for s in summary["suggestions"]] == ["ACTIVITY", "ACTIVITY", "DINNER"]
    assert impact_of(room, "p4")["share_cents"] == 33000
    categories = {c["category"] for c in shared_view(room, "room_candidates")}
    assert categories == {"FLIGHT", "STAY", "TRANSFER", "ACTIVITY", "DINNER"}


def test_room_request_clash_is_negotiated_on_spacetimedb(stdb_room, tmp_path):
    room = stdb_room("own-room-squeeze")
    worker, _ = make_worker(room, tmp_path, "own-room-squeeze")
    assert worker.run_one(room.plan_job())["outcome"] == "WAITING_FOR_INPUT"
    for owner in ("p1", "p2"):
        [question] = room.view(owner, "my_questions")
        assert question["kind"] == "MISSING_FACT" and question["prompt"].startswith("Your own-room request")
    result = None
    for _ in range(4):
        for owner in ("p1", "p2"):
            answer_mine(room, owner, True)
        result = worker.reconcile()[-1]
        if result["outcome"] != "WAITING_FOR_INPUT":
            break
    assert result["outcome"] == "AWAITING_REVIEW"
    [proposal] = [p for p in shared_view(room, "room_proposals") if p["state"] == "AWAITING_REVIEW"]
    assert "stay-condo" in proposal["terms_json"]
    assert impact_of(room, "p4")["price_cents"] == 73000


def test_trip_budget_from_the_database_blocks_privately(stdb_room, tmp_path):
    room = stdb_room("trip-happy")
    room.backend.http.call(room.tokens["p1"], "set_constraint", room_id=room.room_id, key="trip_budget_total",
                           value_json="60000", unit="USD_cents", hardness="HARD", authorized_range_json=None, private_rationale=None)
    worker, _ = make_worker(room, tmp_path, "trip-happy")
    assert worker.run_one(room.plan_job())["outcome"] == "NO_FEASIBLE_CANDIDATE"
    notes = [m["body"] for m in room.view("p1", "my_private_messages") if m["sender"] == "AGENT"]
    assert notes == ["No plan fits yet: the cheapest trip found for you is $630.00 (your flight plus an equal share of the "
                     "stay and transfer), above your $600.00 trip limit. Raising your limit to at least $630.00 would "
                     "unblock the plan."]
    [event] = [e for e in shared_view(room, "room_events") if e["type"] == "NO_FEASIBLE_PLAN"]
    assert "600" not in event["safe_payload_json"] and json.loads(event["safe_payload_json"])["blocked_travellers"] == {"TRIP_LIMIT": 1}


def test_optimizer_burdens_reach_the_ledger_only_after_everyone_agrees(stdb_room, tmp_path):
    room = stdb_room("happy")
    publish = room.backend.publish_proposal
    room.backend.publish_proposal = lambda *a, **kw: publish(*a, **kw, burdens={"p2": 0.4})
    worker, _ = make_worker(room, tmp_path, "happy")
    assert worker.run_one(room.plan_job())["outcome"] == "AWAITING_REVIEW"
    assert room.view("p2", "my_compromises") == [], "a published suggestion records nothing"

    proposal = shared_view(room, "room_proposals")[0]
    for owner in PEOPLE:
        room.backend.http.call(room.tokens[owner], "respond_to_proposal", proposal_id=proposal["id"],
                               version=proposal["version"], terms_digest=proposal["terms_digest"], response="ACCEPT", note=None)
    [entry] = room.view("p2", "my_compromises")
    assert (entry["decision_id"], entry["burden_micros"], entry["state"]) == ("trip", 400_000, "ACTIVE")
    assert room.view("p1", "my_compromises") == []
