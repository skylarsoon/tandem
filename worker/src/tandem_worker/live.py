"""Opt-in provider wiring and standalone diagnostics. No SpacetimeDB adapter."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .adapters.apify import ApifyFlightResearch, ApifyHTTP, RunLedger
from .adapters.claude import ClaudeModel
from .adapters.memo23 import ACTOR_ID, BUILD_ID, NORMALIZER_VERSION, build_input, normalize_item
from .budget import ProviderBudget
from .contracts import Act, ContractError, Decision, Limits, Payload, ResearchRequest, Snapshot, VoiceDraft, digest
from .policy import fallback_voice, needed_permissions, permitted_facts, validate_voice
from .ports import SystemClock


class CheckedResearch:
    """Require read-only preflight before the first billable Actor start."""
    def __init__(self, gateway):
        self.gateway, self.verified = gateway, False

    def search(self, request):
        if not self.verified:
            raise ContractError("LIVE_PREFLIGHT_REQUIRED")
        try:
            request = ResearchRequest.model_validate(request).model_dump(mode="json")
        except ValidationError:
            raise ContractError("INVALID_RESEARCH_REQUEST") from None
        return self.gateway.search(request)


@dataclass
class Providers:
    model: ClaudeModel
    research: CheckedResearch
    client: object
    budget: ProviderBudget
    ledger: RunLedger
    build: str

    def preflight(self):
        claude = self.model.check_access()
        apify = self.client.check_actor(ACTOR_ID, self.build, BUILD_ID)
        self.research.verified = True
        return {"claude": claude, "apify": apify}

    def report(self):
        return {"providers": {"model": "CLAUDE", "research": "APIFY"}, "backend": "LOCAL_TEST",
                "usage": self.budget.report(), "research_runs": self.ledger.report()}


def make_providers(config, settings, directory, *, clock=None, claude_client=None, apify_client=None, snapshot=None):
    settings.require()
    clock = clock or SystemClock()
    config.validate_dates(clock.now())
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot or Snapshot(room_id=config.room_id, members=tuple(b.owner for b in config.briefs))
    if snapshot.room_id != config.room_id or {s.owner for s in config.searches} != set(snapshot.members):
        raise ContractError("SEARCH_SNAPSHOT_MISMATCH")
    # Permission revisions may supersede a job, but cannot reset this execution's
    # paid-call allowance. Run keys themselves still bind the current revisions.
    scope = digest([config.model_dump(mode="json"), settings.claude_model, settings.apify_actor_build])
    budget = ProviderBudget(directory / "providers.sqlite", scope,
        model_calls=config.limits.model_calls, actor_runs=config.limits.actor_runs)
    model = ClaudeModel(settings.claude_model, settings.anthropic_api_key.get_secret_value(),
        client=claude_client, budget=budget, max_tokens=config.limits.max_output_tokens, timeout=config.limits.http_timeout)
    client = apify_client or ApifyHTTP(settings.apify_token.get_secret_value(), timeout=config.limits.http_timeout)
    ledger = RunLedger(directory / "runs.sqlite")
    gateway = ApifyFlightResearch(client, ledger, actor_id=ACTOR_ID, build=settings.apify_actor_build,
        room_id=snapshot.room_id, input_revision=snapshot.input_revision, permission_revision=snapshot.permission_revision,
        approved_searches=list(config.searches), clock=clock, budget=budget,
        build_input=lambda search, purpose: build_input(search, {"purpose": purpose}, config.limits.max_items),
        request_input_builder=lambda search, request: build_input(search, request, config.limits.max_items),
        normalize_item=lambda item, search, run: normalize_item(item, search, run, freshness_seconds=config.limits.freshness_seconds),
        max_items=config.limits.max_items, max_charge_usd=config.limits.max_charge_usd,
        poll_deadline_seconds=config.limits.poll_deadline_seconds, normalizer_version=NORMALIZER_VERSION)
    return Providers(model, CheckedResearch(gateway), client, budget, ledger, settings.apify_actor_build)


def check_providers(config, settings, *, online=False, clock=None, claude_client=None, apify_client=None):
    clock = clock or SystemClock()
    config.validate_dates(clock.now())
    result = {"status": "OK", "configuration": "VALID", "missing": settings.missing(),
        "model": settings.claude_model, "actor_id": ACTOR_ID, "actor_build": settings.apify_actor_build,
        "message_requests": 0, "actor_runs": 0}
    if result["missing"]:
        result["status"] = "CONFIG_REQUIRED"
        return result
    settings.require()
    if online:
        model = ClaudeModel(settings.claude_model, settings.anthropic_api_key.get_secret_value(),
                            client=claude_client, timeout=config.limits.http_timeout)
        client = apify_client or ApifyHTTP(settings.apify_token.get_secret_value(), timeout=config.limits.http_timeout)
        result["access"] = {"claude": model.check_access(), "apify": client.check_actor(ACTOR_ID, settings.apify_actor_build, BUILD_ID)}
    return result


def bind_session(directory, config, settings, kind, now):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "session.json"
    fingerprint = digest([config.model_dump(mode="json"), settings.claude_model, settings.apify_actor_build, kind])
    session = {"fingerprint": fingerprint, "kind": kind, "observed_at": now}
    try:
        with path.open("x") as handle:
            json.dump(session, handle)
    except FileExistsError:
        try:
            session = json.loads(path.read_text())
        except (OSError, ValueError):
            raise ContractError("INVALID_LIVE_STATE") from None
        if session.get("fingerprint") != fingerprint:
            raise ContractError("LIVE_STATE_CONFIG_MISMATCH")
    return session


def smoke(config, settings, directory, *, clock=None, claude_client=None, apify_client=None):
    """Exercise real provider ports, without inventing agreement or a conflict."""
    clock = clock or SystemClock()
    config.validate_dates(clock.now())
    settings.require()
    if (len(config.briefs) != 1 or config.limits.actor_runs != 1 or config.limits.model_calls > 4
            or config.limits.max_charge_usd > 1 or config.limits.max_items > 10 or config.limits.max_output_tokens > 512):
        raise ContractError("SMOKE_REQUIRES_ONE_PARTICIPANT_AND_SMALL_LIMITS")
    session = bind_session(directory, config, settings, "smoke", clock.now())
    directory = Path(directory)
    cached = directory / "smoke-result.json"
    if cached.exists():
        result = json.loads(cached.read_text())
        if result["fresh_until"] <= clock.now():
            raise ContractError("SMOKE_EVIDENCE_STALE")
        return {**result, "cached": True}
    providers = make_providers(config, settings, directory, clock=clock, claude_client=claude_client, apify_client=apify_client)
    providers.preflight()
    brief = config.briefs[0]
    request = ResearchRequest(purpose="INITIAL", members=(brief.owner,), target_owners=(brief.owner,),
        room_id=config.room_id, input_revision=1, permission_revision=1, observed_at=session["observed_at"])
    candidates = providers.research.search(request.model_dump(mode="json"))
    if not candidates:
        raise ContractError("NO_USABLE_APIFY_EVIDENCE")
    candidate = min(candidates, key=lambda c: c.total_cents if c.total_cents is not None else float("inf"))
    if candidate.valid_until <= clock.now():
        raise ContractError("SMOKE_EVIDENCE_STALE")
    context = {"owner": brief.owner, "hard_cap_cents": brief.hard_cap_cents, "private_reason": brief.private_reason,
        "preferences": brief.preferences.model_dump(mode="json"), "baggage_required": brief.baggage_required,
        "candidate": candidate.model_dump(mode="json"), "sender": "mediator", "inbound_type": "PROPOSE",
        "inbound_payload": {"candidate_refs": [candidate.id]}, "inbound_evidence": [candidate.model_dump(mode="json")],
        "permitted_recipients": ["mediator"], "missing_fact_answered": False}
    decision = None
    for _ in range(2):
        try:
            decision = Decision.model_validate(providers.model.decide(context))
            if decision.recipient != "mediator" or decision.act not in {Act.EVALUATE, Act.OBJECT, Act.COUNTERPROPOSE, Act.CLARIFY}:
                raise ValueError("INVALID_AGENT_ACT")
            break
        except (ValueError, ValidationError):
            decision = None
    if decision is None:
        raise ContractError("INVALID_MODEL_OUTPUT")
    payload = Payload(draft_ref="provider-smoke", evaluation=decision.evaluation, blocker=decision.blocker,
        candidate_refs=(candidate.id,), research=decision.research, concession=decision.concession,
        exceeds_exploration=decision.exceeds_exploration)
    facts = permitted_facts(decision.act, payload)
    text, refs = fallback_voice(facts)
    voice_status = "TEMPLATE_FALLBACK"
    for _ in range(2):
        try:
            text, refs = validate_voice(VoiceDraft.model_validate(providers.model.voice({"act": decision.act.value, "facts": facts})), facts)
            voice_status = "VALIDATED"
            break
        except (ValueError, ValidationError, ContractError) as error:
            if isinstance(error, ContractError) and error.code != "UTTERANCE_PAYLOAD_MISMATCH":
                raise
    permissions = needed_permissions(brief, decision)
    if decision.evaluation == "NEEDS_OWNER_INPUT":
        permissions.append("MISSING_FACT")
    from .solver import FlightValidator
    snapshot = Snapshot(room_id=config.room_id, members=(brief.owner,))
    validation = FlightValidator().validate(snapshot, [brief], candidates, (candidate.id,), clock.now())
    result = {"status": "OK" if voice_status == "VALIDATED" else "VOICE_TEMPLATE_FALLBACK", "mode": "LIVE_PROVIDERS",
        "cached": False, "fresh_until": candidate.valid_until, "candidate": candidate.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"), "voice_status": voice_status,
        "utterance": None if permissions else text, "claim_refs": refs,
        "consultation_required": permissions, "planning_outcome": "EXPLORATORY_ONLY",
        "validation_checks": list(validation.checks), "consent_count": 0,
        **providers.report()}
    with cached.open("w") as handle:
        json.dump(result, handle, indent=2)
    return result


def run_live(config, settings, directory, *, clock=None, claude_client=None, apify_client=None):
    from .backend import LocalBackend
    from .runner import Worker
    clock = clock or SystemClock()
    config.validate_dates(clock.now())
    settings.require()
    bind_session(directory, config, settings, "run", clock.now())
    directory = Path(directory)
    snapshot = Snapshot(room_id=config.room_id, members=tuple(b.owner for b in config.briefs))
    limits = Limits(model_calls=config.limits.model_calls, research_calls=config.limits.actor_runs)
    backend = LocalBackend(directory / "backend.sqlite", clock, limits)
    try:
        job_id = backend.seed(snapshot, list(config.briefs))
    except ContractError as error:
        if error.code != "ALREADY_SEEDED":
            raise
        jobs = backend.eligible_jobs()
        job_id = jobs[0] if jobs else None
    providers = make_providers(config, settings, directory, clock=clock, claude_client=claude_client,
        apify_client=apify_client, snapshot=backend.current_snapshot())
    if job_id is None:
        return {"mode": "LIVE_PROVIDERS", "result": {"outcome": "NO_ELIGIBLE_WORK"}, "report": backend.shared_report(), **providers.report()}
    providers.preflight()
    worker = Worker(backend, providers.model, providers.research, directory / "checkpoints.sqlite", clock=clock, limits=limits,
                    worker_id="live-local-worker-" + uuid.uuid4().hex[:8])
    result = worker.run_one(job_id)
    return {"mode": "LIVE_PROVIDERS", "result": result, "report": backend.shared_report(), **providers.report()}
