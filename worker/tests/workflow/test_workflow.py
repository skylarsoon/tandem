import json

import pytest

from tandem_worker.contracts import Act, ContractError, Decision, Limits, Preferences, SolverResult, VoiceDraft, VoiceSegment
from tandem_worker.fixtures import ScriptedModel, scenario_inputs
from tandem_worker.solver import FlightValidator


@pytest.mark.parametrize("scenario,research_count", [("happy", 1), ("targeted-budget", 2), ("agent-objection", 2)])
def test_real_solver_and_scoped_dialogue_publish_without_consent(setup_worker, scenario, research_count):
    backend, worker, job, model, research, clock = setup_worker(scenario)
    assert worker.run_one(job)["outcome"] == "AWAITING_REVIEW"
    report = backend.shared_report()
    assert len(report["proposals"]) == 1
    assert report["consent_count"] == 0
    assert len(research.requests) == research_count
    assert research.requests[0]["purpose"] == "INITIAL"
    assert "PRIVATE_CANARY" not in json.dumps(report)
    assert "hard_cap" not in json.dumps(model.voices)
    assert "private_reason" not in json.dumps(model.voices)
    for context in model.decisions:
        assert context["private_reason"] == f"PRIVATE_CANARY_{context['owner']}"
    if scenario == "agent-objection":
        cause = research.requests[1]["blocker_message_ref"]
        message = next(m for m in report["messages"] if m["message_id"] == cause)
        assert message["sender_agent_id"] == "p4"
        assert message["recipient_agent_id"] == "p2"
        assert message["type"] == "COUNTERPROPOSE"
        recipient_context = next(c for c in model.decisions if c["owner"] == "p4" and c["inbound_type"] == "CLARIFY")
        assert recipient_context["inbound_payload"]["blocker"] == "BAGGAGE_UNKNOWN"
        assert recipient_context["inbound_evidence"][0]["owner"] == "p2"
        assert any("p2-targeted" == c["id"] for p in report["proposals"].values() for c in p["terms"]["selected"])


def test_private_canaries_absent_from_coordinator_checkpoints(setup_worker):
    backend, worker, job, *_ = setup_worker()
    worker.run_one(job)
    from langgraph.checkpoint.sqlite import SqliteSaver
    with SqliteSaver.from_conn_string(worker.checkpoint_path) as saver:
        for checkpoint in saver.list(None):
            assert "PRIVATE_CANARY" not in json.dumps(checkpoint.checkpoint, default=str)


def test_unconfirmed_preferences_wait_before_any_visible_message(setup_worker):
    _, briefs = scenario_inputs("happy")
    briefs[0] = briefs[0].model_copy(update={"preferences": Preferences()})
    backend, worker, job, *_ = setup_worker(briefs=briefs)
    assert worker.run_one(job)["outcome"] == "WAITING_FOR_INPUT"
    assert backend.shared_report()["messages"] == []
    q = backend.owner_questions("p1")[0]
    assert q.kind == "PARTICIPATION"
    backend.answer_question("p1", q.id, 1, True, "confirm")
    assert worker.reconcile()[0]["outcome"] == "AWAITING_REVIEW"


class ObjectingModel(ScriptedModel):
    def decide(self, context):
        if context["owner"] == "p1":
            return Decision(act=Act.OBJECT, evaluation="INELIGIBLE", blocker="HARD_LIMIT")
        return super().decide(context)


def test_private_refusal_keeps_blocker_without_public_objection(setup_worker):
    _, briefs = scenario_inputs("happy")
    briefs[0] = briefs[0].model_copy(update={"preferences": Preferences(confirmed=True)})
    backend, worker, job, *_ = setup_worker(model=ObjectingModel(), briefs=briefs)
    assert worker.run_one(job)["outcome"] == "WAITING_FOR_INPUT"
    assert not any(m["type"] == "OBJECT" for m in backend.shared_report()["messages"])
    q = backend.owner_questions("p1")[0]
    backend.answer_question("p1", q.id, 1, False, "decline")
    assert worker.reconcile()[0]["outcome"] == "OWNER_DECLINED"
    assert backend.shared_report()["proposals"] == {}


class InvalidVoice(ScriptedModel):
    def voice(self, context):
        self.voices.append(context)
        return VoiceDraft(segments=(VoiceSegment(fact_ref=next(iter(context["facts"])), text="This is booked for $1."),))


