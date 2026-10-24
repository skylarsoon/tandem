"""Synthetic providers. No fixture is ever labeled live."""
from __future__ import annotations

import copy

from .contracts import Act, Brief, Candidate, Decision, Preferences, Snapshot, VoiceDraft, VoiceSegment


HOUR = 3600

# Social-friction scenarios: offers are (owner, name, price_cents, hours after the
# first landing, checked bag). Members default to speak-up on, ask-me-first off.
FRICTION = {
    # p4's cheap fare lands 5h after everyone; landing with the group costs them
    # $125 more. They asked to be consulted first.
    "group-arrival": dict(prefs={"p4": {"ask_me_first": True}}, offers=[
        ("p1", "noon", 30_000, 0, True), ("p2", "noon", 32_000, 0.2, True), ("p3", "noon", 35_000, 0.4, True),
        ("p4", "redeye", 40_000, 5, True), ("p4", "noon", 52_500, 0.5, True)]),
    # Same, but p4 says no: the trade-off moves to the other three, who can each
    # land later for $150 more.
    "concession-declined": dict(prefs={"p4": {"ask_me_first": True}}, offers=[
        ("p1", "noon", 30_000, 0, True), ("p2", "noon", 32_000, 0.2, True), ("p3", "noon", 35_000, 0.4, True),
        ("p1", "late", 45_000, 4.5, True), ("p2", "late", 47_000, 4.6, True), ("p3", "late", 50_000, 4.8, True),
        ("p4", "redeye", 40_000, 5, True), ("p4", "noon", 52_500, 0.5, True)]),
    # p1's limit is below every fare, even after targeted searches.
    "budget-blocked": dict(caps={"p1": 25_000}, offers=[
        ("p1", "noon", 30_000, 0, True), ("p2", "noon", 32_000, 0.2, True), ("p3", "noon", 35_000, 0.4, True),
        ("p4", "noon", 40_000, 0.5, True)], targeted=[("p1", "saver", 29_000, 1, True)]),
    # No combination lands everyone within the room's 2h window.
    "arrival-impossible": dict(offers=[
        ("p1", "noon", 30_000, 0, True), ("p2", "noon", 32_000, 0.2, True), ("p3", "noon", 35_000, 0.4, True),
        ("p4", "redeye", 40_000, 5, True), ("p4", "late", 45_000, 3.5, True)]),
}


def scenario_inputs(name: str):
    members = ("p1", "p2", "p3", "p4")
    snapshot = Snapshot(room_id="fixture-room", members=members)
    if name in FRICTION:
        spec = FRICTION[name]
        briefs = [Brief(owner=o, hard_cap_cents=spec.get("caps", {}).get(o, 65_000), private_reason=f"PRIVATE_CANARY_{o}",
            preferences=Preferences(**{"confirmed": True, "speak_up_for_me": True, "ask_me_first": False, **spec.get("prefs", {}).get(o, {})}))
            for o in members]
        return snapshot, briefs
    briefs = [Brief(owner=o, hard_cap_cents=50_000 if o == "p1" else 65_000,
        preferences=Preferences(confirmed=True, speak_up_for_me=True),
        private_reason=f"PRIVATE_CANARY_{o}", baggage_required=name == "agent-objection" and o == "p2") for o in members]
    return snapshot, briefs


class FixtureResearch:
    def __init__(self, clock, scenario="targeted-budget"):
        self.clock, self.scenario = clock, scenario
        self.requests = []

    def search(self, request):
        self.requests.append(copy.deepcopy(request))
        now = request["observed_at"]
        targeted = request["purpose"] == "TARGETED"
        if self.scenario in FRICTION:
            spec = FRICTION[self.scenario]
            offers = spec.get("targeted", []) if targeted else spec["offers"]
            return [Candidate(id=f"{owner}-{name}-{request['purpose'].lower()}-{int(now * 1000)}", owner=owner, total_cents=price,
                arrival_epoch=1_800_052_000 + int(hours * HOUR), arrival_timezone="Pacific/Honolulu", observed_at=now,
                valid_until=now + 3600, source_url=f"https://fixtures.invalid/{owner}/{name}", run_ref=f"fixture-{request['purpose']}",
                mode="FIXTURE", cost_basis="COMPLETE_FLIGHT_TOTAL", baggage_included=bag, flight_identity=f"{owner}-{name}")
                for owner, name, price, hours, bag in offers if owner in request["target_owners"]]
        results = []
        for i, owner in enumerate(request["members"]):
            if targeted and self.scenario == "agent-objection" and owner != "p2":
                continue
            if targeted and self.scenario != "agent-objection" and owner != "p1":
                continue
            price = 45_000
            if targeted and self.scenario == "agent-objection" and owner == "p2":
                price = 44_000
            if owner == "p1" and self.scenario in {"targeted-budget", "clarification-restart"}:
                price = 48_000 if targeted else 52_000
            suffix = f"-r{request['input_revision']}" if request["input_revision"] != 1 else ""
            results.append(Candidate(id=f"{owner}-{'targeted' if targeted else 'initial'}{suffix}", owner=owner,
                total_cents=price, arrival_epoch=1_800_010_000 + i * 600, observed_at=now, valid_until=now + 3600,
                source_url=f"https://fixtures.invalid/{owner}", run_ref=f"fixture-{request['purpose']}",
                mode="FIXTURE", cost_basis="COMPLETE_FLIGHT_TOTAL",
                baggage_included=None if self.scenario == "agent-objection" and owner == "p2" and not targeted else True))
        return results


class ScriptedModel:
    def __init__(self, scenario="targeted-budget"):
        self.scenario = scenario
        self.decisions = []
        self.voices = []

    def decide(self, context):
        self.decisions.append(copy.deepcopy(context))
        if self.scenario == "clarification-restart" and not context["missing_fact_answered"] and context["owner"] == "p1":
            return Decision(evaluation="NEEDS_OWNER_INPUT", blocker="MISSING_FACT")
        if context["inbound_type"] == "CLARIFY":
            return Decision(act=Act.COUNTERPROPOSE, evaluation="INCOMPLETE_EVIDENCE", blocker="BAGGAGE_UNKNOWN", research=True,
                recipient=context["sender"])
        if context["baggage_required"] and context["candidate"]["baggage_included"] is not True:
            # A real addressed question to another owner, whose reply drives research.
            return Decision(act=Act.CLARIFY, evaluation="INCOMPLETE_EVIDENCE", blocker="BAGGAGE_UNKNOWN", recipient="p4")
        return Decision()

    def voice(self, context):
        self.voices.append(copy.deepcopy(context))
        return VoiceDraft(segments=tuple(VoiceSegment(fact_ref=k, text=v) for k, v in context["facts"].items()))
