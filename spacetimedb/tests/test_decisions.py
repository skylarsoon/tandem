"""Independent decisions inside a stage: each proposal is a version of one
decision, validated for its scope and against what the group already agreed."""

import json
import time

from conftest import PEOPLE, Room, expect_error, key
from test_proposals import milestone, publish_selected, record_flights, record_shared, respond, trip_room

DAY = time.time() + 2 * 86400


def agree(room: Room, published: dict) -> None:
    for name in PEOPLE:
        respond(room, name, published)


def states(room: Room) -> dict[int, str]:
    return {p["id"]: p["state"] for p in room.client.view(room.people["alice"], "room_proposals")
            if p["room_id"] == room.room_id}


def next_job(room: Room, worker) -> int:
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    return job_id


def activity(room: Room, worker, job_id: int, per_person: int, start: float | None = None, **facts) -> str:
    if start is not None:
        facts.update(start_epoch=start, end_epoch=start + 2 * 3600)
    return record_shared(room, worker, job_id, "ACTIVITY", per_person, cost_basis="ESTIMATE_PER_PERSON", **facts)


def include(room: Room, worker, job_id: int, decision_id: str, refs: list[str]) -> dict:
    return publish_selected(room, worker, job_id, refs, scope="ACTIVITY_DECISION", decision_id=decision_id,
                            stage="STAY_EXPERIENCES")


def test_agreed_snorkeling_survives_hiking_and_only_its_own_revision_replaces_it(ready_room, worker):
    room, job_id = ready_room
    snorkel = include(room, worker, job_id, "activity_snorkeling", [activity(room, worker, job_id, 9000, DAY)])
    agree(room, snorkel)

    # Agreeing one part queues planning of the next, on top of it.
    job_id = next_job(room, worker)
    assert [c["decision_id"] for c in room.context(worker, job_id)["commitments"]] == ["activity_snorkeling"]
    hike = include(room, worker, job_id, "activity_hiking", [activity(room, worker, job_id, 4000, DAY + 4 * 3600)])
    assert states(room)[snorkel["proposal_id"]] == "AGREED", "a different decision leaves snorkeling agreed"
    assert milestone(room, "STAY_EXPERIENCES")["state"] == "IN_REVIEW"

    agree(room, hike)
    assert milestone(room, "STAY_EXPERIENCES")["state"] == "AGREED"

    job_id = next_job(room, worker)
    revised = include(room, worker, job_id, "activity_snorkeling", [activity(room, worker, job_id, 8000, DAY)])
    assert revised["version"] == 2
    now = states(room)
    assert (now[snorkel["proposal_id"]], now[hike["proposal_id"]], now[revised["proposal_id"]]) == \
        ("SUPERSEDED", "AGREED", "AWAITING_REVIEW"), "only the earlier snorkeling is replaced"
    expect_error("STALE_SNAPSHOT", respond, room, "alice", snorkel)


def test_a_hotel_alone_is_validated_without_flights(ready_room, client, worker):
    room, job_id = ready_room
    stay = record_shared(room, worker, job_id, "STAY", 120000)
    expect_error("INVALID_COVERAGE", publish_selected, room, worker, job_id, [stay], scope="SINGLE_SHARED_BOOKING",
                 decision_id="stay")  # this room only plans flights

    job_id = trip_room(room, client, worker)
    small = record_shared(room, worker, job_id, "STAY", 60000, capacity=2)
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id, [small], scope="SINGLE_SHARED_BOOKING",
                 decision_id="stay")
    stay = record_shared(room, worker, job_id, "STAY", 120000)
    van = record_shared(room, worker, job_id, "TRANSFER", 12000)
    expect_error("INVALID_COVERAGE", publish_selected, room, worker, job_id, [stay, van], scope="SINGLE_SHARED_BOOKING",
                 decision_id="stay")
    published = publish_selected(room, worker, job_id, [stay], scope="SINGLE_SHARED_BOOKING", decision_id="stay",
                                 stage="STAY_EXPERIENCES")
    assert published["version"] == 1


def test_an_activity_that_clashes_with_an_agreed_one_is_blocked(ready_room, worker):
    room, job_id = ready_room
    snorkel_ref = activity(room, worker, job_id, 9000, DAY)
    agree(room, include(room, worker, job_id, "activity_snorkeling", [snorkel_ref]))

    job_id = next_job(room, worker)
    clash = activity(room, worker, job_id, 4000, DAY + 3600)  # starts while snorkeling is on
    expect_error("CONFLICTS_WITH_COMMITMENT", include, room, worker, job_id, "activity_hiking", [clash])
    expect_error("CONFLICTS_WITH_COMMITMENT", include, room, worker, job_id, "activity_snorkel_again", [snorkel_ref])
    # Activities without known times can't be checked for overlap, so they pass.
    assert include(room, worker, job_id, "activity_luau", [activity(room, worker, job_id, 6000)])["version"] == 1


