"""Admin gateway is separate from workers and participant identities."""
import hashlib
import json
import secrets
import subprocess
import pytest
from conftest import STDB_URL, STDB_DB, STDB_SERVER, SPACETIME, expect_error, register_test_identity
from stdb_client import Client, StdbError


def owner(name, *args):
    subprocess.run([SPACETIME, 'call', '--server', STDB_SERVER, '--no-config', '-y', STDB_DB, name,
                    *map(json.dumps, args)], check=True, capture_output=True)


def digest():
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()


@pytest.fixture
def admin():
    c = Client(STDB_URL, STDB_DB); gateway = c.mint_identity()
    email = f'{secrets.token_hex(8)}@example.test'
    owner('configure_admin_gateway', gateway.arg, True)
    owner('configure_admin_account', email, True, 'a'*128, 'b'*64)
    try: yield c, gateway, email
    finally:
        owner('configure_admin_account', email, False, 'a'*128, 'b'*64)
        owner('configure_admin_gateway', gateway.arg, False)


def login(c, g, email, change=True):
    credential = c.procedure(g, 'admin_login_credential', email=email, client_hash=digest())['credential']
    session_hash = digest()
    c.procedure(g, 'create_admin_session', email=email, version=credential['version'], session_hash=session_hash)
    if change: c.procedure(g, 'change_admin_password', session_hash=session_hash, password_hash='c'*128, password_salt='d'*64)
    return session_hash


def action(c, g, session_hash, operation, **payload):
    return c.procedure(g, 'admin_action', session_hash=session_hash, action=operation, payload_json=json.dumps(payload))


def test_gateway_boundary_and_private_credentials(admin, client, worker):
    c, g, email = admin; stranger = c.mint_identity()
    for who in [stranger, worker]:
        expect_error('NOT_AUTHORIZED', c.procedure, who, 'admin_login_credential', email=email, client_hash=digest())
        expect_error('NOT_AUTHORIZED', c.call, who, 'configure_admin_account', email='intruder@example.test', enabled=True, password_hash='a'*128, password_salt='b'*64)
    for table in ['admin_account', 'admin_session', 'admin_gateway', 'admin_audit']:
        with pytest.raises(StdbError): c.sql(g, f'SELECT * FROM {table}')
    expect_error('INVITATION_REQUIRED', c.call, g, 'create_room', goal='not a participant', display_name='Gateway')
    assert c.procedure(g, 'admin_login_credential', email='unknown@example.test', client_hash=digest())['credential'] is None
    expect_error('NOT_AUTHORIZED', c.procedure, g, 'admin_overview', session_hash=digest())


def test_first_login_password_reset_and_version_revocation(admin):
    c,g,email=admin
    first = login(c,g,email,False)
    overview=c.procedure(g,'admin_overview',session_hash=first)
    assert overview['mustChangePassword'] and 'users' not in overview and 'recentJobs' not in overview
    expect_error('PASSWORD_CHANGE_REQUIRED', action,c,g,first,'create_invitation',tokenHash=digest(),label='blocked',hours=24)
    second=login(c,g,email,False)
    c.procedure(g,'change_admin_password',session_hash=first,password_hash='e'*128,password_salt='f'*64)
    assert not c.procedure(g,'admin_overview',session_hash=first)['mustChangePassword']
    expect_error('NOT_AUTHORIZED',c.procedure,g,'admin_overview',session_hash=second)
    owner('configure_admin_account',email,False,'a'*128,'b'*64)
    expect_error('NOT_AUTHORIZED',c.procedure,g,'admin_overview',session_hash=first)


