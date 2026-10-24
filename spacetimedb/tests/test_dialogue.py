"""Threaded agent dialogue rules (P01, P03, V04, T02–T04, U07, C05)."""

import json
import time

from conftest import PEOPLE, Room, expect_error, key, make_room
from stdb_client import some


def thread_open(room: Room, *names: str, budget=None):
    ids = [room.mediator_id] + [room.agent_ids[n] for n in names]
    return some({"topic_ref": "topic:arrival", "participant_agent_ids": ids, "turn_budget": some(budget) if budget else None,
                 "successor_of": None})


def threads(room: Room, job_id: int) -> list[dict]:
    return [t for t in room.client.view(room.people["alice"], "room_threads") if t["job_id"] == job_id]


def test_room_message_is_visible_to_everyone(ready_room, client, worker):
    room, job_id = ready_room
    args = room.send(worker, job_id)
    for name in PEOPLE:
        chat = [m for m in client.view(room.people[name], "room_messages") if m["room_id"] == room.room_id]
        assert [m["utterance"] for m in chat] == [args["utterance"]]
    assert "payload_json" not in client.view(room.people["bob"], "room_messages")[0]


def test_v04_owner_private_messages_reach_only_the_owner(ready_room, client, worker):
    room, job_id = ready_room
    room.send(worker, job_id, sender_agent_id=room.agent_ids["alice"], recipient_agent_id=room.mediator_id,
              type="EVALUATE", audience="OWNER_PRIVATE", utterance="This keeps you under your cap.")
    assert [m["utterance"] for m in client.view(room.people["alice"], "my_agent_messages")] == [
        "This keeps you under your cap."]
    for name in ["bob", "carol", "dave"]:
        assert client.view(room.people[name], "my_agent_messages") == []
    for name in PEOPLE:
        assert client.view(room.people[name], "room_messages") == []
    expect_error("NOT_AUTHORIZED", room.send, worker, job_id, sender_agent_id=room.agent_ids["alice"],
                 recipient_agent_id=room.mediator_id, audience="OWNER_PRIVATE",
                 audience_member_id=some(room.member_ids["bob"]))


def test_p01_unconfirmed_preferences_block_room_speech(room: Room, client, worker):
    for name, cap in zip(PEOPLE, [50000, 65000, 65000, 65000]):
        room.set_constraint(name, "flight_budget_total", cap)
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    # Nothing room-visible involving alice until she confirms her settings,
    # whether her agent speaks or the mediator addresses her in the room.
    expect_error("PREFERENCES_UNCONFIRMED", room.send, worker, job_id, sender_agent_id=room.agent_ids["alice"],
                 recipient_agent_id=room.mediator_id, type="EVALUATE")
    expect_error("PREFERENCES_UNCONFIRMED", room.send, worker, job_id)
    # Internal coordination can still happen.
    room.send(worker, job_id, audience="INTERNAL")


def test_p03_room_objection_needs_speak_up_or_a_grant_for_this_draft(room: Room, client, worker):
    room.confirm_all(speak_up=False, ask_first=True, give_time=True)
    room.set_constraint("alice", "flight_budget_total", 50000)
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    objection = dict(sender_agent_id=room.agent_ids["alice"], recipient_agent_id=room.mediator_id, type="OBJECT",
                     utterance="One participant's requirements aren't met by this option.")
    expect_error("CONSULTATION_REQUIRED", room.send, worker, job_id, **objection)

    request_key = key("q")
    client.call(worker, "ask_owner", job_id=job_id, attempt=room.leases[job_id], request_key=request_key,
                member_id=room.member_ids["alice"], kind="OBJECTION_DISCLOSURE",
                action_json=json.dumps({"action_ref": "draft-1"}), prompt="May I raise this objection?",
                answer_schema_json="{}", thread_id=None)
    q = next(q for q in client.view(room.people["alice"], "my_questions") if q["request_key"] == request_key)
    client.call(room.people["alice"], "answer_question", question_id=q["id"], version=q["version"],
                answer_json=json.dumps({"granted": True}))
    # A granted permission changes what the agent may do: new generation.
    expect_error("STALE_SNAPSHOT", room.send, worker, job_id, **objection)
    new_job = room.plan_job(worker)
    room.claim(worker, new_job)
    room.send(worker, new_job, **objection)
    # The grant covers only that draft.
    expect_error("CONSULTATION_REQUIRED", room.send, worker, new_job,
                 payload_json=json.dumps({"draft_ref": "draft-2"}), draft_ref=some("draft-2"), **objection)


