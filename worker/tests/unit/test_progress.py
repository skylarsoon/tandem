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


def test_research_context_attaches_revision_and_resets_between_rooms(setup_worker):
    from types import SimpleNamespace
    from tandem_worker.progress import research_reporting, research_update
    backend, _, job, *_ = setup_worker('happy')
    claim = backend.claim_job(job, 'worker', 'claim')
    search = SimpleNamespace(key='search-key', gateway=SimpleNamespace(category='FLIGHT', actor_id='public-actor'), run={'run_id':'run-1','polls':1}, results=[])
    with research_reporting(backend, claim, round=2):
        research_update(search, 'RUNNING')
    research_update(search, 'FAILED')  # No observer outside the claimed job.
    events = [e for e in backend.shared_report()['events'] if e['kind'] == 'RESEARCH_ACTIVITY']
    assert len(events) == 1
    assert events[0]['payload']['round'] == 2
    assert events[0]['payload']['job_ref'] == claim.job_id
    assert events[0]['payload']['input_revision'] == backend.load_execution_context(claim).input_revision


def test_search_details_come_from_submitted_input_and_exclude_private_filters():
    from types import SimpleNamespace
    from tandem_worker.progress import search_context
    job = SimpleNamespace(gateway=SimpleNamespace(category='FLIGHT'), search=SimpleNamespace(leg='return'), purpose='TARGETED', actor_input={
        'origin':'SAN','destination':'SFO','departDate':'2027-06-06','adults':1,'maxItems':10,
        'maxStops':0,'checkedBags':1,'hard_cap':500,'private_reason':'PRIVATE_CANARY','token':'SECRET'})
    detail=search_context(job)
    assert detail == {'origin':'SAN','destination':'SFO','departure_date':'2027-06-06','travellers':1,'result_limit':10,'leg':'return','purpose':'TARGETED'}
    assert 'PRIVATE_CANARY' not in json.dumps(detail) and 'SECRET' not in json.dumps(detail)


def test_restaurant_query_and_location_are_reported_without_raw_actor_input():
    from types import SimpleNamespace
    from tandem_worker.progress import search_context
    job=SimpleNamespace(gateway=SimpleNamespace(category='DINNER'),purpose='INITIAL',actor_input={
        'searchStringsArray':['restaurants for groups'],'locationQuery':'San Diego','maxCrawledPlacesPerSearch':10,'scrapeContacts':False})
    assert search_context(job) == {'queries':['restaurants for groups'],'location':'San Diego','result_limit':10,'purpose':'INITIAL'}


def test_private_search_context_is_dropped_without_losing_progress(setup_worker):
    from types import SimpleNamespace
    from tandem_worker.progress import research_reporting,research_update
    backend,_,job,*_=setup_worker('happy'); claim=backend.claim_job(job,'worker','claim')
    original=backend.room_event
    def guarded(claim,kind,payload,key):
        if 'search_context' in payload: raise ContractError('PRIVATE_CONTENT_DETECTED')
        return original(claim,kind,payload,key)
    backend.room_event=guarded
    search=SimpleNamespace(key='s',gateway=SimpleNamespace(category='DINNER',actor_id='actor'),actor_input={'locationQuery':'private place'},run={},results=[])
    with research_reporting(backend,claim):research_update(search,'RUNNING')
    events=[e for e in backend.shared_report()['events'] if e['kind']=='RESEARCH_ACTIVITY']
    assert len(events)==1 and 'search_context' not in events[0]['payload']
