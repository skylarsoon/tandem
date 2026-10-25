"""Publication, consent and agreement (G01 backend side, C01, C03)."""

import json

from conftest import PEOPLE, Room, expect_error, key
from stdb_client import some


def respond(room: Room, name: str, published: dict, response: str = "ACCEPT", **overrides) -> None:
    args = dict(proposal_id=published["proposal_id"], version=published["version"],
                terms_digest=published["terms_digest"], response=response, note=None)
    args.update(overrides)
    room.client.call(room.people[name], "respond_to_proposal", **args)


def counts(room: Room, name: str, proposal_id: int) -> dict:
    return next(c for c in room.client.view(room.people[name], "room_consent_counts")
                if c["proposal_id"] == proposal_id)


def milestone(room: Room, stage: str = "TRAVEL") -> dict:
    return next(m for m in room.client.view(room.people["alice"], "room_milestones")
                if m["room_id"] == room.room_id and m["stage"] == stage)


def test_full_agreement_cycle(ready_room, client, worker):
    room, job_id = ready_room
    room.send(worker, job_id)
    published = room.publish(worker, job_id)
    assert published["version"] == 1

    proposals = [p for p in client.view(room.people["bob"], "room_proposals") if p["room_id"] == room.room_id]
    assert [(p["state"], p["reviewer_count"], p["is_reviewer"]) for p in proposals] == [("AWAITING_REVIEW", 4, True)]
    assert "validation_json" not in proposals[0]
    assert milestone(room)["state"] == "IN_REVIEW"
    assert job_id not in {j["id"] for j in room.jobs(worker)}
    # Publishing ends the job: the worker can't keep writing under it.
    expect_error("LEASE_LOST", room.send, worker, job_id)

    for name in PEOPLE[:3]:
        respond(room, name, published)
    c = counts(room, "dave", published["proposal_id"])
    assert (c["required"], c["accepted"], c["pending"]) == (4, 3, 1)
    assert milestone(room)["state"] == "IN_REVIEW", "three of four is not agreement"

    respond(room, "dave", published)
    agreed = next(p for p in client.view(room.people["alice"], "room_proposals") if p["id"] == published["proposal_id"])
    assert agreed["state"] == "AGREED"
    assert milestone(room)["state"] == "AGREED"

    client.call(room.people["carol"], "withdraw_consent", proposal_id=published["proposal_id"], note=None)
    reopened = next(p for p in client.view(room.people["alice"], "room_proposals") if p["id"] == published["proposal_id"])
    assert reopened["state"] == "AWAITING_REVIEW"
    assert milestone(room)["state"] == "IN_REVIEW"
    assert room.jobs(worker, kind="INTERPRET"), "withdrawal asks carol's agent to follow up"


def test_publish_is_idempotent(ready_room, client, worker):
    room, job_id = ready_room
    request_key = key("publish")
    first = room.publish(worker, job_id, request_key=request_key)
    again = room.publish(worker, job_id, request_key=request_key)
    assert first == again
    assert len([p for p in client.view(room.people["alice"], "room_proposals") if p["room_id"] == room.room_id]) == 1


def test_impacts_are_private(ready_room, client, worker):
    room, job_id = ready_room
    published = room.publish(worker, job_id)
    for name in PEOPLE:
        impacts = [i for i in client.view(room.people[name], "my_proposal_impacts")
                   if i["proposal_id"] == published["proposal_id"]]
        assert len(impacts) == 1


