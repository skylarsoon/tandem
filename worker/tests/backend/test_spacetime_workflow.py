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
