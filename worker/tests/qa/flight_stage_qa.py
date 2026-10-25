"""The flight-stage QA brief, live, end to end: a fresh local SpacetimeDB with the current
module, the real worker (Claude reads every message and powers every agent; the default
optimizer chooses), the four personas onboarding through their private chats (their QA
message verbatim, Claude playing their follow-ups), and controlled flight itineraries
(tests/fixtures/qa/flight_stage.json) instead of live search, as the brief asks for the
first run. Everything it does is something a person or the organizer does in the app.

    cd worker && uv run python tests/qa/flight_stage_qa.py --out <dir>

Writes <dir>/results.json (every scenario's evidence and verdict) and <dir>/rows/*.json
(the database rows, read as its owner). Needs worker/.env (Claude) and a local server
(`spacetime start`, default --server ui-check-3610 at 127.0.0.1:3610)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import SecretStr

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE.parents[1] / "workflow"))
import test_qa_flight_stage as offline  # noqa: E402  (fixtures, personas and the QA research)

from tandem_worker import serve as serve_module  # noqa: E402
from tandem_worker.adapters.apify import RunLedger  # noqa: E402
from tandem_worker.adapters.claude import ClaudeModel  # noqa: E402
from tandem_worker.adapters.spacetime import StdbHttp, some  # noqa: E402
from tandem_worker.budget import ProviderBudget  # noqa: E402
from tandem_worker.config import LiveLimits, load_settings  # noqa: E402
from tandem_worker.contracts import digest  # noqa: E402
from tandem_worker.live import CheckedResearch, Providers  # noqa: E402
from tandem_worker.optimizer_bridge import TandemOptimizerBridge  # noqa: E402
from tandem_worker.serve import ServeConfig, serve  # noqa: E402
from tandem_worker.simulate import TravellerAnswer, TravellerReply  # noqa: E402

NAMES = offline.NAMES
ORGANIZER = offline.PERSONAS["organizer"]
TABLES = ("room", "membership", "trip_brief", "intake", "private_message", "constraint_patch", "participant_constraint",
          "participant_rule", "participant_need", "search_fact", "participation_preferences", "profile_answer",
          "profile_calibration", "question", "job", "proposal", "proposal_decision", "participant_impact", "consent",
          "proposal_burden", "compromise_entry", "decision_event", "milestone")
SOLVES: list[dict] = []


class RecordingBridge(TandemOptimizerBridge):
    """The default chooser, unchanged; what it was asked and answered is kept for the report."""
    def solve(self, snapshot, briefs, candidates, rules=None):
        self.last_request = self.last_response = None
        result = super().solve(snapshot, briefs, candidates, rules=rules)
        SOLVES.append({"room": snapshot.room_id, "stage": list(snapshot.components), "input_revision": snapshot.input_revision,
                       "request": self.last_request, "response": self.last_response, "status": result.status,
                       "selected": list(result.selected), "burdens": result.burdens})
        return result


class RoomResearch:
    """The QA itineraries, per room (room C is the pricey-nonstop variant)."""
    def __init__(self):
        self.variants, self.requests = {}, []

    def search(self, request):
        self.requests.append(json.loads(json.dumps(request)))
        # Each search is a new observation, as a live one is: its own candidate ids (the database
        # refuses an id re-recorded with different facts).
        tag = f"{request['room_id']}-{request['purpose'].lower()}-{int(request['observed_at'] * 1000)}"
        found = offline.QAResearch(self.variants.get(str(request["room_id"]))).search(request)
        return [c.model_copy(update={"id": f"{c.id}-{tag}"}) for c in found]


class Persona:
    """Claude plays one persona after their QA message: only what they said and their profile summary."""
    def __init__(self, name, model):
        self.name, self.model, self.extra = name, model, {}

    def system(self, room):
        t = offline.traveller(self.name)
        said = t["replies"][0]["says"] + "".join(f' Later you also told your agent: "{x}"' for x in self.extra.get(room, ()))
        return (f"You are {self.name}, planning a group trip to Honolulu with friends ({', '.join(n for n in NAMES if n != self.name)}). "
                f"You're chatting privately with your own trip agent. Everything about your situation is what you already "
                f"told it: \"{said}\" Your confirmed profile summary: \"{t['summary']}\" Reply briefly by text, in your own "
                "voice, consistent with that. Never invent new needs, limits or preferences; if asked about something you "
                "haven't covered, say you have no strong preference.")

    def reply(self, room, conversation):
        context = {"conversation": [{"from": "agent" if m["sender"] == "AGENT" else "you", "text": m["body"]} for m in conversation]}
        return self.model._structured(context, TravellerReply, self.system(room) + " Write your next message.", "traveller").reply

    def answer(self, room, question):
        return self.model._structured({"question": question}, TravellerAnswer,
            self.system(room) + " Your agent asks a yes/no question; answer as you truly would.", "traveller").yes


def spacetime(*args):
    return subprocess.run([str(Path.home() / ".local/bin/spacetime"), *args], cwd=ROOT, capture_output=True, text=True, check=True)


def owner_token() -> str:
    return tomllib.loads((Path.home() / ".config/spacetime/cli.toml").read_text())["spacetimedb_token"]


class Run:
    def __init__(self, out: Path, server: str, url: str):
        self.out, self.url, self.log = out, url, []
        self.db = f"tandem-qa-{int(time.time())}"
        spacetime("publish", "--server", server, "--module-path", "spacetimedb", "--yes", self.db)
        self.http = StdbHttp(url, self.db)
        worker = self.http.mint_identity()
        spacetime("call", "--server", server, "--no-config", "-y", self.db, "add_worker",
                  json.dumps({"__identity__": "0x" + worker["identity"].removeprefix("0x")}), '"qa-worker"')
        self.owner = owner_token()
        self.settings = load_settings(ROOT / "worker" / ".env").model_copy(update={
            "stdb_url": url, "stdb_database": self.db, "stdb_worker_token": SecretStr(worker["token"])})
        self.config = ServeConfig(limits=LiveLimits(model_calls=500, actor_runs=1))
        key = self.settings.anthropic_api_key.get_secret_value()
        budget = ProviderBudget(out / "providers.sqlite", digest(["qa", self.db]), model_calls=500, actor_runs=1, daily=True)
        model = ClaudeModel(self.settings.claude_model, key, budget=budget, max_tokens=4096, timeout=30)
        self.research = RoomResearch()
        checked = CheckedResearch(self.research)
        checked.verified = True  # fixture itineraries: nothing paid runs, so no Apify preflight
        self.providers = Providers(model, checked, None, budget, RunLedger(out / "runs.sqlite"), self.settings.apify_actor_build)
        # The people are played by Claude too, on their own budget (an unbudgeted client stops at 4 calls).
        people = ProviderBudget(out / "people.sqlite", digest(["qa-people", self.db]), model_calls=400, actor_runs=1, daily=True)
        players = ClaudeModel(self.settings.claude_model, key, budget=people, max_tokens=1024, timeout=30)
        self.personas = {n: Persona(n, players) for n in NAMES}
        self.tokens = {n: self.http.mint_identity()["token"] for n in NAMES}
        self.rooms: dict[str, int] = {}
        serve_module.make_solver = lambda policy, time_limit=5: RecordingBridge()

    # ---------- people ----------

    def call(self, who, reducer, **args):
        self.http.call(self.tokens[who], reducer, **args)
        self.log.append({"t": round(time.time(), 1), "who": who, "call": reducer,
                         "args": {k: v for k, v in args.items() if k not in ("value_json",)}})

    def mine(self, who, view, room):
        return [r for r in self.http.view(self.tokens[who], view) if r.get("room_id") == room]

    def profiles(self):
        """Each persona's profile answers, then the summary their agent writes, which they confirm."""
        for user in offline.PROFILES["users"]:
            name = user["answers"]["display_name"]
            for qid, value in offline.by_question_id(user["answers"]).items():
                self.call(name, "save_profile_answer", question_id=qid, value_json=json.dumps(value))
            self.call(name, "request_profile_summary")
        for _ in range(20):
            serve(self.config, self.settings, self.out, rooms=set(), once=True, providers=self.providers, kinds={"PROFILE"})
            pending = []
            for name in NAMES:
                [c] = self.http.view(self.tokens[name], "my_profile_calibration") or [{}]
                if c.get("state") == "PROPOSED":
                    self.call(name, "confirm_profile_summary", profile_revision=c["profile_revision"])
                elif c.get("state") != "CONFIRMED":
                    pending.append(name)
            if not pending:
                return
        raise RuntimeError("profile summaries didn't arrive")

    def room(self, key, variant=None):
        self.call(ORGANIZER, "create_room", goal=f"Honolulu, Nov 20-24 (QA room {key})", display_name=ORGANIZER)
        room = max(self.http.view(self.tokens[ORGANIZER], "my_rooms"), key=lambda r: r["id"])
        for name in NAMES:
            if name != ORGANIZER:
                self.call(name, "join_room", invite_code=room["invite_code"], display_name=name)
        self.rooms[key], self.research.variants[str(room["id"])] = room["id"], variant
        self.call(ORGANIZER, "set_trip_description", room_id=room["id"], description=offline.PERSONAS["trip"]["description"])

    def act(self, key, name, accept_from=None):
        """What this person does now in this room, if anything. Returns whether they did something."""
        room, did = self.rooms[key], False
        if name == ORGANIZER:
            brief = (self.mine(name, "room_trip_brief", room) or [None])[0]
            if brief and brief["state"] == "PROPOSED":
                self.call(name, "confirm_trip_brief", room_id=room, revision=brief["revision"])
                return True
        intake = (self.mine(name, "my_intake", room) or [None])[0]
        messages = sorted(self.mine(name, "my_private_messages", room), key=lambda m: m["id"])
        if intake is None:
            if name == ORGANIZER and messages and messages[-1]["sender"] == "AGENT":  # a question about the brief
                self.call(name, "submit_private_message", room_id=room, body=self.personas[name].reply(room, messages))
                return True
            return False
        patches = self.mine(name, "my_constraint_patches", room)
        for p in patches:
            body = json.loads(p["patch_json"])
            if p["state"] == "PENDING" and body.get("key") == "origin_airport" and intake["state"] != "CONFIRMED":
                expected = offline.traveller(name)["origin"]
                if body["value"] == expected:
                    self.call(name, "confirm_origin", room_id=room, patch_id=p["id"])
                else:
                    self.call(name, "submit_private_message", room_id=room, body=f"No, I fly from {expected}.")
                did = True
        if did:
            return True
        if intake["state"] == "SUMMARY":
            pending = [p["id"] for p in patches if p["state"] == "PENDING"]
            [prefs] = self.http.view(self.tokens[name], "my_participation_preferences") or [{}]
            try:
                self.call(name, "confirm_intake", room_id=room, accepted_patch_ids=pending,
                          speak_up_for_me=some(prefs.get("speak_up_for_me", True)), ask_me_first=some(prefs.get("ask_me_first", False)),
                          give_me_time=some(prefs.get("give_me_time", True)), reminders_opted_in=None)
            except Exception as error:  # e.g. money missing: say so, as a person would
                self.log.append({"who": name, "room": key, "confirm_failed": str(error)[:200]})
                self.call(name, "submit_private_message", room_id=room, body=self.personas[name].reply(room, messages))
            return True
        if intake["state"] == "ASKING" and messages and messages[-1]["sender"] == "AGENT":
            spoke = any(m["sender"] == "HUMAN" for m in messages)
            body = self.personas[name].reply(room, messages) if spoke else offline.traveller(name)["replies"][0]["says"]
            self.call(name, "submit_private_message", room_id=room, body=body)
            return True
        if intake["state"] == "CONFIRMED":
            for p in patches:  # a change they asked for after onboarding, shown back to them: they confirm it
                if p["state"] == "PENDING":
                    self.call(name, "confirm_constraint_patch", patch_id=p["id"])
                    did = True
        for q in self.mine(name, "my_questions", room):
            if q["state"] == "OPEN":
                yes = self.personas[name].answer(room, q["prompt"])
                action = json.loads(q["action_json"])
                payload = ({"answer": yes, "constraint": action["nudge"]["yes" if yes else "no"]} if action.get("nudge")
                           else {"granted": yes} if q["kind"] in ("CONCESSION_CONSULTATION", "OBJECTION_DISCLOSURE", "BROADER_EXPLORATION")
                           else {"answer": yes})
                self.call(name, "answer_question", question_id=q["id"], version=q["version"], answer_json=json.dumps(payload))
                self.log.append({"room": key, "who": name, "kind": q["kind"], "asked": q["prompt"], "yes": yes})
                did = True
        if accept_from:
            for p in self.mine(name, "room_proposals", room):
                if p["state"] == "AWAITING_REVIEW" and p["is_reviewer"] and p["decision_id"] in accept_from:
                    mine = [c for c in self.mine(name, "my_consents", room) if c["proposal_id"] == p["id"] and c["response"] == "ACCEPTED"]
                    if not mine:
                        self.http.call(self.tokens[name], "respond_to_proposal", proposal_id=p["id"], version=p["version"],
                                       terms_digest=p["terms_digest"], response="ACCEPT", note=None)
                        did = True
        return did

    def settle(self, keys, until, accept=None, rounds=40):
        """Run the worker and let everyone act until `until(key)` holds for every room."""
        for n in range(rounds):
            results = serve(self.config, self.settings, self.out, rooms={self.rooms[k] for k in keys}, once=True,
                            providers=self.providers)
            self.log += [{"round": n, "job": r.get("job"), "kind": r.get("kind"), "outcome": r.get("outcome")} for r in results]
            with ThreadPoolExecutor(max_workers=8) as pool:
                acted = list(pool.map(lambda kn: self.act(kn[0], kn[1], (accept or {}).get(kn[0])),
                                      [(k, n) for k in keys for n in NAMES]))
            if all(until(k) for k in keys) and not any(acted) and not results:
                return True
        return False

    # ---------- what the room shows ----------

    def proposals(self, key, decision=None, state="AWAITING_REVIEW"):
        return [p for p in self.mine(ORGANIZER, "room_proposals", self.rooms[key])
                if (state is None or p["state"] == state) and (decision is None or p["decision_id"] == decision)]

    def events(self, key, kind):
        return [e for e in self.http.view(self.tokens[ORGANIZER], "room_events") if e["room_id"] == self.rooms[key] and e["type"] == kind]

    def rows(self, label):
        """Every table, read as the database's owner, saved for the report."""
        folder = self.out / "rows" / label
        folder.mkdir(parents=True, exist_ok=True)
        snap = {t: self.http.view(self.owner, t) for t in TABLES}
        for table, rows in snap.items():
            (folder / f"{table}.json").write_text(json.dumps(rows, indent=1, default=str))
        return snap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--server", default="ui-check-3610")
    parser.add_argument("--url", default="http://127.0.0.1:3610")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    run = Run(args.out, args.server, args.url)
    started = time.time()
    run.profiles()
    run.room("A")
    run.room("B")
    run.room("C", variant="bella_pricey_nonstop")
    planned = lambda k: bool(run.proposals(k, "flight")) or bool(run.events(k, "NO_FEASIBLE_PLAN"))  # noqa: E731
    ok_initial = run.settle("ABC", planned)
    run.rows("01-initial")

    # Room A: everyone agrees the flights; the stay comes next.
    flights_a = run.proposals("A", "flight")
    ok_stay = run.settle("A", lambda k: bool(run.proposals(k, "stay")), accept={"A": {"flight"}})
    run.rows("02-room-a-stay")
    stay_before = run.proposals("A", "stay")

    # QA-02 (room A) and QA-03 (room B), side by side.
    qa02, qa03 = offline.PERSONAS["changes"]["QA-02"], offline.traveller("Chris")["changes"]["QA-03"]
    run.personas["Alex"].extra[run.rooms["A"]], run.personas["Chris"].extra[run.rooms["B"]] = [qa02["says"]], [qa03["says"]]
    run.call("Alex", "submit_private_message", room_id=run.rooms["A"], body=qa02["says"])
    run.call("Chris", "submit_private_message", room_id=run.rooms["B"], body=qa03["says"])
    no_plan_b = len(run.events("B", "NO_FEASIBLE_PLAN"))
    changed = {"A": lambda k: any(p["id"] not in {s["id"] for s in stay_before} for p in run.proposals(k, "stay"))
               or len(run.events(k, "NO_FEASIBLE_PLAN")) > 0,
               "B": lambda k: len(run.events(k, "NO_FEASIBLE_PLAN")) > no_plan_b or bool(run.proposals(k, "flight"))}
    ok_changes = run.settle("AB", lambda k: changed[k](k))
    final = run.rows("03-after-changes")
    (args.out / "results.json").write_text(json.dumps({
        "database": run.db, "minutes": round((time.time() - started) / 60, 1), "settled": {
            "initial": ok_initial, "room_a_stay": ok_stay, "changes": ok_changes},
        "rooms": run.rooms, "flights_a": flights_a, "stay_before": stay_before,
        "proposals": {k: run.proposals(k, state=None) for k in run.rooms},
        "events": {k: [e for e in run.http.view(run.tokens[ORGANIZER], "room_events") if e["room_id"] == r] for k, r in run.rooms.items()},
        "conversations": {k: {n: sorted(run.mine(n, "my_private_messages", r), key=lambda m: m["id"]) for n in NAMES}
                          for k, r in run.rooms.items()},
        "solves": SOLVES, "research": run.research.requests, "log": run.log,
        "claude_calls": run.providers.budget.report()["used"]}, indent=1, default=str))
    print(json.dumps({"database": run.db, "rooms": run.rooms, "settled": [ok_initial, ok_stay, ok_changes],
                      "minutes": round((time.time() - started) / 60, 1), "rows": len(final["participant_rule"])}))


if __name__ == "__main__":
    main()
