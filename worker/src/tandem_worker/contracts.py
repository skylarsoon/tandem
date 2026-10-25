"""Versioned boundary types; private data never belongs in coordinator state."""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, model_validator


def digest(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class ContractError(Exception):
    """Safe code only: errors must not interpolate private inputs."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Preferences(Record):
    speak_up_for_me: bool = False
    ask_me_first: bool = True
    give_me_time: bool = True
    confirmed: bool = False
    revision: int = 1


class Brief(Record):
    owner: str
    hard_cap_cents: int = Field(ge=0)
    preferences: Preferences = Field(default_factory=Preferences)
    private_reason: str = ""
    baggage_required: bool = False
    # Explicit consent to reminders remains required even if Give me time is off.
    reminders_opted_in: bool = False
    # Whole-trip limit: own flight plus an equal share of the stay and transfer.
    trip_cap_cents: int | None = Field(default=None, ge=0)
    own_bedroom: bool = False
    stay_min_rating: float | None = Field(default=None, ge=0, le=5)
    # From onboarding: where they fly from, when they're willing to land
    # ({"earliest": "HH:MM", "latest": "HH:MM"}, local), how many stops, an
    # explicit "money's no issue", and private notes planning can't use yet.
    origin_airport: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    arrival_window: dict | None = None
    max_stops: int | None = Field(default=None, ge=0, le=3)
    no_budget_limit: bool = False
    notes: tuple[str, ...] = ()
    # Constraint keys the owner has actually answered (confirmed rows).
    answered: tuple[str, ...] = ()


class Commitment(Record):
    """A decision the whole group already agreed, and the candidates it chose."""
    decision_id: str
    scope: str
    refs: tuple[str, ...] = ()


class Snapshot(Record):
    room_id: str
    input_revision: int = 1
    membership_revision: int = 1
    permission_revision: int = 1
    members: tuple[str, ...]
    arrival_overlap_seconds: int = Field(default=7200, ge=0)
    policy_version: Literal["flight_cost_v1", "minimax_v1"] = "minimax_v1"
    # Parts the plan must choose: categories from contracts/decisions.json.
    components: tuple[str, ...] = ("FLIGHT",)
    # Travellers without an own-room request share this many to a bedroom.
    people_per_bedroom: int = Field(default=2, ge=1, le=8)
    # The room was described and onboarded: silence on a topic means "not asked
    # yet", so agents ask just in time instead of assuming.
    onboarding: bool = False
    # Members who confirmed their onboarding; nothing is proposed until it's everyone.
    onboarded: tuple[str, ...] = ()
    # The confirmed trip brief's facts searches need (destination airport, dates).
    destination_airport: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    destination: str | None = None
    # The organizer's order of decisions (catalog categories) and how arrivals relate.
    stages: tuple[str, ...] = ()
    arrive_together: Literal["required", "preferred", "off"] = "required"
    activities: int | None = None
    # Agreed decisions: what later planning builds on and must not silently drop.
    commitments: tuple[Commitment, ...] = ()
    # What those agreed decisions already cost each person, scaled by n (rules.committed_costs):
    # what's left of a budget for this stage. Agreed prices are in the shared terms, so not private.
    committed_scaled: dict[str, int] = Field(default_factory=dict)
    # Each traveller's cumulative approved compromise in this room (H_i, from the ledger).
    compromise_history: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_members(self):
        if not self.members or len(set(self.members)) != len(self.members) or "mediator" in self.members:
            raise ValueError("INVALID_MEMBERSHIP")
        from .rules import decisions
        if (set(self.components) | set(self.stages)) - set(decisions()["categories"]):
            raise ValueError("UNKNOWN_COMPONENT")
        return self


Category = Literal["FLIGHT", "STAY", "TRANSFER", "ACTIVITY", "DINNER"]
# A complete total can go into an approvable plan; the others are observations or estimates.
# Activities and dinners only ever have a per-person estimate, approved as such (as the database's PRICE_BASIS).
COMPLETE_BASIS = {"FLIGHT": "COMPLETE_FLIGHT_TOTAL", "STAY": "COMPLETE_STAY_TOTAL", "TRANSFER": "COMPLETE_TRANSFER_TOTAL",
                  "ACTIVITY": "ESTIMATE_PER_PERSON", "DINNER": "ESTIMATE_PER_PERSON"}
SHARED = ("STAY", "TRANSFER")


class Candidate(Record):
    id: str
    category: Category = "FLIGHT"
    # Flights belong to one traveller; stays, transfers and suggestions to the group (None).
    owner: str | None = None
    # Whole price for what this candidate covers (the full stay, the vehicle);
    # for ACTIVITY/DINNER, a per-person estimate.
    total_cents: int | None = Field(ge=0)
    currency: Literal["USD"] = "USD"
    arrival_epoch: int | None = None
    observed_at: float
    valid_until: float
    source_url: str
    run_ref: str
    mode: Literal["FIXTURE", "APIFY_OBSERVED", "PROVIDER_VERIFIED", "CACHED_WITH_TIMESTAMP", "SANDBOX"]
    cost_basis: Literal["COMPLETE_FLIGHT_TOTAL", "OBSERVED_FLIGHT_PRICE", "COMPLETE_STAY_TOTAL",
                        "COMPLETE_TRANSFER_TOTAL", "OBSERVED_PRICE", "ESTIMATE_PER_PERSON"]
    baggage_included: bool | None = None
    missing_critical_fields: tuple[str, ...] = ()
    actor_id: str | None = None
    actor_build: str | None = None
    dataset_ref: str | None = None
    flight_identity: str | None = None
    departure_epoch: int | None = None
    departure_timezone: str | None = None
    arrival_timezone: str | None = None
    # Local wall-clock arrival "HH:MM" at the destination, and connections.
    arrival_local: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    stops: int | None = Field(default=None, ge=0)
    # Local dates and travel times, derived from the epochs and zones when not given.
    departure_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    arrival_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    duration_minutes: int | None = Field(default=None, ge=0)
    # A round trip is one itinerary: the way home leaves the destination, lands at the origin.
    return_departure_epoch: int | None = None
    return_arrival_epoch: int | None = None
    return_departure_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    return_arrival_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    return_stops: int | None = Field(default=None, ge=0)
    return_duration_minutes: int | None = Field(default=None, ge=0)
    # A one-way search's result, before it's paired into an itinerary (None: already whole).
    leg: Literal["outbound", "return"] | None = None
    checked_bags_included: int | None = None
    # valid_until is a local freshness deadline, never a held fare's expiry.
    provider_expires_at: float | None = None
    # Stays, transfers and suggestions.
    title: str | None = None
    capacity: int | None = Field(default=None, ge=1)  # guests (stay) or passengers (transfer)
    bedrooms: int | None = Field(default=None, ge=0)
    rating: float | None = Field(default=None, ge=0, le=5)  # normalized to 0-5
    free_cancellation: bool | None = None
    # What a venue serves, as tags ("vegetarian", "vegan"), when it says; None when it doesn't.
    dietary_options: tuple[str, ...] | None = None
    check_in: str | None = None
    check_out: str | None = None

    @model_validator(mode="before")
    @classmethod
    def derived_facts(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)

        def local_date(epoch, zone):
            from datetime import datetime
            from zoneinfo import ZoneInfo
            return datetime.fromtimestamp(epoch, ZoneInfo(zone)).date().isoformat()

        out_zone, there_zone = data.get("departure_timezone"), data.get("arrival_timezone")
        for field, epoch, zone in (("departure_date", "departure_epoch", out_zone), ("arrival_date", "arrival_epoch", there_zone),
                                   ("return_departure_date", "return_departure_epoch", there_zone),
                                   ("return_arrival_date", "return_arrival_epoch", out_zone)):
            if data.get(field) is None and data.get(epoch) is not None and zone:
                data[field] = local_date(data[epoch], zone)
        for field, start, end in (("duration_minutes", "departure_epoch", "arrival_epoch"),
                                  ("return_duration_minutes", "return_departure_epoch", "return_arrival_epoch")):
            if data.get(field) is None and data.get(start) is not None and data.get(end) is not None and data[end] >= data[start]:
                data[field] = (data[end] - data[start]) // 60
        return data

    @model_validator(mode="after")
    def valid_evidence(self):
        if self.valid_until <= self.observed_at or not self.source_url or not self.run_ref:
            raise ValueError("INVALID_EVIDENCE")
        if (self.category == "FLIGHT") != (self.owner is not None):
            raise ValueError("INVALID_OWNER")
        if self.category == "FLIGHT" and self.arrival_epoch is None:
            raise ValueError("INVALID_EVIDENCE")
        return self

    @property
    def complete(self) -> bool:
        return (self.total_cents is not None and not self.missing_critical_fields
                and self.cost_basis == COMPLETE_BASIS.get(self.category))


class Claim(Record):
    job_id: str
    worker_id: str
    attempt_token: str
    generation: str
    graph_thread_ref: str
    snapshot_ref: str
    lease_expiry: float
    kind: Literal["PLAN", "INTERPRET", "INTAKE", "PROFILE"] = "PLAN"


class Act(StrEnum):
    PROPOSE = "PROPOSE"
    EVALUATE = "EVALUATE"
    OBJECT = "OBJECT"
    COUNTERPROPOSE = "COUNTERPROPOSE"
    CLARIFY = "CLARIFY"
    OWNER_RESPONSE = "OWNER_RESPONSE"
    VALIDATE_REQUEST = "VALIDATE_REQUEST"
    VALIDATION_RESULT = "VALIDATION_RESULT"
    REQUEST_HUMAN_REVIEW = "REQUEST_HUMAN_REVIEW"
    ROUND_PAUSED = "ROUND_PAUSED"
    ROUND_CLOSED = "ROUND_CLOSED"


class Decision(Record):
    act: Act = Act.EVALUATE
    evaluation: Literal["ELIGIBLE", "INELIGIBLE", "NEEDS_OWNER_INPUT", "INCOMPLETE_EVIDENCE"] = "ELIGIBLE"
    recipient: str = "mediator"
    blocker: Literal["NONE", "BAGGAGE_UNKNOWN", "HARD_LIMIT", "MISSING_FACT"] = "NONE"
    research: bool = False
    concession: bool = False
    exceeds_exploration: bool = False
    # With NEEDS_OWNER_INPUT: one short question for the owner only (never the room).
    owner_question: str = Field(default="", max_length=300)


class Payload(Record):
    draft_ref: str
    evaluation: str
    blocker: Literal["NONE", "BAGGAGE_UNKNOWN", "HARD_LIMIT", "MISSING_FACT"] = "NONE"
    candidate_refs: tuple[str, ...] = ()
    research: bool = False
    concession: bool = False
    exceeds_exploration: bool = False


class ValidationMetadata(Record):
    validator_version: Literal["voice-v1"] = "voice-v1"
    snapshot_ref: str
    permission_revision: int
    status: Literal["VALIDATED", "TEMPLATE_FALLBACK"]
    content_digest: str


class Message(Record):
    schema_version: Literal[2] = 2
    message_id: str
    room_id: str
    thread_id: str
    round_id: int = Field(ge=0)
    input_revision: int
    membership_revision: int
    permission_revision: int
    sender_agent_id: str
    recipient_agent_id: str
    type: Act
    reply_to: str | None = None
    payload: Payload
    utterance: str
    audience: Literal["PUBLIC_SUMMARY", "OWNER_PRIVATE", "INTERNAL"] = "PUBLIC_SUMMARY"
    authority_scope: Literal["PROPOSE_ONLY"] = "PROPOSE_ONLY"
    evidence_refs: tuple[str, ...] = ()
    claim_refs: tuple[str, ...] = ()
    idempotency_key: str
    worker_validation: ValidationMetadata

    def checked_content(self) -> dict:
        return {
            "type": self.type.value, "utterance": self.utterance, "payload": self.payload.model_dump(mode="json"),
            "claim_refs": self.claim_refs, "evidence_refs": self.evidence_refs,
        }


class VoiceSegment(Record):
    fact_ref: str
    text: str


class VoiceDraft(Record):
    # Free-form claims are deliberately not accepted. Claude selects/orders
    # canonical permitted sentences, with a small checked connective vocabulary.
    segments: tuple[VoiceSegment, ...] = Field(min_length=1, max_length=16)
    connector: Literal[" ", " Also, ", " Next, "] = " "


class Question(Record):
    id: str
    owner: str
    version: int = 1
    kind: Literal["PARTICIPATION", "DISCLOSURE", "CONSULTATION", "EXPLORE", "MISSING_FACT"]
    action_ref: str
    snapshot_ref: str
    answer: bool | None = None
    # Owner-only wording of exactly what is being asked (never shown to the room).
    prompt: str = ""
    # A just-in-time question: the constraint each answer records, {"yes": patch, "no": patch}.
    nudge: dict | None = None


class Impact(Record):
    """One traveller's position in a plan. Private to that traveller."""
    owner: str
    candidate: str | None = None  # their own pick, if the plan has one (a stage of shared picks has none)
    price_cents: int  # what they pay in total: their own pick plus their share
    cheapest_alone_cents: int
    extra_cents: int = Field(ge=0)
    reason: Literal["NONE", "ARRIVAL_WINDOW", "SHARED_CHOICE"] = "NONE"
    flight_cents: int | None = None
    share_cents: int = 0  # their share of the shared picks (split, or per head)
    # Their preferences this plan doesn't fully meet, in plain words with their quote.
    unmet: tuple[str, ...] = ()


class GroupSummary(Record):
    """Room-safe facts about a plan: no caps, reasons or names."""
    total_cents: int
    # Arrivals, when the plan includes travel.
    earliest_arrival: int | None = None
    latest_arrival: int | None = None
    arrival_window_seconds: int
    travellers_paying_extra: int = 0
    max_extra_cents: int = 0
    # The cheapest combination ignoring the shared arrival rule, when it differs.
    cheapest_total_cents: int | None = None
    cheapest_spread_seconds: int | None = None
    stay: str | None = None
    stay_total_cents: int | None = None
    transfer: str | None = None
    transfer_total_cents: int | None = None
    share_each_cents: int = 0
    # How much the group's room requirements add over the cheapest stay that fits everyone.
    stay_premium_cents: int = 0


class Conflict(Record):
    """Why no plan exists. Owner-specific conflicts go only to that owner."""
    code: Literal["HARD_LIMIT", "BAGGAGE_UNAVAILABLE", "NO_CANDIDATES", "ARRIVAL_WINDOW",
                  "TRIP_LIMIT", "STAY_UNAVAILABLE", "TRANSFER_UNAVAILABLE", "ARRIVAL_TIME", "TOO_MANY_STOPS", "RULE",
                  "DECLINED"]
    owner: str | None = None
    # RULE: the attributes whose rules (the owner's own, or the trip's dates) left them no option.
    attributes: tuple[str, ...] = ()
    # DECLINED: how many options they were asked about and said no to.
    declined: int = 0
    cheapest_cents: int | None = None
    # TRIP_LIMIT: the cheapest trip for this owner and their limit; STAY_UNAVAILABLE:
    # which requirement no stay meets (never whose).
    trip_cap_cents: int | None = None
    requirement: Literal["CAPACITY", "BEDROOMS", "RATING", "ARRIVAL_TIME", "STOPS"] | None = None
    closest_spread_seconds: int | None = None
    required_seconds: int | None = None


class ImpactNote(Record):
    impact: Impact
    text: str


class Suggestion(Record):
    """An activity or dinner idea with an estimated per-person price. Never part
    of what people approve."""
    candidate: str
    category: Literal["ACTIVITY", "DINNER"]
    title: str
    estimate_per_person_cents: int | None = None
    rating: float | None = None
    source_url: str


class Explanation(Record):
    """Published with a proposal: `summary`/`group`/`suggestions` are room-safe;
    each impact note goes only to its owner."""
    summary: str
    group: GroupSummary
    impacts: tuple[ImpactNote, ...] = ()
    suggestions: tuple[Suggestion, ...] = ()


class SolverResult(Record):
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "MODEL_INVALID", "UNKNOWN"]
    selected: tuple[str, ...] = ()
    policy_version: str = "flight_cost_v1"
    # Each person's compromise in the chosen plan (0..1), for the ledger once it's agreed.
    burdens: dict[str, float] = Field(default_factory=dict)
    impacts: tuple[Impact, ...] = ()
    group: GroupSummary | None = None
    conflicts: tuple[Conflict, ...] = ()