def test_c03_stale_or_wrong_terms_never_count(ready_room, client, worker):
    room, job_id = ready_room
    published = room.publish(worker, job_id)
    expect_error("STALE_SNAPSHOT", respond, room, "alice", published, terms_digest="0" * 64)
    expect_error("STALE_SNAPSHOT", respond, room, "alice", published, version=published["version"] + 1)
    respond(room, "alice", published)

    room.set_constraint("bob", "flight_budget_total", 60000)
    expect_error("STALE_SNAPSHOT", respond, room, "carol", published)
    consents = {c["response"] for c in client.view(room.people["alice"], "my_consents")
                if c["proposal_id"] == published["proposal_id"]}
    assert consents == {"STALE"}
    superseded = next(p for p in client.view(room.people["alice"], "room_proposals") if p["id"] == published["proposal_id"])
    assert superseded["state"] == "SUPERSEDED"
    assert milestone(room)["state"] == "REOPENED"


def test_declines_and_decide_later(ready_room, client, worker):
    room, job_id = ready_room
    published = room.publish(worker, job_id)
    respond(room, "alice", published, "DECIDE_LATER")
    respond(room, "bob", published, "NOT_COMFORTABLE", note=some("arrival is too late for me"))
    respond(room, "carol", published)
    respond(room, "dave", published)
    c = counts(room, "carol", published["proposal_id"])
    assert (c["accepted"], c["declined"], c["pending"]) == (2, 1, 1)
    mine = next(c for c in client.view(room.people["alice"], "my_consents") if c["proposal_id"] == published["proposal_id"])
    assert mine["response"] == "PENDING" and mine["decide_later_available"] is True
    bob_private = [m["body"] for m in client.view(room.people["bob"], "my_private_messages")]
    assert any("NOT_COMFORTABLE" in body and "too late" in body for body in bob_private)
    assert all("too late" not in json.dumps(e) for e in client.view(room.people["carol"], "room_events"))
    expect_error("INVALID_STATE", client.call, room.people["alice"], "withdraw_consent",
                 proposal_id=published["proposal_id"], note=None)


def test_newer_version_supersedes_older(ready_room, client, worker):
    room, job_id = ready_room
    first = room.publish(worker, job_id)
    room.confirm_all()  # new permission revision → new generation
    new_job = room.plan_job(worker)
    room.claim(worker, new_job)
    second = room.publish(worker, new_job, terms={"flights": {"alice": "UA9"}})
    assert second["version"] == first["version"] + 1
    states = {p["id"]: p["state"] for p in client.view(room.people["alice"], "room_proposals")}
    assert states[first["proposal_id"]] == "SUPERSEDED" and states[second["proposal_id"]] == "AWAITING_REVIEW"


def test_outsider_and_worker_cannot_respond(ready_room, client, worker):
    room, job_id = ready_room
    published = room.publish(worker, job_id)
    outsider = client.mint_identity()
    expect_error("NOT_A_MEMBER", client.call, outsider, "respond_to_proposal", proposal_id=published["proposal_id"],
                 version=published["version"], terms_digest=published["terms_digest"], response="ACCEPT", note=None)
    expect_error("NOT_AUTHORIZED", client.call, worker, "respond_to_proposal", proposal_id=published["proposal_id"],
                 version=published["version"], terms_digest=published["terms_digest"], response="ACCEPT", note=None)


def record_flights(room: Room, worker, job_id: int, overrides: dict | None = None) -> list[str]:
    """One fresh flight per member under their cap, arriving 10 minutes apart."""
    import time as _time
    now = _time.time()
    caps = {"alice": 50000, "bob": 65000, "carol": 65000, "dave": 65000}
    candidates, refs = [], []
    for i, name in enumerate(PEOPLE):
        facts = {"owner_member_id": room.member_ids[name], "total_cents": caps[name] - 1000, "currency": "USD",
                 "arrival_epoch": int(now) + 86400 + i * 600, "observed_at": now - 5, "valid_until": now + 3600,
                 "baggage_included": True, "cost_basis": "COMPLETE_FLIGHT_TOTAL"}
        facts.update((overrides or {}).get(name, {}))
        ref = f"flight-{name}-{key()}"
        refs.append(ref)
        candidates.append({"candidate_ref": ref, "candidate_set_version": 1, "category": "FLIGHT", "mode": "FIXTURE",
                           "source_url": "fixture://test", "observed_at": "2026-10-24T09:00:00Z",
                           "facts_json": json.dumps(facts), "missing_fields": []})
    room.client.call(worker, "record_candidates", job_id=job_id, attempt=room.leases[job_id], request_key=key(),
                     candidates=candidates)
    return refs