def test_concession_and_wider_search_need_grants(room: Room, client, worker):
    room.confirm_all(speak_up=True, ask_first=True, give_time=True)
    room.set_constraint("alice", "flight_budget_total", 50000)
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    from_alice = dict(sender_agent_id=room.agent_ids["alice"], recipient_agent_id=room.mediator_id,
                      type="COUNTERPROPOSE")
    expect_error("CONSULTATION_REQUIRED", room.send, worker, job_id,
                 payload_json=json.dumps({"draft_ref": "draft-1", "concession": True}), **from_alice)
    expect_error("CONSULTATION_REQUIRED", room.send, worker, job_id,
                 payload_json=json.dumps({"draft_ref": "draft-1", "exceeds_exploration": True}), **from_alice)
    room.send(worker, job_id, **from_alice)


def test_private_reason_text_is_blocked_from_the_room(room: Room, client, worker):
    room.confirm_all()
    room.set_constraint("alice", "flight_budget_total", 50000, rationale="saving for my sister's wedding")
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    expect_error("PRIVATE_CONTENT_DETECTED", room.send, worker, job_id,
                 utterance="Alice is SAVING FOR MY SISTER'S WEDDING so this is too expensive.")
    room.send(worker, job_id, utterance="One participant needs a cheaper option.")


def test_claim_refs_must_resolve(ready_room, client, worker):
    room, job_id = ready_room
    expect_error("UTTERANCE_PAYLOAD_MISMATCH", room.send, worker, job_id, claim_refs=["cand_sfo_1005"])
    client.call(worker, "record_candidates", job_id=job_id, attempt=room.leases[job_id], request_key=key(), candidates=[{
        "candidate_ref": "cand_sfo_1005", "candidate_set_version": 1, "category": "FLIGHT", "mode": "FIXTURE",
        "source_url": "fixture://flights/sfo", "observed_at": "2026-10-24T09:00:00Z",
        "facts_json": json.dumps({"price_cents": 48000}), "missing_fields": ["baggage"]}])
    room.send(worker, job_id, claim_refs=["cand_sfo_1005"], evidence_refs=["draft:draft-1"])
    # Field citations resolve through their record; unknown records or odd fields don't.
    room.send(worker, job_id, claim_refs=["cand_sfo_1005#price", "draft:draft-1#arrival_window"])
    expect_error("UTTERANCE_PAYLOAD_MISMATCH", room.send, worker, job_id, claim_refs=["cand_nope#price"])
    expect_error("UTTERANCE_PAYLOAD_MISMATCH", room.send, worker, job_id, claim_refs=["cand_sfo_1005#Price!"])
    candidates = client.view(room.people["dave"], "room_candidates")
    assert [c["candidate_ref"] for c in candidates] == ["cand_sfo_1005"]


def test_t02_routing_rules(ready_room, client, worker):
    room, job_id = ready_room
    other = make_room(client)
    expect_error("NOT_AUTHORIZED", room.send, worker, job_id, recipient_agent_id=other.agent_ids["bob"])
    expect_error("INVALID_ARGUMENT", room.send, worker, job_id, open_thread=None)
    room.send(worker, job_id)
    thread_id = threads(room, job_id)[0]["id"]
    expect_error("NOT_AUTHORIZED", room.send, worker, job_id, open_thread=None, thread_id=some(thread_id),
                 recipient_agent_id=room.agent_ids["carol"])
    expect_error("NOT_AUTHORIZED", room.send, worker, job_id, recipient_agent_id=room.mediator_id)


def test_t03_thread_and_round_budgets(ready_room, client, worker):
    room, job_id = ready_room
    room.send(worker, job_id, open_thread=thread_open(room, "alice", budget=2))
    thread_id = threads(room, job_id)[0]["id"]
    room.send(worker, job_id, open_thread=None, thread_id=some(thread_id),
              sender_agent_id=room.agent_ids["alice"], recipient_agent_id=room.mediator_id, type="EVALUATE")
    thread = threads(room, job_id)[0]
    assert (thread["state"], thread["closure_reason"], thread["turns_used"]) == ("CLOSED", "TURN_BUDGET", 2)
    expect_error("TURN_BUDGET_EXHAUSTED", room.send, worker, job_id, open_thread=None, thread_id=some(thread_id))
    # Round budget: 24 turns across threads, then nothing more in that round.
    for i in range(22):
        name = PEOPLE[i % 4]
        room.send(worker, job_id, round_id="round-1", open_thread=thread_open(room, name),
                  recipient_agent_id=room.agent_ids[name])
    expect_error("TURN_BUDGET_EXHAUSTED", room.send, worker, job_id, round_id="round-1")
    room.send(worker, job_id, round_id="round-2")


