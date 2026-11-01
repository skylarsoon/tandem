import pytest
from tandem_worker.backend import LocalBackend
from tandem_worker.contracts import ContractError
from tandem_worker.fixtures import scenario_inputs, ScriptedModel
from tandem_worker.ports import FakeClock
from tandem_worker.runner import Worker


@pytest.mark.parametrize('components,stages', [
    (('FLIGHT','STAY'), ()),
    (('FLIGHT','STAY','DINNER'), ()),
    (('STAY','ACTIVITY'), ()),
    (('FLIGHT','STAY','DINNER'), ('FLIGHT','STAY','DINNER')),
])
def test_full_trip_includes_discovery_without_duplicate_categories(tmp_path,components,stages):
    clock=FakeClock()
    snapshot,briefs=scenario_inputs('happy')
    snapshot=snapshot.model_copy(update={'components':components,'stages':stages})
    backend=LocalBackend(tmp_path/'backend.sqlite',clock)
    job=backend.seed(snapshot,briefs)
    requests=[]
    class Research:
        def search(self,request):
            requests.append(request)
            # Stop after the actual graph reaches the provider boundary.
            raise ContractError('SCOPE_CAPTURED')
    worker=Worker(backend,ScriptedModel('happy'),Research(),tmp_path/'checkpoints.sqlite',clock=clock)
    assert worker.run_one(job)['outcome']=='SCOPE_CAPTURED'
    assert len(requests)==1
    assert requests[0]['categories']==(list(components) if stages else list(dict.fromkeys([*components, 'ACTIVITY', 'DINNER'])))
