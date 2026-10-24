"""Live multiplayer signals: presence, agent activity and review reminders."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from conftest import (PEOPLE, SPACETIME, STDB_DB, STDB_SERVER, STDB_URL, Room, expect_error, key, make_room)

DISPATCHER_DIR = Path(__file__).resolve().parents[2] / "dispatcher"
needs_node = pytest.mark.skipif(
    not (DISPATCHER_DIR / "node_modules").exists() or shutil.which("npx") is None,
    reason="dispatcher dependencies not installed (cd dispatcher && npm install)",
)


def wait_for(predicate, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def presence(room: Room, viewer: str) -> dict[int, dict]:
    return {p["membership_id"]: p for p in room.client.view(room.people[viewer], "room_presence")
            if p["room_id"] == room.room_id}


def activity(room: Room, viewer: str) -> dict[int, str]:
    return {a["agent_id"]: a["activity"] for a in room.client.view(room.people[viewer], "room_agent_activity")
            if a["room_id"] == room.room_id}


@needs_node
def test_presence_follows_open_connections(room: Room):
    alice_member = room.member_ids["alice"]
    assert presence(room, "bob")[alice_member]["online"] is False

    env = {**os.environ, "STDB_URL": STDB_URL, "STDB_DB": STDB_DB, "STDB_TOKEN": room.people["alice"].token}
    tabs = [subprocess.Popen(["npx", "tsx", "src/tools/hold-connection.ts"], cwd=DISPATCHER_DIR, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(2)]
    try:
        assert wait_for(lambda: presence(room, "bob")[alice_member]["online"]), "alice should show online"
        tabs[0].terminate()
        tabs[0].wait(timeout=10)
        time.sleep(1)
        assert presence(room, "bob")[alice_member]["online"], "one tab closed, the other is still open"
    finally:
        for tab in tabs:
            tab.terminate()
            tab.wait(timeout=10)
    assert wait_for(lambda: not presence(room, "bob")[alice_member]["online"]), "alice should go offline"
    assert presence(room, "bob")[alice_member]["last_seen"] is not None


def test_presence_is_scoped_to_my_rooms(room: Room, client):
    other = make_room(client)
    rows = client.view(room.people["alice"], "room_presence")
    assert {p["room_id"] for p in rows} == {room.room_id}
    assert all(p["room_id"] != other.room_id for p in rows)


def test_agent_activity_is_live_and_lease_bound(ready_room, client, worker):
    room, job_id = ready_room
    alice_agent = room.agent_ids["alice"]
    client.call(worker, "set_agent_activity", job_id=job_id, attempt=room.leases[job_id], agent_id=alice_agent,
                activity="SEARCHING", thread_id=None)
    for viewer in PEOPLE:
        assert activity(room, viewer)[alice_agent] == "SEARCHING"

    expect_error("INVALID_ARGUMENT", client.call, worker, "set_agent_activity", job_id=job_id,
                 attempt=room.leases[job_id], agent_id=alice_agent, activity="PARTYING", thread_id=None)
    other = make_room(client)
    expect_error("NOT_AUTHORIZED", client.call, worker, "set_agent_activity", job_id=job_id,
                 attempt=room.leases[job_id], agent_id=other.agent_ids["bob"], activity="THINKING", thread_id=None)
    expect_error("NOT_AUTHORIZED", client.call, room.people["alice"], "set_agent_activity", job_id=job_id,
                 attempt=room.leases[job_id], agent_id=alice_agent, activity="THINKING", thread_id=None)
    assert activity(other, "alice") == {}

    client.call(worker, "finish_job", job_id=job_id, attempt=room.leases[job_id], request_key=key(),
                outcome="PAUSED_NO_PROGRESS", outcome_json="{}")
    assert activity(room, "bob")[alice_agent] == "IDLE", "a finished job never leaves agents busy"


@pytest.fixture
def short_reminders():
    def set_delay(seconds: int) -> None:
        subprocess.run([SPACETIME, "call", "--server", STDB_SERVER, "--no-config", "-y", STDB_DB,
                        "set_reminder_delay", str(seconds)], check=True, capture_output=True)
    set_delay(2)
    yield
    set_delay(600)


def test_review_reminders_respect_give_me_time(room: Room, client, worker, short_reminders):
    room.confirm_all(speak_up=True, ask_first=False, give_time=False, reminders=True)
    client.call(room.people["bob"], "confirm_participation_preferences",
                speak_up_for_me=True, ask_me_first=False, give_me_time=True, reminders_opted_in=True)
    client.call(room.people["dave"], "confirm_participation_preferences",
                speak_up_for_me=True, ask_me_first=False, give_me_time=False, reminders_opted_in=False)
    for name, cap in zip(PEOPLE, [50000, 65000, 65000, 65000]):
        room.set_constraint(name, "flight_budget_total", cap)
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    published = room.publish(worker, job_id)
    client.call(room.people["carol"], "respond_to_proposal", proposal_id=published["proposal_id"],
                version=published["version"], terms_digest=published["terms_digest"], response="ACCEPT", note=None)

    def reminders(name: str) -> list[dict]:
        return [json.loads(e["safe_payload_json"]) for e in client.view(room.people[name], "room_events")
                if e["type"] == "REVIEW_REMINDER" and e["room_id"] == room.room_id]

    assert wait_for(lambda: reminders("alice"), timeout=10)
    assert reminders("alice") == [{"proposal_id": published["proposal_id"], "stage": "TRAVEL",
                                   "version": published["version"]}]
    assert reminders("bob") == [], "bob asked for time"
    assert reminders("carol") == [], "carol already responded"
    assert reminders("dave") == [], "dave never opted in to reminders"


def test_reminder_delay_is_owner_only(room: Room, client):
    expect_error("NOT_AUTHORIZED", client.call, room.people["alice"], "set_reminder_delay", seconds=1)
