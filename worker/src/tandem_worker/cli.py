from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from pathlib import Path

from .backend import LocalBackend
from .contracts import Message, Question, Snapshot
from .fixtures import FixtureResearch, ScriptedModel, scenario_inputs
from .ports import SystemClock
from .runner import Worker


SCENARIOS = ("happy", "targeted-budget", "agent-objection", "clarification-restart")


def run_scenario(name, directory):
    directory.mkdir(parents=True, exist_ok=True)
    clock = SystemClock()
    backend = LocalBackend(directory / "backend.sqlite", clock)
    snapshot, briefs = scenario_inputs(name)
    job = backend.seed(snapshot, briefs)
    model = ScriptedModel(name)
    worker = Worker(backend, model, FixtureResearch(clock, name), directory / "checkpoints.sqlite", clock=clock)
    result = worker.run_one(job)
    if name == "clarification-restart" and result["outcome"] == "WAITING_FOR_INPUT":
        # Reopen all connections/dependencies. The test suite also verifies this
        # sequence across two distinct Python OS processes.
        for owner in snapshot.members:
            for q in backend.owner_questions(owner):
                if q.answer is None:
                    backend.answer_question(owner, q.id, q.version, True, "fixture-answer:" + q.id)
        backend = LocalBackend(directory / "backend.sqlite", clock)
        worker = Worker(backend, ScriptedModel(name), FixtureResearch(clock, name), directory / "checkpoints.sqlite", clock=clock)
        result = worker.reconcile()[-1]
    return {"result": result, "report": backend.shared_report(), "mode": "FIXTURE"}


def main():
    parser = argparse.ArgumentParser(description="Tandem worker: synthetic offline scenarios and typed contracts")
    sub = parser.add_subparsers(dest="command", required=True)
    scenario = sub.add_parser("scenario")
    scenario.add_argument("name", choices=SCENARIOS)
    scenario.add_argument("--mode", choices=["fixture"], default="fixture")
    scenario.add_argument("--state-dir", type=Path, help="Fresh directory; retains synthetic backend/checkpoint files")
    seed = sub.add_parser("seed", help="Create synthetic local state for manual worker testing")
    seed.add_argument("name", choices=SCENARIOS)
    seed.add_argument("--state-dir", type=Path, required=True)
    for command in ("run-one", "reconcile", "questions", "answer"):
        command_parser = sub.add_parser(command, help="Local fixture backend only")
        command_parser.add_argument("--state-dir", type=Path, required=True)
        if command == "run-one":
            command_parser.add_argument("job_id")
        if command in {"run-one", "reconcile"}:
            command_parser.add_argument("--scenario", choices=SCENARIOS, default="happy")
        if command in {"questions", "answer"}:
            command_parser.add_argument("--owner", required=True)
        if command == "answer":
            command_parser.add_argument("question_id")
            command_parser.add_argument("--version", type=int, default=1)
            command_parser.add_argument("--response", choices=["yes", "no"], required=True)
    sub.add_parser("contracts", help="Print JSON schemas for backend integration")
    args = parser.parse_args()
    if args.command == "contracts":
        print(json.dumps({c.__name__: c.model_json_schema() for c in (Message, Question, Snapshot)}, indent=2))
        return
    if args.command != "scenario":
        args.state_dir.mkdir(parents=True, exist_ok=True)
        clock = SystemClock()
        backend = LocalBackend(args.state_dir / "backend.sqlite", clock)
        if args.command == "seed":
            snapshot, briefs = scenario_inputs(args.name)
            result = {"job_id": backend.seed(snapshot, briefs), "mode": "FIXTURE"}
        elif args.command == "questions":
            result = [q.model_dump(mode="json") for q in backend.owner_questions(args.owner)]
        elif args.command == "answer":
            result = {"answer_ref": backend.answer_question(args.owner, args.question_id, args.version,
                args.response == "yes", uuid.uuid4().hex), "mode": "FIXTURE"}
        else:
            worker = Worker(backend, ScriptedModel(args.scenario), FixtureResearch(clock, args.scenario),
                args.state_dir / "checkpoints.sqlite", clock=clock)
            result = worker.run_one(args.job_id) if args.command == "run-one" else worker.reconcile()
        print(json.dumps(result, indent=2))
        return
    if args.state_dir:
        result = run_scenario(args.name, args.state_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="tandem-worker-") as directory:
            result = run_scenario(args.name, Path(directory))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
