"""After someone says no to every option: the question with ways forward, what picking one
does, and what's remembered for the worker across replans (worker/src/tandem_worker/recovery.py)."""

import json

from conftest import expect_error
from test_questions import answer, ask, park

BUDGET = {"key": "flight_budget_total", "value": 135000, "hardness": "SOFT", "unit": "USD_cents",
          "source_quote": "Flight: fare at most $1,350"}
CHOICES = [
    {"id": "change-1", "kind": "change", "label": "Flight: fare at most $1,350", "patches": [BUDGET]},
    {"id": "wider", "kind": "search", "label": "Look for more options", "search": {"wider": True}},
    {"id": "pause", "kind": "pause", "label": "Pause this for now"},
]
OPTION = {"decision_id": "flight", "key": "FLIGHT|alice|HA12026-11-20|True", "price_cents": 135000,
          "unmet": [{"category": "FLIGHT", "attribute": "total_cents", "operator": "lte", "value": 50000}]}


def recovery(room, worker, job_id, choices=None):
    return ask(room, worker, job_id, "alice", kind="PLAN_RECOVERY",
               action={"action_ref": "recover:flight:0", "choices": CHOICES if choices is None else choices,
                       "decision_id": "flight",
                       "rules_ref": "r1"})


def test_a_recovery_question_needs_real_choices(ready_room, client, worker):
    room, job_id = ready_room
    for bad in ([], [{"id": "a", "kind": "pause", "label": "x"}, {"id": "a", "kind": "pause", "label": "y"}],
                [{"id": "a", "kind": "guess", "label": "x"}], [{"id": "a", "kind": "change", "label": "x"}],
                [{"id": "a", "kind": "pause", "label": " "}]):
        expect_error("INVALID_ARGUMENT", recovery, room, worker, job_id, bad)


def test_picking_a_change_confirms_it_and_replans(ready_room, client, worker):
    room, job_id = ready_room
    q = recovery(room, worker, job_id)
    park(room, worker, job_id, [q["id"]])
    expect_error("INVALID_ARGUMENT", answer, room, "alice", q, {"choice": "something-else"})
    answer(room, "alice", q, {"choice": "change-1"})
    [budget] = [c for c in client.view(room.people["alice"], "my_constraints") if c["key"] == "flight_budget_total"]
    assert (json.loads(budget["value_json"]), budget["hardness"]) == (135000, "SOFT")
    [patch] = [p for p in client.view(room.people["alice"], "my_constraint_patches") if p["state"] == "CONFIRMED"]
    assert json.loads(patch["patch_json"]) == BUDGET, "kept like any confirmed change"
    assert job_id not in {j["id"] for j in room.jobs(worker)}, "the waiting plan is out of date"
    assert room.plan_job(worker) > job_id, "one replan with her change"


def test_pausing_or_searching_resumes_the_waiting_job(ready_room, client, worker):
    room, job_id = ready_room
    q = recovery(room, worker, job_id)
    park(room, worker, job_id, [q["id"]])
    answer(room, "alice", q, {"choice": "pause"})
    assert job_id in {j["id"] for j in room.jobs(worker)}, "the same plan picks up her answer"
    assert not [p for p in client.view(room.people["alice"], "my_constraint_patches") if p["state"] == "CONFIRMED"]


def test_what_people_said_no_to_and_picked_reaches_the_worker_across_replans(ready_room, client, worker):
    room, job_id = ready_room
    no = ask(room, worker, job_id, "alice", kind="CONCESSION_CONSULTATION", action={"action_ref": "draft-1", "option": OPTION})
    yes = ask(room, worker, job_id, "bob", kind="CONCESSION_CONSULTATION", action={"action_ref": "draft-1", "option": OPTION})
    q = recovery(room, worker, job_id)
    expect_error("INVALID_ARGUMENT", answer, room, "alice", no, {"granted": False, "reason": 42})
    answer(room, "alice", no, {"granted": False, "reason": "Too pricey"})
    answer(room, "alice", q, {"choice": "wider"})
    answer(room, "bob", yes, {"granted": True})
    room.set_constraint("bob", "flight_budget_total", 70000)  # someone else's change: a new plan
    later = room.plan_job(worker)
    room.claim(worker, later)
    context = room.context(worker, later)
    assert context["declines"] == [{"question_id": no["id"], "member_id": room.member_ids["alice"], "option": OPTION,
                                    "reason": "Too pricey"}], "only the no, with her why"
    [picked] = context["recoveries"]
    assert (picked["member_id"], picked["choice"], picked["action"]["decision_id"]) == (room.member_ids["alice"], "wider", "flight")


def test_her_agent_sees_the_open_question_when_she_replies_in_chat(ready_room, client, worker):
    room, job_id = ready_room
    q = recovery(room, worker, job_id)
    client.call(room.people["alice"], "submit_private_message", room_id=room.room_id, body="Not sure, honestly.")
    [interpret] = room.jobs(worker, kind="INTERPRET")
    room.claim(worker, interpret["id"])
    subject = room.context(worker, interpret["id"])["subject"]
    assert subject["open_recovery"] == {"prompt": q["prompt"], "choices": CHOICES}