def test_invitation_user_access_health_and_logout(admin):
    c,g,email=admin; session_hash=login(c,g,email)
    token=secrets.token_urlsafe(32); token_hash=hashlib.sha256(token.encode()).hexdigest()
    action(c,g,session_hash,'create_invitation',tokenHash=token_hash,label='Access test',hours=24)
    person=c.mint_identity(); register_test_identity(c,person,token)
    assert c.view(person,'my_app_access') == [{'allowed': True}]
    action(c,g,session_hash,'set_user_access',identity=person.hex,revoked=True)
    assert c.view(person,'my_app_access') == [{'allowed': False}]
    action(c,g,session_hash,'set_user_access',identity=person.hex,revoked=False)
    assert c.view(person,'my_app_access') == [{'allowed': True}]
    snapshot=c.procedure(g,'admin_overview',session_hash=session_hash)
    assert any(u['identity']==person.hex for u in snapshot['users'])
    assert all(set(u)=={'identity','name','revoked','grantedAt'} for u in snapshot['users'])
    assert all(set(j)=={'id','kind','status','attempt','updatedAt'} for j in snapshot['recentJobs'])
    assert 'passwordHash' not in json.dumps(snapshot) and 'passwordSalt' not in json.dumps(snapshot)
    expect_error('NOT_AUTHORIZED',action,c,g,session_hash,'add_admin',email='intruder@example.test')
    expect_error('NOT_AUTHORIZED',action,c,g,session_hash,'retry_job',jobId='1')
    action(c,g,session_hash,'revoke_invitation',tokenHash=token_hash)
    assert c.view(person,'my_app_access') == [{'allowed': False}]
    action(c,g,session_hash,'logout')
    expect_error('NOT_AUTHORIZED',c.procedure,g,'admin_overview',session_hash=session_hash)


def test_persistent_login_limit_and_disabled_gateway(admin):
    c,g,email=admin
    for _ in range(10):
        assert not c.procedure(g,'admin_login_credential',email=email,client_hash=digest())['limited']
    assert c.procedure(g,'admin_login_credential',email=email,client_hash=digest())['limited']
    owner('configure_admin_gateway',g.arg,False)
    expect_error('NOT_AUTHORIZED',c.procedure,g,'admin_login_credential',email=email,client_hash=digest())


def test_admin_can_open_app_without_invite_and_cannot_transfer_handoff(admin):
    c,g,email=admin; session_hash=login(c,g,email)
    person=c.mint_identity(); other=c.mint_identity()
    token=secrets.token_urlsafe(32)
    action(c,g,session_hash,'open_app',tokenHash=hashlib.sha256(token.encode()).hexdigest())
    c.call(person,'redeem_admin_app_access',token=token)
    assert c.view(person,'my_app_access') == [{'allowed':True}]
    c.call(person,'save_profile_answer',question_id='C01',value_json='"Existing admin traveller"')
    expect_error('INVALID_HANDOFF',c.call,other,'redeem_admin_app_access',token=token)
    expect_error('INVALID_HANDOFF',c.call,other,'redeem_admin_app_access',token=secrets.token_urlsafe(32))
    # Reopening uses the same identity and retains its data.
    token2=secrets.token_urlsafe(32)
    action(c,g,session_hash,'open_app',tokenHash=hashlib.sha256(token2.encode()).hexdigest())
    c.call(person,'redeem_admin_app_access',token=token2)
    assert c.view(person,'my_profile')[0]['display_name']=='Existing admin traveller'
    # Disabling the admin account closes admin-derived app access too.
    owner('configure_admin_account',email,False,'a'*128,'b'*64)
    assert c.view(person,'my_app_access') == [{'allowed':False}]
    assert c.view(person,'my_profile') == []


def test_app_handoff_requires_password_change_and_current_session(admin):
    c,g,email=admin; session_hash=login(c,g,email,False)
    token=secrets.token_urlsafe(32); token_hash=hashlib.sha256(token.encode()).hexdigest()
    expect_error('PASSWORD_CHANGE_REQUIRED',action,c,g,session_hash,'open_app',tokenHash=token_hash)
    c.procedure(g,'change_admin_password',session_hash=session_hash,password_hash='c'*128,password_salt='d'*64)
    action(c,g,session_hash,'open_app',tokenHash=token_hash)
    action(c,g,session_hash,'logout')
    expect_error('NOT_AUTHORIZED',c.call,c.mint_identity(),'redeem_admin_app_access',token=token)


def open_app(c, gateway, session_hash, device):
    token=secrets.token_urlsafe(32)
    action(c,gateway,session_hash,'open_app',tokenHash=hashlib.sha256(token.encode()).hexdigest())
    c.call(device,'redeem_admin_app_access',token=token)


