"""Synthetic providers. No fixture is ever labeled live."""
from __future__ import annotations

import copy

from .contracts import Act, Brief, Candidate, Decision, Preferences, Snapshot, VoiceDraft, VoiceSegment


def scenario_inputs(name: str):
    members = ("p1", "p2", "p3", "p4")
    snapshot = Snapshot(room_id="fixture-room", members=members)
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
