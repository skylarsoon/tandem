"""Synthetic providers. No fixture is ever labeled live."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .contracts import (Act, Brief, BriefDraft, Candidate, Decision, IntakeExtraction, Preferences, Snapshot, TripBrief,
                        VoiceDraft, VoiceSegment)


HOUR = 3600

# Social-friction scenarios: offers are (owner, name, price_cents, hours after the
# first landing, checked bag). Members default to speak-up on, ask-me-first off.
# Trip scenarios add shared stays (name, price, guests, bedrooms, rating),
# transfers (name, price, seats) and ideas (category, name, per-person price, rating).
ON_TIME = [("p1", "noon", 30_000, 0, True), ("p2", "noon", 32_000, 0.2, True), ("p3", "noon", 35_000, 0.4, True),
           ("p4", "noon", 40_000, 0.5, True)]
TRIP = dict(components=("FLIGHT", "STAY", "TRANSFER"), offers=ON_TIME,
    stays=[("beach-house", 160_000, 6, 3, 4.9), ("condo", 120_000, 4, 2, 4.6), ("studio", 60_000, 2, 0, 4.7)],
    transfers=[("van", 12_000, 6), ("sedan", 5_000, 3)],
    ideas=[("ACTIVITY", "Sunrise snorkel tour", 8_900, 4.8), ("ACTIVITY", "Diamond Head hike", None, 4.7),
           ("DINNER", "Family-style luau", 9_500, 4.6)])
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
    # Full trip: flights, the cheapest stay that sleeps four in two bedrooms, a van.
    "trip-happy": TRIP,
    # Two travellers want their own room: three bedrooms, so the stay costs everyone more.
    "own-room": dict(TRIP, briefs={"p1": {"own_bedroom": True}, "p2": {"own_bedroom": True}}),
    # Same, but p4's trip limit only fits the two-bedroom condo. The clash is
    # between the room requests and p4's limit; each side is told privately.
    "own-room-squeeze": dict(TRIP, briefs={"p1": {"own_bedroom": True}, "p2": {"own_bedroom": True},
                                           "p4": {"trip_cap_cents": 75_000}}),
    # p3 wants a near-perfect rating that no stay found has.
    "stay-rating": dict(TRIP, briefs={"p3": {"stay_min_rating": 4.95}}),
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
        snapshot = snapshot.model_copy(update={"components": spec.get("components", ("FLIGHT",))})
        briefs = [Brief(owner=o, hard_cap_cents=spec.get("caps", {}).get(o, 65_000), private_reason=f"PRIVATE_CANARY_{o}",
            preferences=Preferences(**{"confirmed": True, "speak_up_for_me": True, "ask_me_first": False, **spec.get("prefs", {}).get(o, {})}),
            **spec.get("briefs", {}).get(o, {})) for o in members]
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
        if request.get("routes") or request.get("trip"):
            return onboarded_offers(request)
        if self.scenario in FRICTION:
            spec = FRICTION[self.scenario]
            offers = spec.get("targeted", []) if targeted else spec["offers"]
            if "FLIGHT" not in request.get("categories", ["FLIGHT"]):
                offers = []
            return [*self._shared(spec, request), *(Candidate(id=f"{owner}-{name}-{request['purpose'].lower()}-{int(now * 1000)}", owner=owner, total_cents=price,
                arrival_epoch=1_800_052_000 + int(hours * HOUR), arrival_timezone="Pacific/Honolulu", observed_at=now,
                valid_until=now + 3600, source_url=f"https://fixtures.invalid/{owner}/{name}", run_ref=f"fixture-{request['purpose']}",
                mode="FIXTURE", cost_basis="COMPLETE_FLIGHT_TOTAL", baggage_included=bag, flight_identity=f"{owner}-{name}")
                for owner, name, price, hours, bag in offers if owner in request["target_owners"])]
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


    @staticmethod
    def _shared(spec, request):
        now, wanted, tag = request["observed_at"], set(request.get("categories", ())), request["purpose"].lower()
        common = dict(observed_at=now, valid_until=now + 3600, run_ref=f"fixture-{request['purpose']}", mode="FIXTURE")
        items = []
        if "STAY" in wanted:
            items += [Candidate(id=f"stay-{name}-{tag}-{int(now * 1000)}", category="STAY", total_cents=price, capacity=guests,
                bedrooms=rooms, rating=rating, title=name.replace("-", " ").title(), cost_basis="COMPLETE_STAY_TOTAL",
                source_url=f"https://fixtures.invalid/stay/{name}", **common) for name, price, guests, rooms, rating in spec.get("stays", [])]
        if "TRANSFER" in wanted:
            items += [Candidate(id=f"transfer-{name}-{tag}-{int(now * 1000)}", category="TRANSFER", total_cents=price, capacity=seats,
                title=f"Airport {name}", cost_basis="COMPLETE_TRANSFER_TOTAL", source_url=f"https://fixtures.invalid/transfer/{name}",
                **common) for name, price, seats in spec.get("transfers", [])]
        items += [Candidate(id=f"{category.lower()}-{i}-{tag}-{int(now * 1000)}", category=category, total_cents=price, title=name,
            rating=rating, cost_basis="ESTIMATE_PER_PERSON", source_url=f"https://fixtures.invalid/{category.lower()}/{i}", **common)
            for i, (category, name, price, rating) in enumerate(spec.get("ideas", [])) if category in wanted]
        return items


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


# ---------- Onboarding personas ----------

PERSONAS = json.loads((Path(__file__).parent / "personas" / "travellers.json").read_text())


def persona(name: str) -> dict:
    return next(p for p in PERSONAS["travellers"] if p["name"] == name)


class PersonaModel(ScriptedModel):
    """Deterministic onboarding model for scripted personas. Extraction is the
    persona file's annotated reading of each message; questions fall back to the
    engine's own wording; the trip brief is the file's."""
    def extract(self, context):
        p, items, settings, unclear = persona(context["owner"]), [], None, []
        for message in context["messages"]:
            if message["sender"] != "HUMAN":
                continue
            for reply in p["replies"]:
                if reply["says"] not in message["body"]:
                    continue
                items += [{**item, "source_message_id": message["id"]} for item in reply.get("items", [])]
                if reply.get("settings"):
                    settings = {**reply["settings"], "source_message_id": message["id"]}
                unclear += reply.get("unclear", [])
        return IntakeExtraction(items=tuple(items), settings=settings, unclear=tuple(unclear))

    def ask(self, context):
        raise ValueError("scripted personas use the engine's fallback questions")

    def brief(self, context):
        return BriefDraft(brief=TripBrief(**PERSONAS["trip"]["brief"]))


class ScriptedTraveller:
    """Answers its agent like the persona would: the opener first, then whatever
    the agent asked about (up to two topics), each answer used once."""
    def __init__(self, name: str):
        self.persona, self.used = persona(name), set()

    def reply(self, asked: list[str]) -> str | None:
        said = []
        for topic in asked:
            for i, reply in enumerate(self.persona["replies"]):
                if i not in self.used and reply["when"] == topic:
                    self.used.add(i)
                    said.append(reply["says"])
                    break
        return " ".join(said) or None


def onboarded_offers(request):
    """Fixture offers for a room that searches from people's onboarding answers."""
    import airportsdata
    from .adapters.memo23 import local_epoch
    offers, now, wanted = PERSONAS["fixture_offers"], request["observed_at"], set(request["categories"])
    common = dict(observed_at=now, valid_until=now + 3600, run_ref=f"fixture-{request['purpose']}", mode="FIXTURE")
    tag = f"{request['purpose'].lower()}-{int(now * 1000)}"
    results = []
    if "FLIGHT" in wanted:
        for route in request["routes"]:
            if route["owner"] not in request["target_owners"]:
                continue
            zone = airportsdata.load("IATA")[route["destination"]]["tz"]
            for name, price, arrive, stops, bag in offers["flights"].get(route["origin"], []):
                if route.get("max_stops") is not None and stops > route["max_stops"]:
                    continue  # the search itself filters stops, like the live actor's maxStops
                results.append(Candidate(id=f"{route['owner']}-{name}-{tag}", owner=route["owner"], total_cents=price,
                    arrival_epoch=int(local_epoch(route["date"], arrive, zone)), arrival_timezone=zone, arrival_local=arrive,
                    stops=stops, baggage_included=bag, cost_basis="COMPLETE_FLIGHT_TOTAL", flight_identity=f"{route['owner']}-{name}",
                    source_url=f"https://fixtures.invalid/{route['origin']}/{name}", **common))
    trip = request.get("trip") or {}
    if "STAY" in wanted:
        results += [Candidate(id=f"stay-{i}-{tag}", category="STAY", total_cents=price, capacity=guests, bedrooms=rooms,
            rating=rating, title=title, cost_basis="COMPLETE_STAY_TOTAL", source_url=f"https://fixtures.invalid/stay/{i}", **common)
            for i, (title, price, guests, rooms, rating) in enumerate(offers["stays"])]
    if "TRANSFER" in wanted and trip.get("pickup_time"):
        results += [Candidate(id=f"transfer-{i}-{tag}", category="TRANSFER", total_cents=price, capacity=seats,
            title=f"{title}, pickup {trip['pickup_time']}", cost_basis="COMPLETE_TRANSFER_TOTAL",
            source_url=f"https://fixtures.invalid/transfer/{i}", **common) for i, (title, price, seats) in enumerate(offers["transfers"])]
    results += [Candidate(id=f"idea-{i}-{tag}", category=category, total_cents=price, title=title, rating=rating,
        cost_basis="ESTIMATE_PER_PERSON", source_url=f"https://fixtures.invalid/idea/{i}", **common)
        for i, (category, title, price, rating) in enumerate(offers["ideas"]) if category in wanted]
    return results
