"""Run these invariants against the real adapter when that module is available.

These cases prove the local double's behavior, not deployed authorization.
"""
import pytest

from tandem_worker.contracts import Act, ContractError, Payload, Preferences
from tandem_worker.graphs.personal import question_for, send_message


def test_claim_idempotency_and_conflicting_body(setup_worker):
    backend, _, job, *_ = setup_worker()
    claim = backend.claim_job(job, "w", "same")
    assert backend.claim_job(job, "w", "same") == claim
    with pytest.raises(ContractError, match="KEY_BODY_MISMATCH"):
        backend.claim_job("another-job", "w", "same")


def test_no_private_read_for_unknown_owner(setup_worker):
    backend, _, job, *_ = setup_worker()
    claim = backend.claim_job(job, "w", "x")
    with pytest.raises(ContractError, match="NOT_AUTHORIZED"):
        backend.read_brief(claim, "outsider")


def test_wrong_owner_or_stale_question_cannot_answer(setup_worker):
    backend, _, job, *_ = setup_worker()
    claim = backend.claim_job(job, "w", "x")
    q = question_for(claim, "p1", "MISSING_FACT", "action")
    backend.ask_owner(claim, q)
    with pytest.raises(ContractError, match="NOT_AUTHORIZED"):
        backend.answer_question("p2", q.id, 1, True, "bad")
    with pytest.raises(ContractError, match="QUESTION_SUPERSEDED"):
        backend.answer_question("p1", q.id, 2, True, "bad")


def test_backend_checks_digest_but_never_judges_wording(setup_worker):
    backend, worker, job, *_ = setup_worker()
    claim = backend.claim_job(job, "w", "x")
    snapshot = backend.load_execution_context(claim).model_dump(mode="json")
    # Deliberately nonsensical wording with valid trusted-worker metadata is
    # accepted by the LOCAL backend. Worker V02/V03 must prevent submitting it.
    ref = send_message(backend, claim, snapshot, "mediator", "p1", Act.PROPOSE,
        Payload(draft_ref="d", evaluation="REQUESTED"), "conversation-test", ("mediator", "p1"), 0,
        text="Semantic checks do not run here.", refs=())
    assert ref
    from tandem_worker.contracts import Message
    message = Message.model_validate(backend.shared_report()["messages"][0])
    bad = message.model_copy(update={"utterance": "changed", "message_id": "changed", "idempotency_key": "changed"})
    with pytest.raises(ContractError, match="INVALID_VALIDATION_METADATA"):
        backend.submit_agent_message(claim, bad, ("mediator", "p1"))


def test_duplicate_message_is_one_turn_and_different_body_conflicts(setup_worker):
    backend, _, job, *_ = setup_worker()
    claim = backend.claim_job(job, "w", "x")
    snapshot = backend.load_execution_context(claim).model_dump(mode="json")
    kwargs = dict(backend=backend, claim=claim, snapshot=snapshot, sender="mediator", recipient="p1", act=Act.PROPOSE,
        payload=Payload(draft_ref="d", evaluation="REQUESTED"), thread_id="conversation-test", participants=("mediator", "p1"), round_id=0)
    assert send_message(**kwargs) == send_message(**kwargs)
    assert backend.shared_report()["threads"]["conversation-test"]["turns"] == 1
    from tandem_worker.contracts import Message
    message = Message.model_validate(backend.shared_report()["messages"][0])
    with pytest.raises(ContractError, match="KEY_BODY_MISMATCH"):
        backend.submit_agent_message(claim, message.model_copy(update={"utterance": "changed"}), ("mediator", "p1"))


@pytest.mark.parametrize("recipient,participants", [("outsider", ("mediator", "outsider")), ("p1", ("mediator",)), ("*", ("mediator", "p1"))])
def test_thread_audience_and_broadcast_checks(setup_worker, recipient, participants):
    backend, _, job, *_ = setup_worker()
    claim = backend.claim_job(job, "w", "x")
    snapshot = backend.load_execution_context(claim).model_dump(mode="json")
    with pytest.raises(ContractError, match="NOT_AUTHORIZED"):
        send_message(backend, claim, snapshot, "mediator", recipient, Act.PROPOSE,
            Payload(draft_ref="d", evaluation="REQUESTED"), "t", participants, 0)


def test_closed_thread_cannot_receive_new_turn(setup_worker):
    backend, _, job, *_ = setup_worker()
    claim = backend.claim_job(job, "w", "x")
    snapshot = backend.load_execution_context(claim).model_dump(mode="json")
    send_message(backend, claim, snapshot, "mediator", "p1", Act.PROPOSE,
        Payload(draft_ref="d", evaluation="REQUESTED"), "t", ("mediator", "p1"), 0)
    backend.close_threads(claim, "NO_PROGRESS")
    with pytest.raises(ContractError, match="THREAD_CLOSED"):
        send_message(backend, claim, snapshot, "mediator", "p1", Act.PROPOSE,
            Payload(draft_ref="new", evaluation="REQUESTED"), "t", ("mediator", "p1"), 0)
