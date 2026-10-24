from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from pathlib import Path

from .backend import LocalBackend
from .contracts import ContractError, Message, Question, Snapshot
from .fixtures import FixtureResearch, ScriptedModel, scenario_inputs
from .ports import SystemClock
from .runner import Worker


from .fixtures import FRICTION

SCENARIOS = ("happy", "targeted-budget", "agent-objection", "clarification-restart", *FRICTION)
# How each owner answers questions when a scenario pauses for them.
ANSWERS = {"clarification-restart": True, "group-arrival": True, "concession-declined": False, "own-room-squeeze": True, "stay-rating": False}


def run_scenario(name, directory):
    directory.mkdir(parents=True, exist_ok=True)
    clock = SystemClock()
    backend = LocalBackend(directory / "backend.sqlite", clock)
    snapshot, briefs = scenario_inputs(name)
    job = backend.seed(snapshot, briefs)
    model = ScriptedModel(name)
    worker = Worker(backend, model, FixtureResearch(clock, name), directory / "checkpoints.sqlite", clock=clock)
    result = worker.run_one(job)
    # A granted permission restarts planning, which may ask the next owner; answer
    # until the run settles.
    for _ in range(len(snapshot.members) + 1):
        if name not in ANSWERS or result["outcome"] != "WAITING_FOR_INPUT":
            break
        # Reopen all connections/dependencies. The test suite also verifies this
        # sequence across two distinct Python OS processes.
        for owner in snapshot.members:
            for q in backend.owner_questions(owner):
                if q.answer is None:
                    backend.answer_question(owner, q.id, q.version, ANSWERS[name], "fixture-answer:" + q.id)
        backend = LocalBackend(directory / "backend.sqlite", clock)
        worker = Worker(backend, ScriptedModel(name), FixtureResearch(clock, name), directory / "checkpoints.sqlite", clock=clock)
        result = worker.reconcile()[-1]
    return {"result": result, "report": backend.shared_report(), "mode": "FIXTURE",
            "private": {owner: backend.owner_view(owner) for owner in snapshot.members}}


def _main():
    parser = argparse.ArgumentParser(description="Tandem worker: explicit fixture and live-provider workflows")
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
    providers = sub.add_parser("providers", help="Configure/check Claude and Apify; local test state only")
    provider_actions = providers.add_subparsers(dest="provider_action", required=True)
    for name in ("check", "smoke"):
        command_parser = provider_actions.add_parser(name)
        command_parser.add_argument("--config", type=Path, required=True)
        command_parser.add_argument("--env-file", type=Path, default=Path(".env"))
        if name == "check":
            command_parser.add_argument("--online", action="store_true", help="Read-only provider metadata checks; no generation or Actor starts")
        else:
            command_parser.add_argument("--state-dir", type=Path, required=True)
    run = sub.add_parser("run", help="Run the worker with live providers against SpacetimeDB")
    run.add_argument("--providers", choices=["live"], required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--env-file", type=Path, default=Path(".env"))
    run.add_argument("--state-dir", type=Path, required=True)
    run.add_argument("--room", type=int, help="Plan an existing app room instead of the room seeded in --state-dir")
    live_room = sub.add_parser("live-room", help="Seeded SpacetimeDB room for live demos without the frontend")
    room_actions = live_room.add_subparsers(dest="room_action", required=True)
    for name in ("seed", "questions", "answer"):
        command_parser = room_actions.add_parser(name)
        command_parser.add_argument("--state-dir", type=Path, required=True)
        command_parser.add_argument("--env-file", type=Path, default=Path(".env"))
        if name == "seed":
            command_parser.add_argument("--config", type=Path, required=True)
        else:
            command_parser.add_argument("--owner", required=True)
        if name == "answer":
            command_parser.add_argument("question_ref")
            command_parser.add_argument("--version", type=int, default=1)
            command_parser.add_argument("--response", choices=["yes", "no"], required=True)
    serve_parser = sub.add_parser("serve", help="Process real rooms on SpacetimeDB: onboarding and planning")
    serve_parser.add_argument("--env-file", type=Path, default=Path(".env"))
    serve_parser.add_argument("--state-dir", type=Path, required=True)
    serve_parser.add_argument("--config", type=Path, help="JSON {limits, dinner_query}; defaults otherwise")
    serve_parser.add_argument("--room", type=int, action="append", help="Only these rooms (repeatable)")
    serve_parser.add_argument("--once", action="store_true", help="One sweep, then exit")
    serve_parser.add_argument("--kinds", help="Only these job kinds, comma-separated (e.g. INTAKE,INTERPRET)")
    intake = sub.add_parser("intake", help="Onboarding simulations")
    intake_actions = intake.add_subparsers(dest="intake_action", required=True)
    simulate_parser = intake_actions.add_parser("simulate", help="Claude plays each persona against the live worker")
    simulate_parser.add_argument("--personas", required=True, help="Comma-separated names from personas/travellers.json")
    simulate_parser.add_argument("--env-file", type=Path, default=Path(".env"))
    simulate_parser.add_argument("--state-dir", type=Path, required=True)
    simulate_parser.add_argument("--config", type=Path)
    simulate_parser.add_argument("--plan", action="store_true", help="Also plan the trip with live searches (costs Apify runs)")
    args = parser.parse_args()
    if args.command in {"serve", "intake"}:
        from .config import load_settings
        from .serve import ServeConfig, serve
        settings = load_settings(args.env_file)
        config = ServeConfig.model_validate_json(args.config.read_text()) if args.config else ServeConfig()
        if args.command == "serve":
            result = serve(config, settings, args.state_dir, rooms=set(args.room) if args.room else None, once=args.once,
                           kinds=set(args.kinds.split(",")) if args.kinds else None)
        else:
            from .simulate import simulate
            result = simulate(settings, args.state_dir, [n.strip() for n in args.personas.split(",")], plan=args.plan,
                              config=config)
        print(json.dumps(result, indent=2))
        return
    if args.command == "live-room":
        from .config import load_config, load_settings
        from .live import live_answer, live_questions, seed_live_room
        settings = load_settings(args.env_file)
        if args.room_action == "seed":
            result = seed_live_room(load_config(args.config), settings, args.state_dir)
        elif args.room_action == "questions":
            result = live_questions(settings, args.state_dir, args.owner)
        else:
            result = live_answer(settings, args.state_dir, args.owner, args.question_ref, args.version, args.response == "yes")
        print(json.dumps(result, indent=2))
        return
    if args.command in {"providers", "run"}:
        # Fixture-only commands do not import providers, read .env, or need keys.
        from .config import load_config, load_settings
        from .live import check_providers, run_live, smoke
        config, settings = load_config(args.config), load_settings(args.env_file)
        if args.command == "run":
            result = run_live(config, settings, args.state_dir, room_id=args.room)
        elif args.provider_action == "check":
            result = check_providers(config, settings, online=args.online)
        else:
            result = smoke(config, settings, args.state_dir)
        print(json.dumps(result, indent=2))
        if result.get("status") not in (None, "OK"):
            raise SystemExit(2)
        return
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


def main():
    try:
        _main()
    except ContractError as error:
        print(json.dumps({"status": "ERROR", "code": error.code}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
