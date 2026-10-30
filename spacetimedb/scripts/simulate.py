"""Fixture simulator: plays the worker's part so the frontend and demo can run
before the real LangGraph worker exists. Uses FIXTURE flights and scripted
utterances; every write goes through the same backend rules as the real worker.

  # create a room with simulated people; join it from the app with the invite code
  python simulate.py seed --people 3 --participants-file /private/tmp/test-participants.json

  # act as the worker for a room: agents talk, activity changes, a plan is published
  STDB_WORKER_TOKEN=... python simulate.py negotiate --room 1

  # rehearse a crash: claim with a short lease and stop mid-negotiation;
  # running `negotiate` again afterwards shows another worker taking over
  STDB_WORKER_TOKEN=... python simulate.py negotiate --room 1 --crash-after 2

Defaults: --url http://127.0.0.1:3000 --db tandem-dev (or STDB_URL / STDB_DB).
The worker token's identity must be registered with add_worker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from stdb_client import Client, Identity, StdbError, some  # noqa: E402

SIM_NAMES = ["Sam", "Riley", "Jordan", "Morgan", "Casey"]
BUDGETS = [50000, 65000, 65000, 65000, 60000]


def key(step: str) -> str:
    return f"sim:{step}:{uuid.uuid4().hex[:10]}"


def seed(client: Client, people: int, participants_file: Path) -> None:
    # Use already authenticated, invited test accounts. Seeding never bypasses
    # registration or grants access, and the file must be kept private.
    tokens = json.loads(participants_file.read_text())
    if not isinstance(tokens, list) or len(tokens) != people or not all(isinstance(t, str) and t for t in tokens):
        raise SystemExit("participants file must contain one authenticated test participant token per person")
    sims = [Identity(hex="", token=token) for token in tokens]
    for sim in sims:
        access = client.view(sim, "my_app_access")
        if not access or not access[0]["allowed"]:
            raise SystemExit("test participant must have an active invited account session")
    client.call(sims[0], "create_room", goal="Hawaii trip (simulated)", display_name=f"{SIM_NAMES[0]} (sim)")
    room = max(client.view(sims[0], "my_rooms"), key=lambda r: r["id"])
    for i, sim in enumerate(sims[1:], start=1):
        client.call(sim, "join_room", invite_code=room["invite_code"], display_name=f"{SIM_NAMES[i]} (sim)")
    for i, sim in enumerate(sims):
        client.call(sim, "confirm_participation_preferences",
                    speak_up_for_me=i != 0, ask_me_first=i == 0, give_me_time=True, reminders_opted_in=False)
        client.call(sim, "set_constraint", room_id=room["id"], key="flight_budget_total",
                    value_json=json.dumps(BUDGETS[i]), unit="USD_cents", hardness="HARD" if i == 0 else "SOFT",
                    authorized_range_json=None, private_rationale=None)
    print(f"room_id={room['id']} invite_code={room['invite_code']}")
    print("Join from the app with the invite code, then run `negotiate --room "
          f"{room['id']}` with a registered worker token.")


class Run:
    def __init__(self, client: Client, worker: Identity, room_id: int, pace: float, lease: int):
        self.client, self.worker, self.room_id, self.pace, self.lease = client, worker, room_id, pace, lease
        self.job_id = 0
        self.attempt = 0

    def wait_and_claim(self, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            jobs = [j for j in self.client.view(self.worker, "worker_pending_jobs")
                    if j["room_id"] == self.room_id and j["kind"] == "PLAN"]
            if jobs:
                job = max(jobs, key=lambda j: j["id"])
                claim = self.client.procedure(self.worker, "claim_job", job_id=some(job["id"]),
                                              lease_seconds=self.lease, request_key=key("claim"))
                self.job_id, self.attempt = claim["job_id"], claim["attempt"]
                print(f"claimed job {self.job_id} (attempt {self.attempt})")
                return
            time.sleep(1)
        raise SystemExit(f"no pending PLAN job for room {self.room_id}; set or change a constraint first")

    def call(self, reducer: str, **args) -> None:
        self.client.call(self.worker, reducer, job_id=self.job_id, attempt=self.attempt, **args)
        time.sleep(self.pace)

    def activity(self, agent_id: int, activity: str, thread_id: int | None = None) -> None:
        self.client.call(self.worker, "set_agent_activity", job_id=self.job_id, attempt=self.attempt,
                         agent_id=agent_id, activity=activity, thread_id=some(thread_id) if thread_id else None)

    def renew(self) -> None:
        self.client.call(self.worker, "renew_lease", job_id=self.job_id, attempt=self.attempt,
                         lease_seconds=self.lease)

    def message(self, *, sender: int, recipient: int, type_: str, utterance: str, audience: str,
                thread_id: int | None, participants: list[int] | None, refs: list[str], round_id: str = "round-1",
                reply_to: int | None = None, audience_member_id: int | None = None) -> None:
        open_thread = None if thread_id else some({"topic_ref": "topic:flights", "participant_agent_ids": participants,
                                                   "turn_budget": None, "successor_of": None})
        self.call("submit_agent_message", message_key=key("msg"), schema_version=2, round_id=round_id,
                  thread_id=some(thread_id) if thread_id else None, open_thread=open_thread,
                  sender_agent_id=sender, recipient_agent_id=recipient, type=type_,
                  reply_to=some(reply_to) if reply_to else None, draft_ref=some("draft-sim-1"),
                  proposal_id=None, proposal_version=None,
                  payload_json=json.dumps({"category": "flight", "draft_ref": "draft-sim-1", "candidate_refs": refs}),
                  utterance=utterance, claim_refs=refs, evidence_refs=[], audience=audience,
                  audience_member_id=some(audience_member_id) if audience_member_id else None,
                  sanitization_status="TEMPLATE_FALLBACK", validation_digest=None)


def negotiate(client: Client, worker: Identity, room_id: int, pace: float, crash_after: int | None) -> None:
    run = Run(client, worker, room_id, pace, lease=8 if crash_after else 60)
    run.wait_and_claim()
    ctx = client.procedure(worker, "load_execution_context", job_id=run.job_id, attempt=run.attempt)
    mediator = ctx["mediator_agent_id"]
    members = [m for m in ctx["members"] if m["active"]]
    budgets = {c["member_id"]: c["value"] for c in ctx["constraints"]
               if c["key"] == "flight_budget_total" and isinstance(c["value"], (int, float))}

    run.activity(mediator, "SEARCHING")
    run.call("emit_event", request_key=key("event"), type="INITIAL_SEARCH_STARTED", member_id=None,
             payload_json=json.dumps({"mode": "FIXTURE"}))
    unconfirmed = [m["display_name"] for m in members if not m["participation_preferences"]["confirmed"]]
    if unconfirmed:
        raise SystemExit(f"waiting for {', '.join(unconfirmed)} to confirm participation settings in the app")
    now = time.time()
    candidates = []
    for i, m in enumerate(members):
        price = int(min(budgets.get(m["member_id"], 60000) - 2000, 60000))
        # flight_cost_v1 facts: the backend re-checks these when the plan is published.
        facts = {"owner_member_id": m["member_id"], "total_cents": price, "currency": "USD",
                 "arrival_epoch": int(now) + 86400 + i * 600, "observed_at": now - 5, "valid_until": now + 3600,
                 "baggage_included": True, "cost_basis": "COMPLETE_FLIGHT_TOTAL"}
        candidates.append({"candidate_ref": f"sim_flight_{m['member_id']}", "candidate_set_version": 1,
                           "category": "FLIGHT", "mode": "FIXTURE", "source_url": "fixture://simulator",
                           "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                           "facts_json": json.dumps(facts), "missing_fields": []})
    run.call("record_candidates", request_key=key("cands"), candidates=candidates)
    run.activity(mediator, "THINKING")

    sent = 0
    for m in members:
        agent, ref = m["agent_id"], f"sim_flight_{m['member_id']}"
        run.activity(mediator, "WRITING")
        run.message(sender=mediator, recipient=agent, type_="PROPOSE", audience="ROOM", thread_id=None,
                    participants=[mediator, agent], refs=[ref],
                    utterance=f"{m['display_name']}'s agent, here's a flight that lands with the group. Does it work?")
        thread = max(t["thread_id"] for t in client.procedure(
            worker, "load_execution_context", job_id=run.job_id, attempt=run.attempt)["threads"])
        sent += 1
        if crash_after and sent >= crash_after:
            print(f"simulated crash: stopping without releasing job {run.job_id}; its lease expires in ~8s")
            return
        run.activity(agent, "THINKING", thread)
        run.renew()
        run.activity(agent, "WRITING", thread)
        run.message(sender=agent, recipient=mediator, type_="EVALUATE", audience="ROOM",
                    thread_id=thread, participants=None, refs=[ref], utterance="This works for my person.")
        run.activity(agent, "IDLE")

    run.activity(mediator, "CHECKING")
    terms = {"flights": {str(m["member_id"]): f"sim_flight_{m['member_id']}" for m in members}, "mode": "FIXTURE"}
    published = client.procedure(
        worker, "publish_proposal", job_id=run.job_id, attempt=run.attempt, request_key=key("publish"),
        stage="TRAVEL", decision_id="trip", scope="COMPLETE_PLAN", terms_json=json.dumps(terms, sort_keys=True, separators=(",", ":")),
        shared_summary_json=json.dumps({"headline": "Everyone lands within an hour of each other (simulated flights)"}),
        validation_json=json.dumps({"validator": "simulator", "hard_constraints": "PASS"}),
        evidence_refs=[c["candidate_ref"] for c in candidates], affected_member_ids=[],
        selected_candidate_refs=[c["candidate_ref"] for c in candidates],
        impacts=[{"member_id": m["member_id"],
                  "impact_json": json.dumps({"cost_cents": json.loads(candidates[i]["facts_json"])["total_cents"]})}
                 for i, m in enumerate(members)],
        burdens=[])
    print(f"published proposal {published['proposal_id']} v{published['version']}; approve it from the app")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("STDB_URL", "http://127.0.0.1:3000"))
    parser.add_argument("--db", default=os.environ.get("STDB_DB", "tandem-dev"))
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--people", type=int, default=3, choices=range(1, 6))
    s.add_argument("--participants-file", type=Path, required=True, help="private JSON array of authenticated invited test participant tokens")
    n = sub.add_parser("negotiate")
    n.add_argument("--room", type=int, required=True)
    n.add_argument("--pace", type=float, default=1.5, help="seconds between steps")
    n.add_argument("--crash-after", type=int, default=None, help="stop after N proposals to rehearse a takeover")
    args = parser.parse_args()

    client = Client(args.url, args.db)
    try:
        if args.command == "seed":
            seed(client, args.people, args.participants_file)
        else:
            token = os.environ.get("STDB_WORKER_TOKEN")
            if not token:
                raise SystemExit("set STDB_WORKER_TOKEN to a token registered with add_worker")
            negotiate(client, Identity(hex="", token=token), args.room, args.pace, args.crash_after)
    except StdbError as e:
        raise SystemExit(f"backend refused: {e.code}: {e.detail}") from e


if __name__ == "__main__":
    main()