def publish_selected(room: Room, worker, job_id: int, refs: list[str], scope: str = "COMPLETE_PLAN",
                     decision_id: str = "trip", stage: str = "TRAVEL") -> dict:
    return room.client.procedure(
        worker, "publish_proposal", job_id=job_id, attempt=room.leases[job_id], request_key=key("publish"),
        stage=stage, decision_id=decision_id, scope=scope, terms_json=json.dumps({"selected": refs}), shared_summary_json="{}",
        validation_json="{}", evidence_refs=refs, affected_member_ids=[], impacts=[], burdens=[], selected_candidate_refs=refs)


def test_publish_rechecks_flights_against_confirmed_limits(ready_room, client, worker):
    room, job_id = ready_room
    over_cap = record_flights(room, worker, job_id, {"alice": {"total_cents": 50001}})
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id, over_cap)
    expect_error("INVALID_COVERAGE", publish_selected, room, worker, job_id, over_cap[1:])
    stale = record_flights(room, worker, job_id, {"bob": {"valid_until": 1}})
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id, stale)
    spread = record_flights(room, worker, job_id, {"dave": {"arrival_epoch": 1}})
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id, spread)
    expect_error("INVALID_EVIDENCE_REF", publish_selected, room, worker, job_id, ["not-recorded"])
    observed_only = record_flights(room, worker, job_id, {"carol": {"cost_basis": "OBSERVED_FLIGHT_PRICE"}})
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id, observed_only)
    missing_fees = record_flights(room, worker, job_id, {"carol": {"missing_critical_fields": ["taxes"]}})
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id, missing_fees)
    good = record_flights(room, worker, job_id)
    assert publish_selected(room, worker, job_id, good)["version"] == 1


def test_baggage_requirement_is_enforced_at_publish(ready_room, client, worker):
    room, job_id = ready_room
    client.call(room.people["bob"], "set_constraint", room_id=room.room_id, key="baggage_required",
                value_json="true", unit="", hardness="HARD", authorized_range_json=None, private_rationale=None)
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id,
                 record_flights(room, worker, job_id, {"bob": {"baggage_included": None}}))


def test_room_settings_are_organizer_only_and_replan(ready_room, client, worker):
    room, job_id = ready_room
    expect_error("NOT_AUTHORIZED", client.call, room.people["bob"], "set_room_settings", room_id=room.room_id,
                 settings_json=json.dumps({"arrival_overlap_seconds": 60}))
    client.call(room.people["alice"], "set_room_settings", room_id=room.room_id,
                settings_json=json.dumps({"arrival_overlap_seconds": 60}))
    expect_error("STALE_SNAPSHOT", room.context, worker, job_id)
    new_job = room.plan_job(worker)
    room.claim(worker, new_job)
    assert room.context(worker, new_job)["room"]["settings"] == {"arrival_overlap_seconds": 60}
    # Flights 10 minutes apart now break the 60-second overlap rule.
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, new_job, record_flights(room, worker, new_job))


def record_shared(room: Room, worker, job_id: int, category: str, total_cents: int, **facts) -> str:
    import time as _time
    now = _time.time()
    body = {"category": category, "total_cents": total_cents, "currency": "USD", "observed_at": now - 5,
            "valid_until": now + 3600, "capacity": 4, "cost_basis": f"COMPLETE_{category}_TOTAL",
            **({"bedrooms": 2} if category == "STAY" else {}), **facts}
    ref = f"{category.lower()}-{key()}"
    room.client.call(worker, "record_candidates", job_id=job_id, attempt=room.leases[job_id], request_key=key(),
                     candidates=[{"candidate_ref": ref, "candidate_set_version": 1, "category": category, "mode": "FIXTURE",
                                  "source_url": "fixture://test", "observed_at": "2026-10-24T09:00:00Z",
                                  "facts_json": json.dumps(body), "missing_fields": []}])
    return ref