class GroupShortlist(Record):
    """One selection group's shortlist (a traveller's flights), with what was
    left out and why, so nothing disappears silently."""
    group: str
    found: int
    valid: int
    kept: tuple[str, ...]
    excluded: dict[str, int] = Field(default_factory=dict)  # reason -> count
    untried_valid: int = 0  # valid options never yet shown to the solver


class Shortlist(Record):
    """Candidates for the solver: at most `limit` per group, plus everything
    outside the shortlisted category untouched. A heuristic: an infeasible
    shortlist proves nothing about the options left out."""
    candidates: tuple[Candidate, ...]
    groups: tuple[GroupShortlist, ...]
    empty_groups: tuple[str, ...] = ()
    lock_conflicts: tuple[str, ...] = ()

    @property
    def truncated(self) -> bool:
        return any(g.valid > len(g.kept) for g in self.groups)


class Route(Record):
    """One traveller's flight search, built from what they confirmed in onboarding."""
    owner: str
    origin: str = Field(pattern=r"^[A-Z]{3}$")
    destination: str = Field(pattern=r"^[A-Z]{3}$")
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    # The way home: the latest day this person can come back (their rules, else the trip's end).
    return_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    max_stops: int | None = Field(default=None, ge=0, le=3)


class TripFacts(Record):
    """What the shared searches need, from the confirmed brief."""
    destination: str
    check_in: str
    check_out: str
    party_size: int = Field(ge=1, le=20)
    pickup_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")


