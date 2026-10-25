import http.client
import json
import threading
from types import SimpleNamespace

import pytest

from tandem_worker.service import WorkerService, make_http_server


def make_service(tmp_path, run=None):
    backend = SimpleNamespace(pending_jobs=lambda: [{'id':1,'input_revision':1,'membership_revision':1,'permission_revision':1}])
    calls = []
    run = run or (lambda *a, **kw: (calls.append(kw) or {'result':{'outcome':'WAITING_FOR_INPUT'}}))
    clock = [0.0]
    service = WorkerService(None, None, tmp_path, 1, backend=backend, run=run, settle_seconds=2, monotonic=lambda:clock[0])
    return service, backend, calls, clock


def test_repeated_wakes_share_one_authoritative_scan_and_settle_inputs(tmp_path):
    service, backend, calls, clock = make_service(tmp_path)
    assert service.scan_once() == 'SETTLING_INPUTS'
    clock[0] = 1
    service.wake.set()
    assert service.scan_once() == 'SETTLING_INPUTS'
    clock[0] = 2
    assert service.scan_once() == 'WAITING_FOR_INPUT'
    assert len(calls) == 1
    backend.pending_jobs = lambda: []
    assert service.scan_once() == 'NO_ELIGIBLE_WORK'
    assert len(calls) == 1


def test_input_revision_changes_restart_quiet_window(tmp_path):
    service, backend, calls, clock = make_service(tmp_path)
    service.scan_once()
    clock[0] = 2
    backend.pending_jobs = lambda:[{'id':2,'input_revision':2}]
    assert service.scan_once() == 'SETTLING_INPUTS'
    assert not calls


def test_periodic_polling_runs_without_a_notification(tmp_path):
    done = threading.Event()
    service, backend, _, clock = make_service(tmp_path, run=lambda *a,**kw:(done.set() or {'result':{'outcome':'AWAITING_REVIEW'}}))
    queried = []
    def pending():
        queried.append(True)
        return [] if len(queried)==1 else [{'id':1}]
    backend.pending_jobs = pending
    service.settle_seconds=0
    service.poll_interval=.02
    thread=threading.Thread(target=service.run_forever)
    thread.start()
    try:
        assert done.wait(2), 'startup/polling must work without a dispatcher POST'
    finally:
        service.stop()
        thread.join(timeout=2)


def test_wake_endpoint_authentication_room_validation_and_hint_only(tmp_path):
    service, backend, calls, _ = make_service(tmp_path)
    server=make_http_server(service,'test-wake-secret',port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    def post(body, token='test-wake-secret'):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        conn.request('POST','/wake',json.dumps(body),{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        response=conn.getresponse()
        code=response.status
        response.read();conn.close()
        return code
    try:
        body={'room_id':1,'job_id':99,'kind':'PLAN'}
        assert post(body,'wrong')==403
        assert post({**body,'room_id':2})==404
        assert post({'room_id':1})==400
        assert post(body)==202
        assert post(body)==202
        assert service.wake.is_set()
        assert not calls, 'HTTP requests may only wake the scanning loop, never execute work'
        service.stopped.set()
        assert post(body)==503
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


test_wake_endpoint_authentication_room_validation_and_hint_only = pytest.mark.enable_socket(test_wake_endpoint_authentication_room_validation_and_hint_only)


def test_only_one_service_can_execute_per_state_directory(tmp_path):
    from tandem_worker.contracts import ContractError
    entered, release = threading.Event(), threading.Event()
    def run(*args, **kwargs):
        entered.set()
        release.wait(2)
        return {'result':{'outcome':'AWAITING_REVIEW'}}
    first, _, _, _ = make_service(tmp_path, run=run)
    first.settle_seconds = 0
    second, _, _, _ = make_service(tmp_path)
    thread = threading.Thread(target=first.run_forever)
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(ContractError, match='WORKER_SERVICE_ALREADY_RUNNING'):
            second.run_forever()
    finally:
        first.stop();release.set();thread.join(timeout=2)