def trip_room(room: Room, client, worker) -> int:
    client.call(room.people["alice"], "set_room_settings", room_id=room.room_id,
                settings_json=json.dumps({"components": ["FLIGHT", "STAY", "TRANSFER"]}))
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    return job_id


def test_trip_publish_checks_shared_items_and_trip_budgets(ready_room, client, worker):
    room, _ = ready_room
    job_id = trip_room(room, client, worker)
    flights = record_flights(room, worker, job_id)  # alice $490, others $640
    stay = record_shared(room, worker, job_id, "STAY", 120000)
    van = record_shared(room, worker, job_id, "TRANSFER", 12000)
    # Every required part, once.
    expect_error("INVALID_COVERAGE", publish_selected, room, worker, job_id, flights + [stay])
    expect_error("INVALID_COVERAGE", publish_selected, room, worker, job_id, flights + [stay, van, record_shared(
        room, worker, job_id, "TRANSFER", 9000)])
    # Shared items must fit everyone and carry a complete, current price.
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id,
                 flights + [record_shared(room, worker, job_id, "STAY", 60000, capacity=2), van])
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id,
                 flights + [stay, record_shared(room, worker, job_id, "TRANSFER", 9000, cost_basis="OBSERVED_PRICE")])
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id,
                 flights + [record_shared(room, worker, job_id, "STAY", 1000, valid_until=1), van])
    # alice: $490 flight + $330 share = $820 > an $800 trip budget.
    client.call(room.people["alice"], "set_constraint", room_id=room.room_id, key="trip_budget_total", value_json="80000",
                unit="USD_cents", hardness="HARD", authorized_range_json=None, private_rationale=None)
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    flights = record_flights(room, worker, job_id)
    van = record_shared(room, worker, job_id, "TRANSFER", 12000)
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id,
                 flights + [record_shared(room, worker, job_id, "STAY", 120000), van])
    assert publish_selected(room, worker, job_id, flights + [record_shared(room, worker, job_id, "STAY", 112000), van])["version"] == 1


def test_room_components_must_be_valid(ready_room, client, worker):
    room, _ = ready_room
    for bad in (["STAY"], ["FLIGHT", "FLIGHT"], ["FLIGHT", "SPA"], "FLIGHT"):
        expect_error("INVALID_ARGUMENT", client.call, room.people["alice"], "set_room_settings", room_id=room.room_id,
                     settings_json=json.dumps({"components": bad}))


def test_arrival_window_and_max_stops_are_enforced_at_publish(ready_room, client, worker):
    room, _ = ready_room
    client.call(room.people["bob"], "set_constraint", room_id=room.room_id, key="arrival_window",
                value_json=json.dumps({"earliest": "06:00", "latest": "23:00"}), unit="", hardness="HARD",
                authorized_range_json=None, private_rationale=None)
    client.call(room.people["carol"], "set_constraint", room_id=room.room_id, key="max_stops", value_json="0", unit="",
                hardness="HARD", authorized_range_json=None, private_rationale=None)
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    fine = {"bob": {"arrival_local": "21:40"}, "carol": {"stops": 0}}
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id,
                 record_flights(room, worker, job_id, {**fine, "bob": {"arrival_local": "01:10"}}))
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id,
                 record_flights(room, worker, job_id, {**fine, "carol": {"stops": 1}}))
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id,
                 record_flights(room, worker, job_id, {"carol": {"stops": 0}}))  # bob's arrival time unknown
    assert publish_selected(room, worker, job_id, record_flights(room, worker, job_id, fine))["version"] == 1