class ResearchRequest(Record):
    purpose: Literal["INITIAL", "TARGETED", "REFRESH"]
    members: tuple[str, ...]
    target_owners: tuple[str, ...]
    scope: Literal["FLIGHTS_ONLY", "TRIP"] = "FLIGHTS_ONLY"
    room_id: str
    input_revision: int
    permission_revision: int
    observed_at: float
    blocker_message_ref: str = ""
    blocker_code: Literal["NONE", "HARD_LIMIT", "BAGGAGE_UNKNOWN", "MISSING_FACT", "NO_MATCHING_CANDIDATE", "ARRIVAL_OVERLAP",
                          "STAY_UNAVAILABLE", "TRANSFER_UNAVAILABLE"] = "NONE"
    # Which categories to search; flights are per target owner, the others are shared.
    categories: tuple[Category, ...] = ("FLIGHT",)
    # Onboarded rooms search from people's answers; other rooms use configured searches.
    routes: tuple[Route, ...] = ()
    trip: TripFacts | None = None

    @model_validator(mode="after")
    def check_targets(self):
        if not self.target_owners or not set(self.target_owners) <= set(self.members):
            raise ValueError("INVALID_RESEARCH_TARGET")
        if self.purpose == "TARGETED" and self.blocker_code == "NONE":
            raise ValueError("TARGETED_BLOCKER_REQUIRED")
        return self


