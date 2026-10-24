"""Versioned boundary types; private data never belongs in coordinator state."""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class Snapshot(Record):
    room_id: str
    input_revision: int = 1
    membership_revision: int = 1
    permission_revision: int = 1
    members: tuple[str, ...]
    arrival_overlap_seconds: int = Field(default=7200, ge=0)
    policy_version: Literal["flight_cost_v1"] = "flight_cost_v1"

    @model_validator(mode="after")
    def valid_members(self):
        if not self.members or len(set(self.members)) != len(self.members) or "mediator" in self.members:
            raise ValueError("INVALID_MEMBERSHIP")
        return self


class Candidate(Record):
    id: str
    owner: str
    total_cents: int | None = Field(ge=0)
    currency: Literal["USD"] = "USD"
    arrival_epoch: int
    observed_at: float
    valid_until: float
    source_url: str
    run_ref: str
    mode: Literal["FIXTURE", "APIFY_OBSERVED", "PROVIDER_VERIFIED", "CACHED_WITH_TIMESTAMP", "SANDBOX"]
    cost_basis: Literal["COMPLETE_FLIGHT_TOTAL"]
    baggage_included: bool | None = None

    @model_validator(mode="after")
    def valid_evidence(self):
        if self.valid_until <= self.observed_at or not self.source_url or not self.run_ref:
            raise ValueError("INVALID_EVIDENCE")
        return self


class Claim(Record):
    job_id: str
    worker_id: str
    attempt_token: str
    generation: str
    graph_thread_ref: str
    snapshot_ref: str
    lease_expiry: float


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
    segments: tuple[VoiceSegment, ...] = Field(min_length=1, max_length=8)
    connector: Literal[" ", " Also, ", " Next, "] = " "


class Question(Record):
    id: str
    owner: str
    version: int = 1
    kind: Literal["PARTICIPATION", "DISCLOSURE", "CONSULTATION", "EXPLORE", "MISSING_FACT"]
    action_ref: str
    snapshot_ref: str
    answer: bool | None = None


class SolverResult(Record):
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "MODEL_INVALID", "UNKNOWN"]
    selected: tuple[str, ...] = ()


class ValidationReport(Record):
    valid: bool
    snapshot_ref: str
    candidate_digest: str
    selected: tuple[str, ...]
    checks: tuple[str, ...]
    validator_version: Literal["flight-validator-v1"] = "flight-validator-v1"


class Limits(Record):
    lease_seconds: float = Field(default=60, gt=0)
    heartbeat_seconds: float = Field(default=15, gt=0)
    rounds: int = Field(default=3, gt=0)
    thread_turns: int = Field(default=6, gt=0)
    round_turns: int = Field(default=24, gt=0)
    model_calls: int = Field(default=100, gt=0)
    research_calls: int = Field(default=4, gt=0)
    solver_seconds: float = Field(default=5, gt=0)

    @model_validator(mode="after")
    def heartbeat_before_expiry(self):
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("HEARTBEAT_MUST_PRECEDE_EXPIRY")
        return self
