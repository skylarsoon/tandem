import json

from tandem_worker.progress import activity, report
from tandem_worker.contracts import ContractError
import pytest


def test_workflow_reports_only_allowlisted_room_safe_fields(setup_worker):
    backend, worker, job, *_ = setup_worker('clarification-restart')
    assert worker.run_one(job)['outcome'] == 'WAITING_FOR_INPUT'
    events = [e for e in backend.shared_report()['events'] if e['kind'] == 'WORKFLOW_PROGRESS']
    assert events
    encoded = json.dumps(events)
    assert 'PRIVATE_CANARY' not in encoded and 'question-' not in encoded
    assert 'hard_cap' not in encoded and 'private_reason' not in encoded
    # Shared pause is generic, never the owner who needs to answer.
    paused = [e for e in events if e['payload']['phase'] == 'PAUSED']
    assert paused and 'owner' not in paused[0]['payload']


def test_telemetry_deduplicates_and_rejects_expired_attempts(setup_worker):
    backend, _, job, _, _, clock = setup_worker('happy')
    claim = backend.claim_job(job, 'worker', 'claim')
    report(backend, claim, 'RESEARCH', 'STARTED')
    report(backend, claim, 'RESEARCH', 'STARTED')
    assert len([e for e in backend.shared_report()['events'] if e['kind'] == 'WORKFLOW_PROGRESS']) == 1
    clock.advance(61)
    with pytest.raises(ContractError):
        activity(backend, claim, 'mediator', 'SEARCHING')


def test_unavailable_progress_reporting_does_not_change_planning(setup_worker):
    backend, worker, job, *_ = setup_worker('happy')
    def unavailable(*args, **kwargs):
        raise RuntimeError('telemetry offline')
    backend.room_event = unavailable
    backend.set_agent_activity = unavailable
    assert worker.run_one(job)['outcome'] == 'AWAITING_REVIEW'
    assert backend.shared_report()['consent_count'] == 0
