import json
from threading import Event
from types import SimpleNamespace
import pytest
from tandem_worker.contracts import ContractError
from tandem_worker.progress import completed_search, report
from tandem_worker.research_dialogue import search_with_dialogue


def test_findings_are_published_before_last_search_finishes_and_replay_is_idempotent(setup_worker):
    backend, worker, job, model, fixture, clock = setup_worker('happy')
    claim=backend.claim_job(job,'test','claim')
    snapshot=backend.load_execution_context(claim)
    candidates=fixture.search({'observed_at':clock.now(),'purpose':'INITIAL','input_revision':1,'members':list(snapshot.members)})
    from tandem_worker.contracts import Candidate
    sample=Candidate.model_validate(candidates[0])
    published=Event()
    original=backend.submit_agent_message
    def send(*args):
        result=original(*args); published.set(); return result
    backend.submit_agent_message=send
    class Research:
        def search(self, request):
            completed_search(SimpleNamespace(key='actual-result', gateway=SimpleNamespace(category='FLIGHT'),
                search=SimpleNamespace(owner=sample.owner), results=[sample]))
            assert published.wait(3), 'No discussion while provider work was still active'
            return candidates
    assert search_with_dialogue(backend,claim,model,Research(),{},snapshot,0,clock)==candidates
    before=backend.shared_report()['messages']
    assert len(before)==2
    assert 'PRIVATE_CANARY' not in json.dumps(before)
    assert all(m['type']=='RESEARCH_UPDATE' for m in before)
    assert not backend.reusable_candidates(claim), 'A partial handoff is not a completed search batch'
    search_with_dialogue(backend,claim,model,Research(),{},snapshot,0,clock)
    assert backend.shared_report()['messages']==before
    report(backend,claim,'RESEARCH','COMPLETED')
    assert not backend.reusable_candidates(claim), 'Research examples must never replace full itineraries'


def test_search_error_keeps_evidence_but_does_not_claim_complete(setup_worker):
    backend, worker, job, model, fixture, clock=setup_worker('happy')
    claim=backend.claim_job(job,'test','claim'); snapshot=backend.load_execution_context(claim)
    from tandem_worker.contracts import Candidate
    candidate=Candidate.model_validate(fixture.search({'observed_at':clock.now(),'purpose':'INITIAL','input_revision':1,'members':list(snapshot.members)})[0])
    class Research:
        def search(self, request):
            completed_search(SimpleNamespace(key='partial',gateway=SimpleNamespace(category='FLIGHT'),search=SimpleNamespace(owner=candidate.owner),results=[candidate]))
            raise ContractError('APIFY_POLL_DEADLINE_EXCEEDED')
    with pytest.raises(ContractError,match='APIFY_POLL_DEADLINE_EXCEEDED'):
        search_with_dialogue(backend,claim,model,Research(),{},snapshot,0,clock)
    assert backend.shared_report()['messages']
    assert not backend.reusable_candidates(claim)
