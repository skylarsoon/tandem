"""Admin gateway is separate from workers and participant identities."""
import hashlib
import json
import secrets
import subprocess
import pytest
from conftest import STDB_URL, STDB_DB, STDB_SERVER, SPACETIME, expect_error
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
    person=c.mint_identity(); c.call(person,'redeem_app_invitation',token=token)
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
