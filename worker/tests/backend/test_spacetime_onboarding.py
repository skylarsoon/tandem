"""Conversational onboarding end to end on the real module: description → brief →
each traveller's conversation → quick origin → one confirmed summary."""
import json

from tandem_worker.fixtures import PERSONAS, PersonaModel, ScriptedTraveller, persona
from tandem_worker.intake_policy import open_topics
from tandem_worker.runner import Worker

from conftest import onboarding_room

NAMES = ("sam", "maya", "jordan", "luis")


def mine(room, name, view):
    return [r for r in room.backend.http.view(room.tokens[name], view) if r.get("room_id") == room.room_id]


def test_four_travellers_onboard_through_conversation(tmp_path):
    room = onboarding_room(NAMES, "sam")
    http, tokens = room.backend.http, room.tokens
    worker = Worker(room.backend, PersonaModel(), None, tmp_path / "checkpoints.sqlite", limits=room.backend.limits)

    def drain():
        for _ in range(40):
            jobs = [str(j["id"]) for j in room.backend.pending_jobs() if j["kind"] != "PLAN"]
            if not jobs:
                return
            for job in jobs:
                worker.run_one(job)

    http.call(tokens["sam"], "set_trip_description", room_id=room.room_id, description=PERSONAS["trip"]["description"])
    drain()
    [brief] = mine(room, "maya", "room_trip_brief")
    assert brief["state"] == "PROPOSED" and json.loads(brief["brief_json"])["destination_airport"] == "HNL"
    http.call(tokens["sam"], "confirm_trip_brief", room_id=room.room_id, revision=brief["revision"])
    drain()

    travellers, first = {n: ScriptedTraveller(n) for n in NAMES}, {n: True for n in NAMES}
    for _ in range(8):
        for name in NAMES:
            [intake] = mine(room, name, "my_intake")
            for patch in mine(room, name, "my_constraint_patches"):
                if patch["state"] == "PENDING" and json.loads(patch["patch_json"])["key"] == "origin_airport":
                    http.call(tokens[name], "confirm_origin", room_id=room.room_id, patch_id=patch["id"])
            if intake["state"] == "SUMMARY":
                pending = [p["id"] for p in mine(room, name, "my_constraint_patches") if p["state"] == "PENDING"]
                [prefs] = room.backend.http.view(tokens[name], "my_participation_preferences")
                http.call(tokens[name], "confirm_intake", room_id=room.room_id, accepted_patch_ids=pending,
                          speak_up_for_me={"some": prefs["speak_up_for_me"]}, ask_me_first={"some": prefs["ask_me_first"]},
                          give_me_time={"some": prefs["give_me_time"]}, reminders_opted_in=None)
            elif intake["state"] == "ASKING":
                messages = sorted(mine(room, name, "my_private_messages"), key=lambda m: m["id"])
                if messages and messages[-1]["sender"] == "AGENT":
                    asked = ["open"] if first[name] else open_topics(json.loads(intake["coverage_json"]))[:2]
                    first[name] = False
                    if said := travellers[name].reply(asked):
                        http.call(tokens[name], "submit_private_message", room_id=room.room_id, body=said)
        drain()
        if all(mine(room, n, "my_intake")[0]["state"] == "CONFIRMED" for n in NAMES):
            break
    for name in NAMES:
        [intake] = mine(room, name, "my_intake")
        assert intake["state"] == "CONFIRMED" and intake["question_count"] <= 3, (name, intake)
        keys = {c["key"]: json.loads(c["value_json"]) for c in mine(room, name, "my_constraints")}
        truth = persona(name)["truth"]
        for key, expected in truth.items():
            if key in ("speak_up_for_me", "ask_me_first"):
                continue
            assert keys[key] == (expected["value"] if isinstance(expected, dict) and "hardness" in expected else expected), (name, key)
    [progress] = mine(room, "luis", "room_intake_progress")
    assert (progress["ready"], progress["total"]) == (4, 4)
    room_text = json.dumps(mine(room, "luis", "room_events") + mine(room, "luis", "room_messages"))
    assert "laid off" not in room_text and "car broke down" not in room_text
