"""Invitations create ordinary accounts, never administrators; two devices share data."""
import hashlib,json,secrets,subprocess
from concurrent.futures import ThreadPoolExecutor
import pytest
from conftest import STDB_URL,STDB_DB,STDB_SERVER,SPACETIME,expect_error
from stdb_client import Client,StdbError


def owner(name,*args):
    subprocess.run([SPACETIME,'call','--server',STDB_SERVER,'--no-config','-y',STDB_DB,name,*map(json.dumps,args)],check=True,capture_output=True)


def digest(value): return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def accounts():
    c=Client(STDB_URL,STDB_DB);gateway=c.mint_identity()
    owner('configure_admin_gateway',gateway.arg,True)
    try: yield c,gateway
    finally: owner('configure_admin_gateway',gateway.arg,False)


def invitation():
    token=secrets.token_urlsafe(32)
    owner('create_app_invitation',digest(token),'account-test',600)
    return token


def prepare(c,g,invite='',email=None):
    token=secrets.token_urlsafe(32);session=digest(secrets.token_urlsafe(32));address=email or f'{secrets.token_hex(10)}@example.test'
    c.procedure(g,'prepare_traveller_registration',email=address,password_hash='a'*128,password_salt='b'*64,invitation_hash=digest(invite) if invite else '',token_hash=digest(token),session_hash=session)
    return token,session,address


def register(c,g,person,invite=None,email=None):
    token,session,address=prepare(c,g,invitation() if invite is None else invite,email)
    c.call(person,'complete_traveller_registration',token=token)
    return session,address


def login(c,g,address):
    session=digest(secrets.token_urlsafe(32))
    credential=c.procedure(g,'traveller_login_credential',email=address,client_hash=digest(secrets.token_urlsafe(32)))['credential']
    c.procedure(g,'create_traveller_session',email=address,version=credential['version'],session_hash=session)
    return session


def open_app(c,g,session,person):
    token=secrets.token_urlsafe(32)
    c.procedure(g,'traveller_account_action',session_hash=session,action='open_app',token_hash=digest(token))
    c.call(person,'redeem_traveller_access',token=token)


def test_registration_requires_invitation_and_never_grants_admin(accounts):
    c,g=accounts;a=c.mint_identity();token,session,address=prepare(c,g)
    expect_error('INVITATION_INVALID',c.call,a,'complete_traveller_registration',token=token)
    assert c.view(a,'my_app_access')==[{'allowed':False}]
    session,address=register(c,g,a)
    assert c.view(a,'my_app_access')==[{'allowed':True}]
    expect_error('NOT_AUTHORIZED',c.procedure,g,'admin_overview',session_hash=session)
    expect_error('NOT_AUTHORIZED',c.procedure,a,'traveller_login_credential',email=address,client_hash=digest('test'))
    for table in ['traveller_credential','traveller_session','traveller_registration']:
        with pytest.raises(StdbError):c.sql(a,f'SELECT * FROM {table}')
    expect_error('EMAIL_IN_USE',prepare,c,g,invitation(),address)
    expect_error('ACCOUNT_REQUIRED',c.call,c.mint_identity(),'redeem_app_invitation',token=invitation())


def test_invitation_race_is_atomic_and_retry_is_device_bound(accounts):
    c,g=accounts;invite=invitation();people=[c.mint_identity(),c.mint_identity()]
    pending=[prepare(c,g,invite),prepare(c,g,invite)]
    def finish(i):
        try:c.call(people[i],'complete_traveller_registration',token=pending[i][0]);return i
        except StdbError as e:assert e.code=='INVITATION_INVALID';return None
    winners=[i for i in ThreadPoolExecutor(2).map(finish,[0,1]) if i is not None]
    assert len(winners)==1
    win=winners[0];lose=1-win
    c.call(people[win],'complete_traveller_registration',token=pending[win][0])
    expect_error('INVALID_HANDOFF',c.call,people[lose],'complete_traveller_registration',token=pending[win][0])
    assert c.view(people[lose],'my_app_access')==[{'allowed':False}]


def test_profile_trips_private_data_logout_and_password_revocation(accounts):
    c,g=accounts;a=c.mint_identity();b=c.mint_identity();stranger=c.mint_identity()
    first,address=register(c,g,a)
    c.call(a,'save_profile_answer',question_id='C01',value_json='"Travelling together"')
    c.call(a,'create_room',goal='Portable trip',display_name='Traveller')
    room=c.view(a,'my_rooms')[0]
    second=login(c,g,address);open_app(c,g,second,b)
    assert c.view(b,'my_rooms')==c.view(a,'my_rooms')
    assert c.view(b,'my_profile')==c.view(a,'my_profile')
    c.call(b,'submit_private_message',room_id=room['id'],body='My private preference')
    assert c.view(b,'my_private_messages')==c.view(a,'my_private_messages')
    assert c.view(stranger,'my_private_messages')==[]
    c.procedure(g,'traveller_account_action',session_hash=second,action='logout',token_hash='')
    assert c.view(b,'my_profile')==[] and c.view(b,'my_rooms')==[]
    assert c.view(a,'my_profile')
    third=login(c,g,address);open_app(c,g,third,b)
    c.procedure(g,'change_traveller_password',session_hash=third,password_hash='c'*128,password_salt='d'*64)
    assert c.view(a,'my_app_access')==[{'allowed':False}]
    assert c.view(b,'my_app_access')==[{'allowed':True}]
    owner('reset_traveller_password',address,'e'*128,'f'*64)
    assert c.view(b,'my_app_access')==[{'allowed':False}]
    reset=login(c,g,address)
    assert c.procedure(g,'traveller_account_status',session_hash=reset)['mustChangePassword']
    expect_error('PASSWORD_CHANGE_REQUIRED',open_app,c,g,reset,b)


def test_revocation_and_account_switch_do_not_leak_or_lose_data(accounts):
    c,g=accounts;a=c.mint_identity();invite=invitation();first,address=register(c,g,a,invite)
    c.call(a,'save_profile_answer',question_id='C01',value_json='"First account"')
    second,other=register(c,g,a)
    assert c.view(a,'my_profile')==[]
    c.call(a,'save_profile_answer',question_id='C01',value_json='"Second account"')
    open_app(c,g,first,a)
    assert c.view(a,'my_profile')[0]['display_name']=='First account'
    owner('revoke_app_invitation',digest(invite))
    assert c.view(a,'my_profile')==[]
    expect_error('ACCESS_SUSPENDED',open_app,c,g,first,a)
    open_app(c,g,second,a)
    assert c.view(a,'my_profile')[0]['display_name']=='Second account'
