"""Exercise the database boundary directly, including callers bypassing the UI."""
import hashlib
import json
import secrets
import subprocess
from concurrent.futures import ThreadPoolExecutor
import pytest
from conftest import SPACETIME, STDB_SERVER, STDB_DB, STDB_URL, expect_error
from stdb_client import Client, StdbError


def admin(reducer, *args):
    return subprocess.run([SPACETIME, "call", "--server", STDB_SERVER, "--no-config", "-y", STDB_DB,
        reducer, *[json.dumps(a) for a in args]], capture_output=True, text=True, check=True)


def invite(ttl=600):
    token = secrets.token_urlsafe(32)
    admin("create_app_invitation", hashlib.sha256(token.encode()).hexdigest(), "test-invite", ttl)
    return token


def test_closed_by_default_and_owner_only_issuance():
    c = Client(STDB_URL, STDB_DB); stranger = c.mint_identity()
    assert c.view(stranger, "my_app_access") == [{"allowed": False}]
    expect_error("INVITATION_REQUIRED", c.call, stranger, "create_room", goal="Bypass", display_name="Stranger")
    expect_error("INVITATION_REQUIRED", c.call, stranger, "save_profile_answer", question_id="C01", value_json='"Stranger"')
    expect_error("INVITATION_REQUIRED", c.call, stranger, "set_agent_appearance", variant="mint")
    expect_error("NOT_AUTHORIZED", c.call, stranger, "create_app_invitation", token_hash="a"*64, label="bypass", expires_in_seconds=600)
    expect_error("INVITATION_INVALID", c.call, stranger, "redeem_app_invitation", token=secrets.token_urlsafe(32))
    for view in ["my_rooms", "my_profile", "my_profile_answers", "my_agent_appearance", "my_private_messages"]:
        assert c.view(stranger, view) == []
    with pytest.raises(StdbError): c.sql(stranger, "SELECT * FROM app_invitation")


def test_single_use_atomic_redemption_and_revocation():
    from conftest import register_test_identity
    c = Client(STDB_URL, STDB_DB); person = c.mint_identity(); token = invite()
    register_test_identity(c, person, token)
    other = c.mint_identity()
    expect_error('INVITATION_INVALID', register_test_identity, c, other, token)
    c.call(person, 'save_profile_answer', question_id='C01', value_json='"Invited traveller"')
    c.call(person, 'create_room', goal='Private trip', display_name='Invited traveller')
    assert c.view(person, 'my_profile') and c.view(person, 'my_rooms')
    admin('revoke_app_invitation', hashlib.sha256(token.encode()).hexdigest())
    assert c.view(person, 'my_app_access') == [{'allowed': False}]
    for view in ['my_rooms','my_profile','my_profile_answers','room_members','room_agents','my_agent_appearance']:
        assert c.view(person, view) == []
    expect_error('INVITATION_REQUIRED', c.call, person, 'save_profile_answer', question_id='C01', value_json='"Changed"')
    # A new invitation cannot silently bypass suspension of this account.
    expect_error('ACCOUNT_REQUIRED', c.call, person, 'redeem_app_invitation', token=invite())


def test_revoked_unused_invitation_and_lifetime_bounds():
    c=Client(STDB_URL, STDB_DB); person=c.mint_identity(); token=invite()
    admin("revoke_app_invitation",hashlib.sha256(token.encode()).hexdigest())
    expect_error("INVITATION_INVALID",c.call,person,"redeem_app_invitation",token=token)
    for ttl in [0,59,604801]:
        with pytest.raises(subprocess.CalledProcessError): invite(ttl)