def test_t04_c05_duplicate_delivery_charges_once(ready_room, client, workers):
    room, job_id = ready_room
    first, second = workers
    args = room.send(first, job_id)
    client.call(first, "submit_agent_message", **args)  # duplicate delivery
    assert len(client.view(room.people["alice"], "room_messages")) == 1
    assert threads(room, job_id)[0]["turns_used"] == 1
    expect_error("KEY_BODY_MISMATCH", client.call, first, "submit_agent_message", **{**args, "utterance": "changed"})
    # A replacement attempt replaying the same logical message gets the original receipt.
    client.call(first, "renew_lease", job_id=job_id, attempt=room.leases[job_id], lease_seconds=2)
    time.sleep(3)
    attempt = client.procedure(second, "claim_job", job_id=some(job_id), lease_seconds=30, request_key=key())["attempt"]
    client.call(second, "submit_agent_message", **{**args, "attempt": attempt})
    assert len(client.view(room.people["alice"], "room_messages")) == 1


def test_u07_unsupported_schema_mutates_nothing(ready_room, client, worker):
    room, job_id = ready_room
    expect_error("UNSUPPORTED_SCHEMA", room.send, worker, job_id, schema_version=1)
    assert threads(room, job_id) == []
    assert client.view(room.people["alice"], "room_messages") == []


def test_c05_preference_edit_mid_run_rejects_messages(ready_room, client, worker):
    room, job_id = ready_room
    room.send(worker, job_id)
    client.call(room.people["carol"], "confirm_participation_preferences",
                speak_up_for_me=False, ask_me_first=True, give_me_time=True, reminders_opted_in=False)
    expect_error("STALE_SNAPSHOT", room.send, worker, job_id)
    assert room.plan_job(worker) > job_id


def test_message_processing_receipts(ready_room, client, worker):
    room, job_id = ready_room
    room.send(worker, job_id)
    msgs = room.context(worker, job_id)["messages"]
    propose = msgs[0]
    thread_id = propose["thread_id"]
    room.send(worker, job_id, open_thread=None, thread_id=some(thread_id), sender_agent_id=room.agent_ids["alice"],
              recipient_agent_id=room.mediator_id, type="EVALUATE", reply_to=some(propose["message_id"]),
              utterance="This works for me.")
    reply = room.context(worker, job_id)["messages"][1]
    record = dict(job_id=job_id, attempt=room.leases[job_id], message_id=propose["message_id"],
                  recipient_agent_id=room.agent_ids["alice"], reply_ids=[reply["message_id"]])
    client.call(worker, "record_message_processed", **record)
    client.call(worker, "record_message_processed", **record)
    expect_error("KEY_BODY_MISMATCH", client.call, worker, "record_message_processed", **{**record, "reply_ids": []})
    expect_error("NOT_AUTHORIZED", client.call, worker, "record_message_processed",
                 **{**record, "recipient_agent_id": room.mediator_id})
    processed = room.context(worker, job_id)["messages"][0]
    assert processed["processing_state"] == "PROCESSED" and processed["processed_reply_ids"] == [reply["message_id"]]


def python_digest(content: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_validation_digest_matches_python_byte_for_byte(ready_room, client, worker):
    room, job_id = ready_room
    payload = {"draft_ref": "draft-1", "evaluation": "REQUESTED", "candidate_refs": [], "research": False}
    utterance = "Café option — lands 13:05 ✈️ \"quoted\" \\ back\\slash"
    content = {"type": "PROPOSE", "utterance": utterance, "payload": payload, "claim_refs": ["act:PROPOSE"],
               "evidence_refs": []}
    args = dict(payload_json=json.dumps(payload), utterance=utterance, claim_refs=["act:PROPOSE"])
    room.send(worker, job_id, validation_digest=some(python_digest(content)), **args)
    tampered = {**content, "utterance": utterance + "!"}
    expect_error("INVALID_VALIDATION_METADATA", room.send, worker, job_id,
                 validation_digest=some(python_digest(tampered)), **args)


def test_reply_must_answer_a_message_to_the_sender_in_the_same_thread(ready_room, client, worker):
    room, job_id = ready_room
    room.send(worker, job_id)
    ctx = room.context(worker, job_id)
    propose = ctx["messages"][0]
    # The mediator can't "reply" to its own message.
    expect_error("INVALID_REPLY", room.send, worker, job_id, open_thread=None, thread_id=some(propose["thread_id"]),
                 reply_to=some(propose["message_id"]))
    room.send(worker, job_id, open_thread=None, thread_id=some(propose["thread_id"]),
              sender_agent_id=room.agent_ids["alice"], recipient_agent_id=room.mediator_id, type="EVALUATE",
              reply_to=some(propose["message_id"]))


def test_evidence_and_size_limits(ready_room, client, worker):
    room, job_id = ready_room
    expect_error("INVALID_EVIDENCE_REF", room.send, worker, job_id, evidence_refs=["cand_missing"])
    expect_error("INVALID_EVIDENCE_REF", room.send, worker, job_id,
                 payload_json=json.dumps({"draft_ref": "draft-1", "candidate_refs": ["cand_missing"]}))
    expect_error("PAYLOAD_TOO_LARGE", room.send, worker, job_id, utterance="x" * 4097)
