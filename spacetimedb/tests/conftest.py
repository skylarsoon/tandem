"""Fixtures for the backend contract tests.

Run through scripts/test.sh, which starts a throwaway in-memory server, publishes
the module and exports STDB_URL / STDB_DB / STDB_SERVER. Never point these tests
at a shared database: they create many identities and rooms.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass, field

import pytest

from stdb_client import Client, Identity, StdbError, some

STDB_URL = os.environ.get("STDB_URL", "http://127.0.0.1:3100")
STDB_DB = os.environ.get("STDB_DB", "tandem-test")
STDB_SERVER = os.environ.get("STDB_SERVER", "tandem-test-local")
SPACETIME = os.environ.get("SPACETIME_BIN", os.path.expanduser("~/.local/bin/spacetime"))

PEOPLE = ["alice", "bob", "carol", "dave"]


def key(prefix: str = "k") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def register_worker(identity: Identity, label: str) -> None:
    # add_worker is owner-only; the CLI calls it as the identity that published.
    subprocess.run(
        [SPACETIME, "call", "--server", STDB_SERVER, "--no-config", "-y", STDB_DB, "add_worker",
         json.dumps(identity.arg), json.dumps(label)],
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session")
def client() -> Client:
    return Client(STDB_URL, STDB_DB)


@pytest.fixture(scope="session")
def workers(client: Client) -> tuple[Identity, Identity]:
    w1, w2 = client.mint_identity(), client.mint_identity()
    register_worker(w1, "pytest-worker-1")
    register_worker(w2, "pytest-worker-2")
    return w1, w2


@pytest.fixture
def worker(workers: tuple[Identity, Identity]) -> Identity:
    return workers[0]


@dataclass
class Room:
    client: Client
    people: dict[str, Identity]
    room_id: int
    invite_code: str
    member_ids: dict[str, int]
    agent_ids: dict[str, int]
    mediator_id: int
    leases: dict[int, int] = field(default_factory=dict)

    # ---------- human helpers ----------

    def confirm_all(self, speak_up: bool = True, ask_first: bool = False, give_time: bool = False,
                    reminders: bool = False) -> None:
        for who in self.people.values():
            self.client.call(who, "confirm_participation_preferences", speak_up_for_me=speak_up,
                             ask_me_first=ask_first, give_me_time=give_time, reminders_opted_in=reminders)

    def set_constraint(self, name: str, key_: str, value, hardness: str = "HARD", rationale: str | None = None) -> None:
        self.client.call(self.people[name], "set_constraint", room_id=self.room_id, key=key_,
                         value_json=json.dumps(value), unit="USD_cents", hardness=hardness,
                         authorized_range_json=None, private_rationale=some(rationale) if rationale else None)

    # ---------- worker helpers ----------

    def jobs(self, worker: Identity, kind: str = "PLAN") -> list[dict]:
        return [j for j in self.client.view(worker, "worker_pending_jobs")
                if j["room_id"] == self.room_id and j["kind"] == kind]

    def plan_job(self, worker: Identity) -> int:
        jobs = self.jobs(worker)
        assert jobs, "expected a pending PLAN job"
        return max(j["id"] for j in jobs)

    def claim(self, worker: Identity, job_id: int | None = None, lease: int = 60) -> dict:
        job_id = job_id if job_id is not None else self.plan_job(worker)
        claim = self.client.procedure(worker, "claim_job", job_id=some(job_id), lease_seconds=lease, request_key=key("claim"))
        self.leases[job_id] = claim["attempt"]
        return claim

    def context(self, worker: Identity, job_id: int) -> dict:
        return self.client.procedure(worker, "load_execution_context", job_id=job_id, attempt=self.leases[job_id])

    def message_args(self, job_id: int, **overrides) -> dict:
        args = dict(
            job_id=job_id,
            attempt=self.leases.get(job_id, 1),
            message_key=key("msg"),
            schema_version=2,
            round_id="round-1",
            thread_id=None,
            open_thread=some({
                "topic_ref": "topic:arrival",
                "participant_agent_ids": [self.mediator_id, self.agent_ids["alice"]],
                "turn_budget": None,
                "successor_of": None,
            }),
            sender_agent_id=self.mediator_id,
            recipient_agent_id=self.agent_ids["alice"],
            type="PROPOSE",
            reply_to=None,
            draft_ref=some("draft-1"),
            proposal_id=None,
            proposal_version=None,
            payload_json=json.dumps({"category": "flight", "draft_ref": "draft-1"}),
            utterance="Here is a draft with everyone landing within two hours.",
            claim_refs=[],
            evidence_refs=[],
            audience="ROOM",
            audience_member_id=None,
            sanitization_status="VALIDATED",
            validation_digest=None,
        )
        args.update(overrides)
        return args

    def send(self, worker: Identity, job_id: int, **overrides) -> dict:
        args = self.message_args(job_id, **overrides)
        self.client.call(worker, "submit_agent_message", **args)
        return args

    def publish(self, worker: Identity, job_id: int, terms: dict | None = None, request_key: str | None = None,
                burdens: dict[str, float] | None = None, decision_id: str = "travel", stage: str = "TRAVEL") -> dict:
        terms = terms or {"flights": {"alice": "UA1", "bob": "UA2", "carol": "AA3", "dave": "DL4"}}
        return self.client.procedure(
            worker, "publish_proposal",
            job_id=job_id, attempt=self.leases[job_id], request_key=request_key or key("publish"),
            stage=stage, decision_id=decision_id, scope="GENERAL_AGREEMENT", terms_json=json.dumps(terms, sort_keys=True),
            shared_summary_json=json.dumps({"summary": "Everyone lands within two hours"}),
            validation_json=json.dumps({"hard_constraints": "PASS"}),
            evidence_refs=[], affected_member_ids=[], selected_candidate_refs=[],
            impacts=[{"member_id": self.member_ids[n], "impact_json": json.dumps({"cost_cents": 45000})} for n in PEOPLE],
            burdens=[{"member_id": self.member_ids[n], "burden_micros": round(b * 1_000_000)}
                     for n, b in (burdens or {}).items()],
        )


def make_room(client: Client) -> Room:
    people = {name: client.mint_identity() for name in PEOPLE}
    client.call(people["alice"], "create_room", goal="Hawaii trip", display_name="Alice")
    rooms = client.view(people["alice"], "my_rooms")
    room = max(rooms, key=lambda r: r["id"])
    for name in PEOPLE[1:]:
        client.call(people[name], "join_room", invite_code=room["invite_code"], display_name=name.title())

    members = [m for m in client.view(people["alice"], "room_members") if m["room_id"] == room["id"]]
    by_display = {m["display_name"].lower(): m for m in members}
    agents = [a for a in client.view(people["alice"], "room_agents") if a["room_id"] == room["id"]]
    mediator = next(a for a in agents if a["kind"] == "MEDIATOR")
    return Room(
        client=client,
        people=people,
        room_id=room["id"],
        invite_code=room["invite_code"],
        member_ids={n: by_display[n]["id"] for n in PEOPLE},
        agent_ids={n: by_display[n]["agent_id"] for n in PEOPLE},
        mediator_id=mediator["id"],
    )


@pytest.fixture
def room(client: Client) -> Room:
    return make_room(client)


@pytest.fixture
def ready_room(room: Room, worker: Identity) -> tuple[Room, int]:
    """Room with confirmed preferences and constraints, plus a claimed PLAN job."""
    room.confirm_all()
    for name, cap in zip(PEOPLE, [50000, 65000, 65000, 65000]):
        room.set_constraint(name, "flight_budget_total", cap)
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    return room, job_id


def expect_error(expected: str, fn, *args, **kwargs) -> StdbError:
    with pytest.raises(StdbError) as info:
        fn(*args, **kwargs)
    assert info.value.code == expected, f"expected {expected}, got {info.value.code}: {info.value.detail}"
    return info.value
