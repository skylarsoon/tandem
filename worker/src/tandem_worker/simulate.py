"""Live onboarding simulation: Claude plays each persona (private backstory,
speaking style) against the real worker on SpacetimeDB, then scores what the
agents recorded against the persona's ground truth.

Scores per traveller: questions asked, truth items captured, items recorded
that the persona never had (assumptions, must be 0), and private reasons that
reached the room (must be 0)."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import Field

from .adapters.claude import ClaudeModel
from .contracts import Record
from .fixtures import PERSONAS, persona


class TravellerReply(Record):
    reply: str = Field(min_length=1, max_length=600)


class TravellerAnswer(Record):
    yes: bool


class ClaudeTraveller:
    """One persona, played by Claude. It only knows its own backstory."""
    def __init__(self, name: str, model: ClaudeModel):
        self.persona, self.model = persona(name), model

    def _system(self):
        p = self.persona
        return (f"You are {p['name']}, a real person planning a group trip with friends. Private backstory (yours only): "
                f"{p['backstory']} How you talk: {p['style']} You're chatting privately with your own trip agent. Reply "
                "the way you would by text: answer what it asked, briefly, in your own voice. Share private details only "
                "if the question invites them or you'd naturally mention them. Never invent needs beyond your backstory. "
                f"Your actual situation, for consistency (don't recite it): {json.dumps(p['truth'])}")

    def reply(self, conversation: list[dict]) -> str:
        context = {"conversation": [{"from": "agent" if m["sender"] == "AGENT" else "you", "text": m["body"]} for m in conversation]}
        return self.model._structured(context, TravellerReply, self._system() + " Write your next message.", "traveller").reply

    def answer(self, question: str) -> bool:
        return self.model._structured({"question": question}, TravellerAnswer,
            self._system() + " Your agent asks a yes/no question; answer as you truly would.", "traveller").yes


def score(name: str, constraints: dict, questions: int, room_text: str) -> dict:
    truth = persona(name)["truth"]
    expected = {k for k in truth if k not in ("speak_up_for_me", "ask_me_first")}
    recorded = {k for k in constraints if k != "dates_ok"}
    captured = sorted(expected & recorded)
    # Things recorded that the persona never had. Answers to just-in-time questions
    # (rooming, bags, ...) are the persona's own answers, so only onboarding keys count.
    onboarding_keys = {"origin_airport", "flight_budget_total", "trip_budget_total", "no_budget_limit",
                       "arrival_window", "max_stops", "stay_min_rating"}
    assumed = sorted(k for k in (recorded - expected) & onboarding_keys)
    reason = truth_reason(name)
    leaked = [reason] if reason and reason.lower() in room_text.lower() else []
    return {"questions": questions, "captured": captured, "missed": sorted(expected - recorded), "assumed": assumed,
            "leaked_reasons": leaked}


def truth_reason(name: str) -> str | None:
    for reply in persona(name)["replies"]:
        for item in reply.get("items", []):
            if item.get("private_rationale"):
                return item["private_rationale"]
    return None


def simulate(settings, directory, names, *, plan=False, max_rounds=10, config=None, claude_client=None, apify_client=None):
    from .adapters.spacetime import StdbHttp, some
    from .serve import ServeConfig, make_room_providers, serve
    settings.require()
    settings.require_backend()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    config = config or ServeConfig()
    providers = make_room_providers(config, settings, directory, claude_client=claude_client, apify_client=apify_client)
    providers.preflight()
    travellers = {n: ClaudeTraveller(n, providers.model) for n in names}
    http = StdbHttp(settings.stdb_url, settings.stdb_database)
    organizer = PERSONAS["trip"]["organizer"] if PERSONAS["trip"]["organizer"] in names else names[0]
    tokens = {n: http.mint_identity()["token"] for n in names}
    http.call(tokens[organizer], "create_room", goal="Honolulu trip (simulation)", display_name=organizer)
    room = max(http.view(tokens[organizer], "my_rooms"), key=lambda r: r["id"])["id"]
    for n in names:
        if n != organizer:
            invite = max(http.view(tokens[organizer], "my_rooms"), key=lambda r: r["id"])["invite_code"]
            http.call(tokens[n], "join_room", invite_code=invite, display_name=n)
    (directory / "simulation-room.json").write_text(json.dumps({"room_id": room, "owner_tokens": tokens}))
    (directory / "simulation-room.json").chmod(0o600)
    http.call(tokens[organizer], "set_trip_description", room_id=room, description=PERSONAS["trip"]["description"])

    def mine(name, view):
        return [r for r in http.view(tokens[name], view) if r.get("room_id") == room]

    log, results, failures = [], [], {}
    for round_no in range(max_rounds):
        # Without --plan, only onboarding runs: no paid searches.
        results += serve(config, settings, directory, rooms={room}, once=True, providers=providers,
                         kinds=None if plan else {"INTAKE", "INTERPRET"})
        briefs = mine(organizer, "room_trip_brief")
        if briefs and briefs[0]["state"] == "PROPOSED":
            http.call(tokens[organizer], "confirm_trip_brief", room_id=room, revision=briefs[0]["revision"])
            continue
        replies = {}  # Claude plays everyone at once; their messages are sent after

        def play(name):
            intakes = mine(name, "my_intake")
            if not intakes or failures.get(name, 0) >= 2:
                return
            intake = intakes[0]
            for patch in mine(name, "my_constraint_patches"):
                body = json.loads(patch["patch_json"])
                if patch["state"] == "PENDING" and body["key"] == "origin_airport":
                    if body["value"] == persona(name)["truth"]["origin_airport"]:
                        http.call(tokens[name], "confirm_origin", room_id=room, patch_id=patch["id"])
                    else:
                        http.call(tokens[name], "submit_private_message", room_id=room,
                                  body=f"No, I fly from {persona(name)['truth']['origin_airport']}.")
            if intake["state"] == "SUMMARY":
                pending = [p["id"] for p in mine(name, "my_constraint_patches") if p["state"] == "PENDING"]
                prefs = http.view(tokens[name], "my_participation_preferences")[0]
                try:
                    http.call(tokens[name], "confirm_intake", room_id=room, accepted_patch_ids=pending,
                              speak_up_for_me=some(prefs["speak_up_for_me"]), ask_me_first=some(prefs["ask_me_first"]),
                              give_me_time=some(prefs["give_me_time"]), reminders_opted_in=None)
                except Exception as error:  # e.g. money still missing: say so, as a person would
                    failures[name] = failures.get(name, 0) + 1  # two failed confirms: stop, it's a loop
                    log.append({"round": round_no, "who": name, "confirm_failed": str(error)[:120]})
                    replies[name] = travellers[name].reply(sorted(mine(name, "my_private_messages"), key=lambda m: m["id"]))
            elif intake["state"] == "ASKING":
                messages = sorted(mine(name, "my_private_messages"), key=lambda m: m["id"])
                if messages and messages[-1]["sender"] == "AGENT":
                    replies[name] = travellers[name].reply(messages)
            for q in mine(name, "my_questions"):
                if q["state"] == "OPEN":
                    yes = travellers[name].answer(q["prompt"])
                    action = json.loads(q["action_json"])
                    payload = ({"answer": yes, "constraint": action["nudge"]["yes" if yes else "no"]} if action.get("nudge")
                               else {"granted": yes} if q["kind"] in ("CONCESSION_CONSULTATION", "OBJECTION_DISCLOSURE", "BROADER_EXPLORATION")
                               else {"answer": yes})
                    http.call(tokens[name], "answer_question", question_id=q["id"], version=q["version"], answer_json=json.dumps(payload))
                    log.append({"round": round_no, "who": name, "asked": q["prompt"], "yes": yes})

        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            list(pool.map(play, names))
        for name, body in replies.items():
            http.call(tokens[name], "submit_private_message", room_id=room, body=body)
        done = all(mine(n, "my_intake") and mine(n, "my_intake")[0]["state"] == "CONFIRMED" for n in names)
        proposed = any(p["state"] == "AWAITING_REVIEW" for p in mine(organizer, "room_proposals"))
        if done and (not plan or proposed):
            break

    room_text = json.dumps(mine(organizer, "room_messages") + mine(organizer, "room_events") + mine(organizer, "room_proposals"))
    report = {"room_id": room, "travellers": {}, "jobs": results, "log": log}
    for name in names:
        constraints = {c["key"]: json.loads(c["value_json"]) for c in mine(name, "my_constraints")}
        intake = (mine(name, "my_intake") or [{}])[0]
        report["travellers"][name] = {
            "state": intake.get("state"), "constraints": constraints,
            "score": score(name, constraints, intake.get("question_count", 0), room_text),
            "transcript": [f"{'AGENT' if m['sender'] == 'AGENT' else name}: {m['body']}"
                           for m in sorted(mine(name, "my_private_messages"), key=lambda m: m["id"])]}
    proposals = mine(organizer, "room_proposals")
    if proposals:
        report["proposal"] = json.loads(proposals[-1]["shared_summary_json"]).get("text")
    report["usage"] = providers.budget.report()["used"]
    (directory / "simulation-report.json").write_text(json.dumps(report, indent=2))
    return report