class ValidationReport(Record):
    valid: bool
    snapshot_ref: str
    candidate_digest: str
    selected: tuple[str, ...]
    checks: tuple[str, ...]
    validator_version: Literal["flight-validator-v1", "trip-validator-v1"] = "flight-validator-v1"


class Limits(Record):
    lease_seconds: float = Field(default=60, gt=0)
    heartbeat_seconds: float = Field(default=15, gt=0)
    rounds: int = Field(default=3, gt=0)
    thread_turns: int = Field(default=6, gt=0)
    round_turns: int = Field(default=24, gt=0)
    model_calls: int = Field(default=100, gt=0)
    research_calls: int = Field(default=4, gt=0)
    solver_seconds: float = Field(default=5, gt=0)
    # Most flights per traveller the solver sees (the optimizer tries every
    # combination), and how many differently-shortlisted tries before searching again.
    shortlist_size: int = Field(default=5, ge=1, le=50)
    shortlist_passes: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def heartbeat_before_expiry(self):
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("HEARTBEAT_MUST_PRECEDE_EXPIRY")
        return self


# ---------- Conversational onboarding ----------

Topic = Literal["origin", "dates", "money", "settings"]
CoverageState = Literal["OPEN", "ANSWERED", "UNCLEAR", "SKIPPED"]
TOPICS: tuple[str, ...] = ("origin", "dates", "money", "settings")
IntakeKey = Literal["origin_airport", "dates_ok", "flight_budget_total", "trip_budget_total", "no_budget_limit",
                    "baggage_required", "own_bedroom", "stay_min_rating", "arrival_window", "max_stops", "trip_note", "rule"]