def test_whole_trip_budget_counts_what_the_group_already_agreed(ready_room, client, worker):
    room, _ = ready_room
    room.set_constraint("alice", "trip_budget_total", 80000)
    job_id = trip_room(room, client, worker)
    flights = record_flights(room, worker, job_id)  # alice's flight: $490
    agree(room, publish_selected(room, worker, job_id, flights, scope="FLIGHT_BUNDLE", decision_id="flights"))

    job_id = next_job(room, worker)
    expect_error("CONFLICTS_WITH_COMMITMENT", publish_selected, room, worker, job_id, record_flights(room, worker, job_id),
                 scope="FLIGHT_BUNDLE", decision_id="flights_again")
    # A $3,400 stay is $850 each: over alice's $800 on its own.
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id,
                 [record_shared(room, worker, job_id, "STAY", 340000)], scope="SINGLE_SHARED_BOOKING", decision_id="stay")
    # $1,400 is $350 each: fine alone, but $490 + $350 > $800 with her agreed flight.
    err = expect_error("CONFLICTS_WITH_COMMITMENT", publish_selected, room, worker, job_id,
                       [record_shared(room, worker, job_id, "STAY", 140000)], scope="SINGLE_SHARED_BOOKING",
                       decision_id="stay")
    assert "already agreed" in err.detail
    stay = publish_selected(room, worker, job_id, [record_shared(room, worker, job_id, "STAY", 120000)],
                            scope="SINGLE_SHARED_BOOKING", decision_id="stay")
    agree(room, stay)

    job_id = next_job(room, worker)
    # $490 + $300 = $790 committed: a $20-per-person activity breaks alice's $800.
    expect_error("CONFLICTS_WITH_COMMITMENT", include, room, worker, job_id, "activity_tour",
                 [activity(room, worker, job_id, 2000)])
    expect_error("CONFLICTS_WITH_COMMITMENT", publish_selected, room, worker, job_id,
                 [record_shared(room, worker, job_id, "STAY", 100000)], scope="SINGLE_SHARED_BOOKING", decision_id="stay_2")
    assert include(room, worker, job_id, "activity_beach", [activity(room, worker, job_id, 1000)])["version"] == 1


def test_a_stale_proposal_cannot_be_approved_but_commitments_survive_changes(ready_room, worker):
    room, job_id = ready_room
    snorkel = include(room, worker, job_id, "activity_snorkeling", [activity(room, worker, job_id, 9000, DAY)])
    agree(room, snorkel)
    job_id = next_job(room, worker)
    hike = include(room, worker, job_id, "activity_hiking", [activity(room, worker, job_id, 4000, DAY + 4 * 3600)])

    room.set_constraint("bob", "flight_budget_total", 60000)  # an input changes
    expect_error("STALE_SNAPSHOT", respond, room, "alice", hike)
    now = states(room)
    assert (now[hike["proposal_id"]], now[snorkel["proposal_id"]]) == ("SUPERSEDED", "AGREED")


def test_scopes_and_decision_ids_are_checked(ready_room, client, worker):
    room, job_id = ready_room
    one, two = activity(room, worker, job_id, 1000), activity(room, worker, job_id, 2000)
    expect_error("INVALID_COVERAGE", include, room, worker, job_id, "activity_x", [one, two])
    expect_error("INVALID_COVERAGE", publish_selected, room, worker, job_id, [one], scope="FLIGHT_BUNDLE", decision_id="f")
    expect_error("INVALID_COVERAGE", publish_selected, room, worker, job_id, [one], scope="GENERAL_AGREEMENT",
                 decision_id="dates")
    expect_error("INVALID_ARGUMENT", publish_selected, room, worker, job_id, [], scope="WHATEVER", decision_id="x")
    expect_error("INVALID_ARGUMENT", publish_selected, room, worker, job_id, [], scope="GENERAL_AGREEMENT",
                 decision_id="Not Valid")
    skip = include(room, worker, job_id, "activity_volcano", [])  # skipping is a decision too
    agree(room, skip)
    assert room.jobs(worker), "agreeing a part queues planning of the next"


def test_a_general_agreement_needs_no_follow_up(ready_room, worker):
    room, job_id = ready_room
    agree(room, room.publish(worker, job_id, decision_id="dates", stage="GOALS_DATES"))
    assert room.jobs(worker) == [], "nothing left to plan after a general agreement"
    assert milestone(room, "GOALS_DATES")["state"] == "AGREED"


def test_an_agreed_decision_a_change_breaks_is_reopened_and_one_it_doesnt_stands(ready_room, client, worker):
    room, job_id = ready_room
    flights = publish_selected(room, worker, job_id, record_flights(room, worker, job_id), scope="FLIGHT_BUNDLE",
                               decision_id="flight", stage="TRAVEL")
    agree(room, flights)
    assert states(room)[flights["proposal_id"]] == "AGREED"
    # Carol now says her fare must be under $100: her agreed flight ($640) breaks it.
    client.call(room.people["carol"], "add_rule", room_id=room.room_id, source_quote=None,
                rule_json=json.dumps({"category": "FLIGHT", "attribute": "total_cents", "operator": "lte", "value": 10000}))
    assert states(room)[flights["proposal_id"]] == "SUPERSEDED"
    events = [e for e in client.view(room.people["bob"], "room_events") if e["room_id"] == room.room_id]
    assert any(e["type"] == "DECISION_REOPENED" for e in events) and "carol" not in json.dumps(events)
    # A change that breaks nothing leaves agreement alone.
    job_id = next_job(room, worker)
    again = publish_selected(room, worker, job_id, record_flights(room, worker, job_id, {"carol": {"total_cents": 9000}}),
                             scope="FLIGHT_BUNDLE", decision_id="flight", stage="TRAVEL")
    agree(room, again)
    client.call(room.people["bob"], "add_rule", room_id=room.room_id, source_quote=None,
                rule_json=json.dumps({"category": "FLIGHT", "attribute": "total_cents", "operator": "lte", "value": 90000}))
    assert states(room)[again["proposal_id"]] == "AGREED"
