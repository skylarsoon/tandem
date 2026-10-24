"""C04: the worker's contract invariants (tests/contract) against the real
SpacetimeDB module through SpacetimeBackend."""
import pytest

from tandem_worker.contracts import Act, ContractError, Payload
from tandem_worker.graphs.personal import question_for, send_message


class Recorder:
    """Records submitted messages so a test can resubmit altered copies."""
    def __init__(self, backend):
        self.backend, self.messages = backend, []

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def submit_agent_message(self, claim, message, participants):
        self.messages.append(message)
        return self.backend.submit_agent_message(claim, message, participants)


def claimed(stdb_room, scenario="happy"):
    room = stdb_room(scenario)
    claim = room.backend.claim_job(room.plan_job(), "w", "x")
    snapshot = room.backend.load_execution_context(claim).model_dump(mode="json")
    return room, claim, snapshot


def test_claim_idempotency_and_conflicting_body(stdb_room):
    room = stdb_room()
    other = stdb_room()
    job = room.plan_job()
    claim = room.backend.claim_job(job, "w", "same")
    assert room.backend.claim_job(job, "w", "same") == claim
    with pytest.raises(ContractError, match="KEY_BODY_MISMATCH"):
        room.backend.claim_job(other.plan_job(), "w", "same")


def test_no_private_read_for_unknown_owner(stdb_room):
    room, claim, _ = claimed(stdb_room)
    with pytest.raises(ContractError, match="NOT_AUTHORIZED"):
        room.backend.read_brief(claim, "outsider")
    brief = room.backend.read_brief(claim, "p1")
    assert (brief.hard_cap_cents, brief.private_reason) == (50_000, "PRIVATE_CANARY_p1")


def test_wrong_owner_or_stale_question_cannot_answer(stdb_room):
    room, claim, _ = claimed(stdb_room)
    q = question_for(claim, "p1", "MISSING_FACT", "action")
    room.backend.ask_owner(claim, q)
    with pytest.raises(ContractError, match="NOT_AUTHORIZED"):
        room.backend.answer_question("p2", q.id, 1, True, "bad")
    with pytest.raises(ContractError, match="QUESTION_SUPERSEDED"):
        room.backend.answer_question("p1", q.id, 2, True, "bad")


def test_backend_checks_digest_but_never_judges_wording(stdb_room):
    room, claim, snapshot = claimed(stdb_room)
    backend = Recorder(room.backend)
    ref = send_message(backend, claim, snapshot, "mediator", "p1", Act.PROPOSE,
        Payload(draft_ref="d", evaluation="REQUESTED"), "conversation-test", ("mediator", "p1"), 0,
        text="Semantic checks do not run here.", refs=())
    assert ref
    bad = backend.messages[0].model_copy(update={"utterance": "changed", "message_id": "changed", "idempotency_key": "changed"})
    with pytest.raises(ContractError, match="INVALID_VALIDATION_METADATA"):
        room.backend.submit_agent_message(claim, bad, ("mediator", "p1"))


def test_duplicate_message_is_one_turn_and_different_body_conflicts(stdb_room):
    room, claim, snapshot = claimed(stdb_room)
    backend = Recorder(room.backend)
    kwargs = dict(backend=backend, claim=claim, snapshot=snapshot, sender="mediator", recipient="p1", act=Act.PROPOSE,
        payload=Payload(draft_ref="d", evaluation="REQUESTED"), thread_id="conversation-test",
        participants=("mediator", "p1"), round_id=0)
    assert send_message(**kwargs) == send_message(**kwargs)
    threads = room.backend.http.procedure(room.backend.token, "load_execution_context",
                                          job_id=int(claim.job_id), attempt=int(claim.attempt_token))["threads"]
    assert [t["turns_used"] for t in threads] == [1]
    with pytest.raises(ContractError, match="KEY_BODY_MISMATCH"):
        room.backend.submit_agent_message(claim, backend.messages[0].model_copy(update={"utterance": "changed"}),
                                          ("mediator", "p1"))


@pytest.mark.parametrize("recipient,participants", [("outsider", ("mediator", "outsider")), ("p1", ("mediator",)), ("*", ("mediator", "p1"))])
def test_thread_audience_and_broadcast_checks(stdb_room, recipient, participants):
    room, claim, snapshot = claimed(stdb_room)
    with pytest.raises(ContractError, match="NOT_AUTHORIZED"):
        send_message(room.backend, claim, snapshot, "mediator", recipient, Act.PROPOSE,
            Payload(draft_ref="d", evaluation="REQUESTED"), "t", participants, 0)


def test_closed_thread_cannot_receive_new_turn(stdb_room):
    room, claim, snapshot = claimed(stdb_room)
    send_message(room.backend, claim, snapshot, "mediator", "p1", Act.PROPOSE,
        Payload(draft_ref="d", evaluation="REQUESTED"), "t", ("mediator", "p1"), 0)
    room.backend.close_threads(claim, "NO_PROGRESS")
    with pytest.raises(ContractError, match="THREAD_CLOSED"):
        send_message(room.backend, claim, snapshot, "mediator", "p1", Act.PROPOSE,
            Payload(draft_ref="new", evaluation="REQUESTED"), "t", ("mediator", "p1"), 0)


def test_operation_ledger_replays_results(stdb_room):
    room, claim, _ = claimed(stdb_room)
    calls = []
    run = lambda: room.backend.operation(claim, "decide:x", {"q": 1}, "model", lambda: calls.append(1) or {"ok": 1})  # noqa: E731
    assert run() == {"ok": 1} and run() == {"ok": 1}
    assert calls == [1], "a completed operation is reused, not repeated"
    with pytest.raises(ContractError, match="KEY_BODY_MISMATCH"):
        room.backend.operation(claim, "decide:x", {"q": 2}, "model", lambda: {})