class TripBrief(Record):
    """The organizer's trip, as their agent understood it and they confirmed."""
    destination: str = Field(min_length=1, max_length=120)
    destination_airport: str = Field(pattern=r"^[A-Z]{3}$")
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    components: tuple[Literal["FLIGHT", "STAY", "TRANSFER"], ...] = ("FLIGHT",)
    group_size: int | None = Field(default=None, ge=1, le=20)
    vibe: str = Field(default="", max_length=200)
    # Only what the organizer said: how arrivals relate, the order to decide things in
    # (catalog categories, any of them), and how many group activities to plan.
    arrive_together: Literal["required", "preferred", "off"] | None = None
    stages: tuple[str, ...] | None = None
    activities: int | None = Field(default=None, ge=0, le=20)

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.start_date > self.end_date:
            raise ValueError("TRIP_DATES_OUT_OF_ORDER")
        from .rules import decisions
        if self.stages is not None and (not self.stages or len(set(self.stages)) != len(self.stages)
                                        or set(self.stages) - set(decisions()["categories"])):
            raise ValueError("UNKNOWN_STAGE")
        return self


class BriefDraft(Record):
    """Model output for a trip description: a brief only if every field was stated,
    plus what to ask the organizer about."""
    brief: TripBrief | None = None
    unclear: tuple[str, ...] = ()