def test_bad_wording_regenerates_once_then_templates(setup_worker):
    backend, worker, job, model, *_ = setup_worker(model=InvalidVoice())
    assert worker.run_one(job)["outcome"] == "AWAITING_REVIEW"
    assert len(model.voices) == 8
    for message in backend.shared_report()["messages"]:
        assert "booked" not in message["utterance"]
        assert message["worker_validation"]["status"] == "TEMPLATE_FALLBACK"


def test_revocation_during_voice_prevents_delivery(setup_worker):
    backend, worker, job, model, *_ = setup_worker()
    original = model.voice
    def revoke(context):
        backend.update_preferences("p1", Preferences(confirmed=True))
        return original(context)
    model.voice = revoke
    assert worker.run_one(job)["outcome"] in {"LEASE_LOST", "STALE_SNAPSHOT"}
    assert not any(m["sender_agent_id"] == "p1" for m in backend.shared_report()["messages"])
    assert not backend.shared_report()["proposals"]


class RejectValidator(FlightValidator):
    def validate(self, *args):
        return super().validate(*args).model_copy(update={"valid": False, "checks": ("TEST_REJECTION",)})


def test_validation_is_independent_of_agent_eligibility(setup_worker):
    backend, worker, job, *_ = setup_worker(validator=RejectValidator())
    assert worker.run_one(job)["outcome"] == "VALIDATION_FAILED"
    assert not backend.shared_report()["proposals"]


@pytest.mark.parametrize("status", ["UNKNOWN", "MODEL_INVALID"])
def test_solver_status_is_preserved(setup_worker, status):
    class Solver:
        def solve(self, *args):
            return SolverResult(status=status)
    backend, worker, job, *_ = setup_worker(solver=Solver())
    assert worker.run_one(job)["outcome"] == status
    assert not backend.shared_report()["proposals"]


def test_turn_budget_pauses_without_fabricated_agreement(setup_worker):
    backend, worker, job, *_ = setup_worker(limits=Limits(round_turns=2))
    assert worker.run_one(job)["outcome"] == "TURN_BUDGET_EXHAUSTED"
    assert len(backend.shared_report()["messages"]) == 2
    assert not backend.shared_report()["proposals"]


def test_call_budget_is_persisted(setup_worker):
    backend, worker, job, *_ = setup_worker(limits=Limits(model_calls=1))
    assert worker.run_one(job)["outcome"] == "CALL_BUDGET_EXHAUSTED"
    assert backend.shared_report()["jobs"][0]["calls"]["model"] == 1


def test_disclosure_grant_is_action_specific_and_not_consent(setup_worker):
    _, briefs = scenario_inputs("happy")
    briefs[0] = briefs[0].model_copy(update={"preferences": Preferences(confirmed=True)})
    backend, worker, job, *_ = setup_worker(model=ObjectingModel(), briefs=briefs)
    assert worker.run_one(job)["outcome"] == "WAITING_FOR_INPUT"
    q = backend.owner_questions("p1")[0]
    backend.answer_question("p1", q.id, q.version, True, "allow-neutral-objection")
    assert worker.reconcile()[0]["outcome"] == "UNRESOLVED_AGENT"
    assert any(m["type"] == "OBJECT" for m in backend.shared_report()["messages"])
    assert backend.shared_report()["consent_count"] == 0


def test_ask_first_consultation_resumes_under_new_authority(setup_worker):
    class ConcessionModel(ScriptedModel):
        def decide(self, context):
            return Decision(concession=context["owner"] == "p1")
    backend, worker, job, *_ = setup_worker(model=ConcessionModel())
    assert worker.run_one(job)["outcome"] == "WAITING_FOR_INPUT"
    q = backend.owner_questions("p1")[0]
    assert q.kind == "CONSULTATION"
    backend.answer_question("p1", q.id, 1, True, "allow-exploration")
    assert worker.reconcile()[0]["outcome"] == "AWAITING_REVIEW"
    assert backend.shared_report()["consent_count"] == 0


def test_repeating_polite_dialogue_stops_without_research_or_approval(setup_worker):
    class LoopModel(ScriptedModel):
        def decide(self, context):
            return Decision(act=Act.CLARIFY, evaluation="INCOMPLETE_EVIDENCE", recipient="p2" if context["owner"] == "p1" else "p1")
    backend, worker, job, *_ = setup_worker(model=LoopModel())
    assert worker.run_one(job)["outcome"] == "NO_PROGRESS"
    assert not backend.shared_report()["proposals"]