def test_account_profile_trips_and_private_messages_follow_two_devices(admin):
    c,g,email=admin; first=login(c,g,email)
    a=c.mint_identity(); b=c.mint_identity(); stranger=c.mint_identity()
    open_app(c,g,first,a)
    c.call(a,'save_profile_answer',question_id='C01',value_json='"Cross-device traveller"')
    c.call(a,'set_agent_appearance',variant='mint')
    c.call(a,'create_room',goal='Cross-device trip',display_name='Traveller')
    room=c.view(a,'my_rooms')[0]; room_id=room['id']
    second=login(c,g,email,False)
    open_app(c,g,second,b)
    assert c.view(b,'my_profile')==c.view(a,'my_profile')
    assert c.view(b,'my_profile_answers')==c.view(a,'my_profile_answers')
    assert c.view(b,'my_agent_appearance')==c.view(a,'my_agent_appearance')
    assert c.view(b,'my_rooms')==c.view(a,'my_rooms')
    assert sum(m['is_me'] for m in c.view(b,'room_members'))==1
    c.call(b,'save_profile_answer',question_id='C01',value_json='"Edited from phone"')
    assert c.view(a,'my_profile')[0]['display_name']=='Edited from phone'
    c.call(b,'submit_private_message',room_id=room_id,body='Private message from second device')
    assert c.view(a,'my_private_messages')==c.view(b,'my_private_messages')
    assert c.view(a,'my_private_messages')[-1]['body']=='Private message from second device'
    c.call(b,'set_trip_description',room_id=room_id,description='An organiser can edit from either device.')
    assert c.view(a,'room_trip_brief')==c.view(b,'room_trip_brief')
    for view in ['my_rooms','my_profile','my_profile_answers','my_private_messages']:
        assert c.view(stranger,view)==[]
    expect_error('INVITATION_REQUIRED',c.call,stranger,'submit_private_message',room_id=room_id,body='Intrusion')
    for table in ['participant_account','participant_device']:
        with pytest.raises(StdbError): c.sql(b,f'SELECT * FROM {table}')
    # Sign-out retracts all private access for that device, but preserves the
    # other device and stored data. A fresh login recovers the same records.
    action(c,g,second,'logout')
    assert c.view(b,'my_app_access')==[{'allowed':False}]
    assert c.view(b,'my_profile')==[] and c.view(b,'my_rooms')==[]
    assert c.view(a,'my_profile')[0]['display_name']=='Edited from phone'
    expect_error('INVITATION_REQUIRED',c.call,b,'save_profile_answer',question_id='C01',value_json='"Blocked"')
    third=login(c,g,email,False);open_app(c,g,third,b)
    assert c.view(a,'my_profile')==c.view(b,'my_profile')
    c.procedure(g,'change_admin_password',session_hash=third,password_hash='e'*128,password_salt='f'*64)
    assert c.view(a,'my_app_access')==[{'allowed':False}]
    assert c.view(b,'my_app_access')==[{'allowed':True}]


def test_different_accounts_on_same_device_never_inherit_each_others_data(admin):
    c,g,email=admin; first=login(c,g,email)
    a=c.mint_identity(); open_app(c,g,first,a)
    c.call(a,'save_profile_answer',question_id='C01',value_json='"Account one private profile"')
    second_email=f'{secrets.token_hex(8)}@example.test'
    owner('configure_admin_account',second_email,True,'a'*128,'b'*64)
    try:
        other=login(c,g,second_email)
        open_app(c,g,other,a)
        assert c.view(a,'my_profile')==[]
        c.call(a,'save_profile_answer',question_id='C01',value_json='"Account two private profile"')
        open_app(c,g,first,a)
        assert c.view(a,'my_profile')[0]['display_name']=='Account one private profile'
        b=c.mint_identity();open_app(c,g,other,b)
        assert c.view(b,'my_profile')[0]['display_name']=='Account two private profile'
    finally: owner('configure_admin_account',second_email,False,'a'*128,'b'*64)