# The model's output is schema-constrained, and a structured-output schema closes every
# object to the fields it names: an untyped dict could only ever come back empty. So the
# dicts a traveller's words become are given their shapes here (and stay dicts in code).
_NULLABLE = lambda schema: {"anyOf": [schema, {"type": "null"}]}  # noqa: E731
_TEXT = _NULLABLE({"type": "string"})
RULE_SCHEMA = _NULLABLE({"type": "object", "additionalProperties": False,
    "required": ["category", "attribute", "aggregate", "operator", "value"],
    "properties": {"category": _TEXT, "attribute": _TEXT, "aggregate": _TEXT,
                   "operator": {"type": "string", "enum": ["eq", "neq", "lt", "lte", "gt", "gte", "in", "not_in", "between",
                                                           "lowest", "highest", "same_as", "contains"]},
                   "value": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "null"},
                                       {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "number"}]}}]}}})
VALUE_SCHEMA = {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"}, {"type": "null"},
                          {"type": "object", "additionalProperties": False, "required": ["earliest", "latest"],
                           "properties": {"earliest": _TEXT, "latest": _TEXT}}]}
RANGE_SCHEMA = _NULLABLE({"type": "object", "additionalProperties": False, "required": ["max"],
                          "properties": {"max": {"type": "integer"}}})


class IntakeItem(Record):
    """One thing the traveller said, with their exact words. `interpreted` marks a
    reading the summary must spell out (e.g. "no red-eyes" → land 06:00-23:00)."""
    key: IntakeKey
    value: Annotated[str | int | float | bool | dict[str, str | None] | None, WithJsonSchema(VALUE_SCHEMA)] = None
    # key "rule": anything checkable, in the generic language
    # ({category, attribute | aggregate, operator, value}; see contracts/attributes.json).
    rule: Annotated[dict | None, WithJsonSchema(RULE_SCHEMA)] = None
    hardness: Literal["HARD", "SOFT"] = "HARD"
    authorized_range: Annotated[dict[str, int] | None, WithJsonSchema(RANGE_SCHEMA)] = None

    @model_validator(mode="after")
    def drop_empty_rule_fields(self):
        # The schema makes every rule field present; a rule names only what it uses.
        if self.rule:
            object.__setattr__(self, "rule", {k: v for k, v in self.rule.items() if v is not None})
        return self
    unit: str = ""
    private_rationale: str = Field(default="", max_length=300)
    source_message_id: int
    source_quote: str = Field(min_length=1, max_length=300)
    interpreted: bool = False


class ProfileSummary(Record):
    """The agent's "Does this sound like you?" summary of a person's profile."""
    summary: str = Field(min_length=1, max_length=1200)


class IntakeSettings(Record):
    speak_up_for_me: bool | None = None
    ask_me_first: bool | None = None
    source_message_id: int | None = None
    source_quote: str = ""


class Unclear(Record):
    topic: Topic
    reason: str = Field(max_length=200)


class IntakeExtraction(Record):
    items: tuple[IntakeItem, ...] = ()
    settings: IntakeSettings | None = None
    unclear: tuple[Unclear, ...] = ()


class IntakeQuestion(Record):
    text: str = Field(min_length=5, max_length=300)
    topics: tuple[Topic, ...] = Field(min_length=1, max_length=2)


class IntakeMessage(Record):
    id: int
    sender: Literal["HUMAN", "AGENT"]
    body: str


class IntakeContext(Record):
    """Everything an onboarding job may see: the shared brief and this one
    traveller's own conversation. Never anyone else's."""
    member_id: int
    owner: str
    purpose: Literal["BRIEF", "INTAKE", "MESSAGE"]
    display_name: str = ""
    is_organizer: bool = False
    others: tuple[str, ...] = ()
    description: str = ""
    brief: TripBrief | None = None
    brief_state: Literal["DRAFT", "PROPOSED", "CONFIRMED"] | None = None
    messages: tuple[IntakeMessage, ...] = ()
    pending: tuple[dict, ...] = ()
    confirmed_keys: tuple[str, ...] = ()
    intake_state: Literal["ASKING", "SUMMARY", "CONFIRMED"] | None = None
    coverage: dict = Field(default_factory=dict)
    question_count: int = 0
    preferences_confirmed: bool = False
    # Their persistent profile answers (question id -> value): defaults to offer
    # for this trip, never to assume.
    profile: dict = Field(default_factory=dict)
