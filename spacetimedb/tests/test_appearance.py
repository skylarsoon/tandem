"""Companion cosmetics persist by owner without changing planning inputs."""
from conftest import expect_error


def test_appearance_is_shared_only_through_room_members(room, client):
    alice, bob = room.people['alice'], room.people['bob']
    before = client.view(alice, 'my_profile')
    revision = client.view(alice, 'my_rooms')[0]['input_revision']
    bob_before = client.view(bob, 'my_agent_appearance')
    client.call(alice, 'set_agent_appearance', variant='peach')
    assert client.view(alice, 'my_agent_appearance')[0]['variant'] == 'peach'
    assert client.view(bob, 'my_agent_appearance') == bob_before
    member = next(m for m in client.view(bob, 'room_members') if m['display_name'].lower() == 'alice')
    appearance = next(a for a in client.view(bob, 'room_agent_appearances') if a['membership_id'] == member['id'])
    assert appearance['variant'] == 'peach'
    assert 'owner' not in member and 'member' not in member and 'self_description' not in member
    assert client.view(alice, 'my_profile') == before
    assert client.view(alice, 'my_rooms')[0]['input_revision'] == revision
    outsider = client.mint_identity()
    assert client.view(outsider, 'room_members') == []
    assert client.view(outsider, 'room_agent_appearances') == []
    expect_error('SQL', client.sql, bob, 'SELECT * FROM agent_appearance')


def test_appearance_works_before_onboarding_and_follows_owner(client):
    person = client.mint_identity()
    default = client.view(person, 'my_agent_appearance')[0]['variant']
    assert default in ('lavender', 'peach', 'mint', 'yellow')
    assert client.view(person, 'my_profile') == []
    client.call(person, 'set_agent_appearance', variant='yellow')
    for goal in ('First trip', 'Second trip'):
        client.call(person, 'create_room', goal=goal, display_name='New traveller')
    members = client.view(person, 'room_members')
    assert len(members) == 2
    assert all(a['variant'] == 'yellow' for a in client.view(person, 'room_agent_appearances'))
    client.call(person, 'set_agent_appearance', variant='mint')
    assert all(a['variant'] == 'mint' for a in client.view(person, 'room_agent_appearances'))


def test_appearance_rejects_unknown_assets_and_worker_writes(room, client, worker):
    alice = room.people['alice']
    before = client.view(alice, 'my_agent_appearance')
    for value in ('../private', 'https://example.com/image.png', '', 'mediator'):
        expect_error('INVALID_ARGUMENT', client.call, alice, 'set_agent_appearance', variant=value)
    assert client.view(alice, 'my_agent_appearance') == before
    expect_error('NOT_AUTHORIZED', client.call, worker, 'set_agent_appearance', variant='mint')
