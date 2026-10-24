"""Fixtures for running the worker against a real, disposable SpacetimeDB.

Run through ../scripts/backend-integration.sh, which starts an in-memory server,
publishes the module, registers a worker identity and sets TANDEM_STDB_*.
Without those variables every test here is skipped.
"""
import os
from dataclasses import dataclass

import pytest

from tandem_worker.adapters.spacetime import SpacetimeBackend, StdbHttp
from tandem_worker.contracts import Brief, Limits
from tandem_worker.fixtures import scenario_inputs

URL = os.environ.get("TANDEM_STDB_URL")
DB = os.environ.get("TANDEM_STDB_DB")
WORKER_TOKEN = os.environ.get("TANDEM_STDB_WORKER_TOKEN")


def pytest_collection_modifyitems(items):
    for item in items:
        if "/tests/backend/" in str(item.fspath):
            item.add_marker(pytest.mark.backend)
            item.add_marker(pytest.mark.enable_socket)
            if not (URL and DB and WORKER_TOKEN):
                item.add_marker(pytest.mark.skip(reason="set TANDEM_STDB_URL/DB/WORKER_TOKEN (see backend-integration.sh)"))


@dataclass
class Room:
    backend: SpacetimeBackend
    room_id: int
    tokens: dict[str, str]

    def plan_job(self) -> str:
        jobs = self.backend.eligible_jobs()
        assert jobs, "expected a pending PLAN job"
        return jobs[-1]

    def view(self, owner: str, name: str) -> list[dict]:
        return [r for r in self.backend.http.view(self.tokens[owner], name) if r.get("room_id") == self.room_id]


def seed_room(briefs: list[Brief], *, limits: Limits | None = None) -> Room:
    """Creates one human per brief (display name = owner), confirms the settings
    the brief says are confirmed, and enters each budget as a HARD constraint
    with the brief's private reason."""
    http = StdbHttp(URL, DB)
    tokens = {b.owner: http.mint_identity()["token"] for b in briefs}
    organizer = briefs[0].owner
    http.call(tokens[organizer], "create_room", goal="Hawaii trip (integration test)", display_name=organizer)
    room = max(http.view(tokens[organizer], "my_rooms"), key=lambda r: r["id"])
    for b in briefs[1:]:
        http.call(tokens[b.owner], "join_room", invite_code=room["invite_code"], display_name=b.owner)
    for b in briefs:
        token = tokens[b.owner]
        if b.preferences.confirmed:
            http.call(token, "confirm_participation_preferences", speak_up_for_me=b.preferences.speak_up_for_me,
                      ask_me_first=b.preferences.ask_me_first, give_me_time=b.preferences.give_me_time,
                      reminders_opted_in=b.reminders_opted_in)
        http.call(token, "set_constraint", room_id=room["id"], key="flight_budget_total",
                  value_json=str(b.hard_cap_cents), unit="USD_cents", hardness="HARD", authorized_range_json=None,
                  private_rationale={"some": b.private_reason} if b.private_reason else None)
        if b.baggage_required:
            http.call(token, "set_constraint", room_id=room["id"], key="baggage_required", value_json="true",
                      unit="", hardness="HARD", authorized_range_json=None, private_rationale=None)
    backend = SpacetimeBackend(URL, DB, WORKER_TOKEN, limits=limits, room_id=room["id"],
                               owner_name=lambda member: member["display_name"], owner_tokens=tokens)
    return Room(backend=backend, room_id=room["id"], tokens=tokens)


@pytest.fixture
def stdb_room():
    def create(scenario: str = "happy", briefs: list[Brief] | None = None, limits: Limits | None = None) -> Room:
        return seed_room(briefs or scenario_inputs(scenario)[1], limits=limits)
    return create
