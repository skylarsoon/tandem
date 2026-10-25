"""Test-only provider boundary: actual Python graph and DB; no provider network calls."""
import argparse
import json
import os
from pathlib import Path

from tandem_worker.adapters.spacetime import SpacetimeBackend
from tandem_worker.contracts import Act, Candidate, Decision
from tandem_worker.fixtures import ScriptedModel
from tandem_worker.ports import SystemClock
from tandem_worker.runner import Worker


class Model(ScriptedModel):
    def __init__(self, scenario):
        super().__init__("happy")
        self.scenario = scenario

    def decide(self, context):
        if self.scenario == "clarify" and context["owner"] == "p1" and not context["missing_fact_answered"]:
            return Decision(act=Act.CLARIFY, evaluation="NEEDS_OWNER_INPUT", owner_question="PRIVATE_P1: Is this departure date OK?")
        return Decision()


class Research:
    def __init__(self, scenario):
        self.scenario = scenario

    def search(self, request):
        stamp = request["observed_at"]
        base = int(stamp) + 86400
        offers = [("p1", "early", 10000, 0), ("p2", "early", 10000, 300)]
        if self.scenario == "concession":
            offers = [("p1", "early", 10000, 0), ("p1", "late", 20000, 18000),
                      ("p2", "late", 10000, 18000), ("p2", "early", 15000, 300)]
        return [Candidate(id=f"{owner}-{name}", owner=owner, total_cents=price, arrival_epoch=base + offset,
                          observed_at=stamp, valid_until=stamp + 900, source_url="https://fixtures.invalid/flight",
                          run_ref="browser-fixture", mode="FIXTURE", cost_basis="COMPLETE_FLIGHT_TOTAL", baggage_included=True)
                for owner, name, price, offset in offers if owner in request["members"]]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--room", type=int, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--scenario", choices=["clarify", "concession"], default="clarify")
    args = parser.parse_args()
    # Deliberately require test-scoped variables, never load private .env files.
    backend = SpacetimeBackend(os.environ["TANDEM_STDB_URL"], os.environ["TANDEM_STDB_DB"], os.environ["TANDEM_STDB_WORKER_TOKEN"],
                              room_id=args.room, owner_name=lambda member: member["display_name"])
    args.state.mkdir(parents=True, exist_ok=True)
    worker = Worker(backend, Model(args.scenario), Research(args.scenario), args.state / "checkpoint.sqlite", clock=SystemClock(),
                    before_run=lambda claim: backend.notify_owner(claim, "p1", "PRIVATE_NOTE_P1: Your private planning note.", "browser-private-note"))
    print(json.dumps(worker.reconcile()))
