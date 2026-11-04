"""Simulation isolation and real worker contracts, on a throwaway database only."""
import json
from datetime import datetime, timedelta, timezone
from conftest import key, expect_error, STDB_URL, STDB_DB, Room, PEOPLE
from stdb_client import Client, some

def start(client):
    person=client.mint_identity()
    day=datetime.now(timezone.utc)+timedelta(days=25)
    args=dict(start_date=day.date().isoformat(),end_date=(day+timedelta(days=3)).date().isoformat(),request_key=key('simulation'))
    client.call(person,'start_planning_simulation',**args)
    row=client.view(person,'my_planning_simulations')[0]
    return person,row,json.loads(row['snapshot_json']),args

def test_four_real_worker_agents_are_isolated_from_profile_and_other_accounts(client,worker):
    person,row,snapshot,args=start(client)
    client.call(person,'start_planning_simulation',**args)
    assert len(client.view(person,'my_planning_simulations'))==1
    assert len(snapshot['members'])==4
    assert {m['origin'] for m in snapshot['members']}=={'SFO','LAX','SEA','PDX'}
    assert client.view(person,'my_rooms')==[]
    outsider=client.mint_identity()
    assert client.view(outsider,'my_planning_simulations')==[]
    expect_error('NOT_AUTHORIZED',client.call,outsider,'stop_planning_simulation',room_id=row['room_id'])
    expect_error('SIMULATION_DAILY_LIMIT',client.call,person,'start_planning_simulation',**{**args,'request_key':key()})
    job=int(snapshot['job']['id'])
    lease=client.procedure(worker,'claim_job',job_id=some(job),lease_seconds=60,request_key=key())
    context=client.procedure(worker,'load_execution_context',job_id=job,attempt=lease['attempt'])
    assert len(context['members'])==4
    assert all(p['participation_preferences']['confirmed'] for p in context['members'])
    assert len([c for c in context['constraints'] if c['key']=='origin_airport'])==4
    # The regular worker receives normal confirmed inputs, not fabricated replies.
    assert not snapshot['messages']
    client.call(worker,'fail_job',job_id=job,attempt=lease['attempt'],request_key=key(),retryable=False,code='APIFY_CONNECTION_FAILED',detail='Controlled contract test')
    retry=dict(room_id=row['room_id'],job_id=job,request_key=key())
    client.call(person,'retry_planning_simulation',**retry)
    client.call(person,'retry_planning_simulation',**retry)
    retried=json.loads(client.view(person,'my_planning_simulations')[0]['snapshot_json'])
    assert retried['retries']==1
    assert int(retried['job']['id'])!=job
    client.call(person,'stop_planning_simulation',room_id=row['room_id'])

def test_stopped_simulation_invalidates_worker_lease_and_cannot_accept_questions(client,worker):
    person,row,snapshot,args=start(client)
    job=int(snapshot['job']['id'])
    lease=client.procedure(worker,'claim_job',job_id=some(job),lease_seconds=60,request_key=key())
    client.call(person,'stop_planning_simulation',room_id=row['room_id'])
    after=client.view(person,'my_planning_simulations')[0]
    assert after['state']=='STOPPED'
    assert json.loads(after['snapshot_json'])['job']['status']=='SUPERSEDED'
    expect_error('STALE_SNAPSHOT',client.procedure,worker,'load_execution_context',job_id=job,attempt=lease['attempt'])
    expect_error('INVALID_STATE',client.call,person,'answer_simulation_question',room_id=row['room_id'],question_id=0,version=1,answer_json='{}')

def test_owner_can_answer_only_questions_in_own_demo_and_starts_are_bounded(client,worker):
    person,row,snapshot,args=start(client)
    job=int(snapshot['job']['id']);member=int(snapshot['members'][0]['id'])
    lease=client.procedure(worker,'claim_job',job_id=some(job),lease_seconds=60,request_key=key())
    client.call(worker,'ask_owner',job_id=job,attempt=lease['attempt'],request_key=key(),member_id=member,kind='MISSING_FACT',action_json='{}',prompt='Does a late arrival work for this fictional traveller?',answer_schema_json='{}',thread_id=None)
    current=json.loads(client.view(person,'my_planning_simulations')[0]['snapshot_json'])
    question=current['questions'][0]
    client.call(person,'answer_simulation_question',room_id=row['room_id'],question_id=int(question['id']),version=1,answer_json='{"answer":true}')
    assert not json.loads(client.view(person,'my_planning_simulations')[0]['snapshot_json'])['questions']
    expect_error('NOT_AUTHORIZED',client.call,person,'answer_simulation_question',room_id=row['room_id'],question_id=999999,version=1,answer_json='{}')
    fourth=client.mint_identity()
    expect_error('SIMULATION_CAPACITY',client.call,fourth,'start_planning_simulation',**{**args,'request_key':key()})
    # No app invitation/account means no public paid simulation endpoint.
    anonymous=Client(STDB_URL,STDB_DB).mint_identity()
    expect_error('INVITATION_REQUIRED',client.call,anonymous,'start_planning_simulation',**{**args,'request_key':key()})
    assert client.view(None,'my_planning_simulations')==[]
    demo=Room(client=client,people={},room_id=row['room_id'],invite_code='',member_ids={n:int(m['id']) for n,m in zip(PEOPLE,snapshot['members'])},agent_ids={n:int(m['agentId']) for n,m in zip(PEOPLE,snapshot['members'])},mediator_id=int(snapshot['mediatorId']),leases={job:lease['attempt']})
    demo.send(worker,job)
    shared=json.loads(client.view(person,'my_planning_simulations')[0]['snapshot_json'])
    assert shared['messages'][-1]['utterance']=='Here is a draft with everyone landing within two hours.'
    from test_proposals import record_flights,publish_selected
    refs=record_flights(demo,worker,job,{name:{'total_cents':20000} for name in PEOPLE})
    proposal=publish_selected(demo,worker,job,refs,scope='FLIGHT_BUNDLE',decision_id='flights')
    review=dict(room_id=row['room_id'],member_id=member,proposal_id=proposal['proposal_id'],version=proposal['version'],terms_digest=proposal['terms_digest'],response='ACCEPT')
    expect_error('NOT_AUTHORIZED',client.call,fourth,'review_simulation_proposal',**review)
    expect_error('STALE_SNAPSHOT',client.call,person,'review_simulation_proposal',**{**review,'terms_digest':'0'*64})
    for m in snapshot['members']:
        client.call(person,'review_simulation_proposal',**{**review,'member_id':int(m['id'])})
    final=json.loads(client.view(person,'my_planning_simulations')[0]['snapshot_json'])
    assert final['proposals'][-1]['state']=='AGREED'
    assert len(final['proposals'][-1]['consents'])==4
    client.call(person,'stop_planning_simulation',room_id=row['room_id'])
