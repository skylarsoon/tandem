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


def test_completed_research_survives_permission_grant_but_not_changed_trip_input(ready_room,client,worker):
    from test_questions import ask, park, answer
    room,job=ready_room
    context=room.context(worker,job)
    j=context['job']
    payload=dict(schema_version=1,stage='RESEARCH',phase='COMPLETED',job_ref=str(job),
                 input_revision=j['input_revision'],membership_revision=j['membership_revision'],permission_revision=j['permission_revision'])
    client.call(worker,'emit_event',job_id=job,attempt=room.leases[job],request_key=key(),
                type='WORKFLOW_PROGRESS',member_id=None,payload_json=json.dumps(payload))
    assert room.context(worker,job)['research_complete'] is True
    question=ask(room,worker,job,'alice',kind='CONCESSION_CONSULTATION',action={'action_ref':'draft-1'})
    park(room,worker,job,[question['id']])
    answer(room,'alice',question,{'granted':True})
    newer=room.plan_job(worker);room.claim(worker,newer)
    assert room.context(worker,newer)['research_complete'] is True
    assert client.view(room.people['alice'],'my_consents')==[]
    room.set_constraint('alice','flight_budget_total',90000)
    changed=room.plan_job(worker);room.claim(worker,changed)
    assert room.context(worker,changed)['research_complete'] is False
