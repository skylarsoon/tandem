"""Fixtures for running the worker against a real, disposable SpacetimeDB.

Run through ../scripts/backend-integration.sh, which starts an in-memory server,
publishes the module, registers a worker identity and sets TANDEM_STDB_*.
Without those variables every test here is skipped.
"""
import os
from dataclasses import dataclass

import pytest

from tandem_worker.adapters.spacetime import SpacetimeBackend, StdbHttp, seed_room
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


def make_room(briefs: list[Brief], *, limits: Limits | None = None, settings: dict | None = None) -> Room:
    seeded = seed_room(StdbHttp(URL, DB), briefs, goal="Hawaii trip (integration test)", settings=settings)
    backend = SpacetimeBackend(URL, DB, WORKER_TOKEN, limits=limits, room_id=seeded["room_id"],
                               owner_name=lambda member: member["display_name"], owner_tokens=seeded["owner_tokens"])
    return Room(backend=backend, room_id=seeded["room_id"], tokens=seeded["owner_tokens"])


@pytest.fixture
def stdb_room():
    def create(scenario: str = "happy", briefs: list[Brief] | None = None, limits: Limits | None = None) -> Room:
        snapshot, defaults = scenario_inputs(scenario)
        settings = {"components": list(snapshot.components)} if len(snapshot.components) > 1 else None
        return make_room(briefs or defaults, limits=limits, settings=settings)
    return create
