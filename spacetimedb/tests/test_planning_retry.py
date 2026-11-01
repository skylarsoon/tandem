import json
import pytest
from conftest import expect_error, key


def finish(client, worker, room, job, outcome):
    client.call(worker,'finish_job',job_id=job,attempt=room.leases[job],request_key=key(),outcome=outcome,outcome_json='{}')


@pytest.mark.parametrize("outcome", ["APIFY_POLL_DEADLINE_EXCEEDED", "INVALID_RESEARCH_REQUEST"])
def test_retry_is_organizer_only_idempotent_and_cannot_duplicate_active_work(ready_room,client,worker,outcome):
    room, job=ready_room
    args=dict(room_id=room.room_id,job_id=job,request_key=key())
    expect_error('NOT_AUTHORIZED',client.call,room.people['bob'],'retry_planning',**args)
    expect_error('JOB_NOT_ELIGIBLE',client.call,room.people['alice'],'retry_planning',**args)
    finish(client,worker,room,job,outcome)
    client.call(room.people['alice'],'retry_planning',**args)
    client.call(room.people['alice'],'retry_planning',**args)
    pending=room.jobs(worker)
    assert len(pending)==1 and pending[0]['id']!=job
    expect_error('JOB_NOT_ELIGIBLE',client.call,room.people['alice'],'retry_planning',**{**args,'request_key':key()})


def test_retry_does_not_bypass_provider_allowance_or_reopen_success(ready_room,client,worker):
    room,job=ready_room
    finish(client,worker,room,job,'ACTOR_RUN_BUDGET_EXHAUSTED')
    expect_error('JOB_NOT_ELIGIBLE',client.call,room.people['alice'],'retry_planning',room_id=room.room_id,job_id=job,request_key=key())


def test_research_handoff_requires_confirmed_preferences_and_recorded_evidence(ready_room,client,worker):
    room,job=ready_room
    room.send(worker,job,type='RESEARCH_UPDATE',utterance='These are research findings, not an approved plan.',claim_refs=['act:RESEARCH_UPDATE'])
    assert any(m['type']=='RESEARCH_UPDATE' for m in client.view(room.people['bob'],'room_messages'))
    expect_error('UTTERANCE_PAYLOAD_MISMATCH',room.send,worker,job,type='RESEARCH_UPDATE',claim_refs=['act:RESEARCH_UPDATE','invented#price'])
    assert room.context(worker,job)['research_complete'] is False
