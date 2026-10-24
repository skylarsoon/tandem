"""Opt-in provider wiring, standalone diagnostics, and live runs against SpacetimeDB."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .adapters.apify import ApifyFlightResearch, ApifyHTTP, ApifyResearch, RunLedger, TripResearch
from .adapters.claude import ClaudeModel
from .adapters.memo23 import ACTOR_ID, BUILD_ID, NORMALIZER_VERSION, build_input, normalize_offers
from .adapters.trip_actors import ACTORS
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
    # Shared trip categories with a configured search, e.g. ("STAY", "TRANSFER", "DINNER").
    trip_categories: tuple[str, ...] = ()

    @property
    def components(self):
        """Parts a plan must choose: FLIGHT plus each configured STAY/TRANSFER."""
        return ("FLIGHT",) + tuple(c for c in ("STAY", "TRANSFER") if c in self.trip_categories)

    def preflight(self):
        claude = self.model.check_access()
        apify = self.client.check_actor(ACTOR_ID, self.build, BUILD_ID)
        result = {"claude": claude, "apify": apify}
        if self.trip_categories:
            result["trip"] = {c: self.client.check_actor(ACTORS[c].actor_id, ACTORS[c].build, ACTORS[c].build_id)
                              for c in self.trip_categories}
        self.research.verified = True
        return result

    def report(self, backend="NONE"):
        return {"providers": {"model": "CLAUDE", "research": "APIFY"}, "backend": backend,
                "components": list(self.components), "trip_categories": list(self.trip_categories),
                "usage": self.budget.report(), "research_runs": self.ledger.report()}


def trip_gateways(config, client, ledger, snapshot, clock, budget):
    """One shared gateway per configured trip category, on the same client,
    ledger and paid-run budget as the flights."""
    gateways = {}
    for category, search in (config.trip.by_category().items() if config.trip else ()):
        spec = ACTORS[category]
        # A stay search may ask for more rows than the configured default.
        max_items = max(config.limits.max_items, getattr(search, "max_items", 0) * 2)
        gateways[category] = ApifyResearch(client, ledger, category=category, actor_id=spec.actor_id, build=spec.build,
            room_id=snapshot.room_id, input_revision=snapshot.input_revision, permission_revision=snapshot.permission_revision,
            approved_searches=[search], clock=clock, budget=budget,
            build_input=lambda search, purpose, spec=spec: spec.build_input(search, {"purpose": purpose}, config.limits.max_items),
            request_input_builder=lambda search, request, spec=spec: spec.build_input(search, request, config.limits.max_items),
            normalize_item=lambda item, search, run, spec=spec: spec.normalize(item, search, run, config.limits.freshness_seconds),
            max_items=max_items, max_charge_usd=config.limits.max_charge_usd,
            poll_deadline_seconds=config.limits.poll_deadline_seconds, normalizer_version=spec.normalizer_version,
            max_concurrent_runs=config.limits.max_concurrent_runs)
    return gateways


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
        normalize_item=lambda item, search, run: normalize_offers(item, search, run, freshness_seconds=config.limits.freshness_seconds),
        max_items=config.limits.max_items, max_charge_usd=config.limits.max_charge_usd,
        poll_deadline_seconds=config.limits.poll_deadline_seconds, normalizer_version=NORMALIZER_VERSION,
        max_concurrent_runs=config.limits.max_concurrent_runs)
    shared = trip_gateways(config, client, ledger, snapshot, clock, budget)
    if config.trip is not None:
        gateway = TripResearch(gateway, shared)
    return Providers(model, CheckedResearch(gateway), client, budget, ledger, settings.apify_actor_build,
                     trip_categories=tuple(shared))


def check_providers(config, settings, *, online=False, clock=None, claude_client=None, apify_client=None):
    clock = clock or SystemClock()
    config.validate_dates(clock.now())
    result = {"status": "OK", "configuration": "VALID", "missing": settings.missing(),
        "model": settings.claude_model, "actor_id": ACTOR_ID, "actor_build": settings.apify_actor_build,
        "message_requests": 0, "actor_runs": 0}
    trip = config.trip.by_category() if config.trip else {}
    if trip:
        result["trip_actors"] = {c: {"actor_id": ACTORS[c].actor_id, "actor_build": ACTORS[c].build} for c in trip}
    if result["missing"]:
        result["status"] = "CONFIG_REQUIRED"
        return result
    settings.require()
    if online:
        model = ClaudeModel(settings.claude_model, settings.anthropic_api_key.get_secret_value(),
                            client=claude_client, timeout=config.limits.http_timeout)
        client = apify_client or ApifyHTTP(settings.apify_token.get_secret_value(), timeout=config.limits.http_timeout)
        result["access"] = {"claude": model.check_access(), "apify": client.check_actor(ACTOR_ID, settings.apify_actor_build, BUILD_ID)}
        if trip:
            result["access"]["trip"] = {c: client.check_actor(ACTORS[c].actor_id, ACTORS[c].build, ACTORS[c].build_id) for c in trip}
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
            or config.limits.max_charge_usd > 1 or config.limits.max_items > 10 or config.limits.max_output_tokens > 4096):
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
        **providers.report(backend="NONE")}
    with cached.open("w") as handle:
        json.dump(result, handle, indent=2)
    return result


ROOM_STATE = "spacetime-room.json"


def _spacetime(settings, directory, clock, limits, room_id=None):
    """The live backend: a seeded room remembered in the state directory, or an
    existing app room. Owners are matched to the config by display name."""
    from .adapters.spacetime import SpacetimeBackend
    state_path = Path(directory) / ROOM_STATE
    tokens = {}
    if room_id is None:
        if state_path.exists():
            state = json.loads(state_path.read_text())
        else:
            raise ContractError("ROOM_NOT_SEEDED")
        room_id, tokens = state["room_id"], state["owner_tokens"]
    backend = SpacetimeBackend(settings.stdb_url, settings.stdb_database, settings.stdb_worker_token.get_secret_value(),
        clock=clock, limits=limits, room_id=room_id, owner_name=lambda member: member["display_name"], owner_tokens=tokens)
    return backend, room_id


def seed_live_room(config, settings, directory):
    """Create the config's participants as real people in SpacetimeDB (demo
    without the frontend). Their tokens stay private in the state directory."""
    from .adapters.spacetime import StdbHttp, seed_room
    settings.require_backend()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ROOM_STATE
    if path.exists():
        raise ContractError("ROOM_ALREADY_SEEDED")
    trip = config.trip
    components = ["FLIGHT", *(["STAY"] if trip and trip.stay else []), *(["TRANSFER"] if trip and trip.transfer else [])]
    seeded = seed_room(StdbHttp(settings.stdb_url, settings.stdb_database), list(config.briefs), goal=config.goal,
                       settings={"components": components} if len(components) > 1 else None)
    with path.open("x") as handle:
        json.dump(seeded, handle)
    path.chmod(0o600)
    return {"room_id": seeded["room_id"], "invite_code": seeded["invite_code"], "owners": sorted(seeded["owner_tokens"])}


def _room_report(backend, room_id):
    """What the room's people see: published proposals and approval counts."""
    tokens = backend.owner_tokens
    if not tokens:
        return {}
    token = next(iter(tokens.values()))
    proposals = [p for p in backend.http.view(token, "room_proposals") if p["room_id"] == room_id]
    counts = [c for c in backend.http.view(token, "room_consent_counts") if c["room_id"] == room_id]
    return {"proposals": [{k: p[k] for k in ("id", "stage", "version", "state", "terms_digest")} for p in proposals],
            "consent_count": sum(c["accepted"] for c in counts)}


def bind_room(directory, room_id):
    """Do not let --room silently repurpose a directory's provider budgets/receipts."""
    path = Path(directory) / "app-room.json"
    value = {"room_id": str(room_id)}
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ContractError("LIVE_STATE_ROOM_MISMATCH")
        return
    try:
        with path.open("x") as handle:
            json.dump(value, handle)
    except FileExistsError:
        if json.loads(path.read_text()) != value:
            raise ContractError("LIVE_STATE_ROOM_MISMATCH") from None


def validate_app_room(config, context):
    """Check operator search configuration against authoritative membership, before providers."""
    names = [m["display_name"] for m in context["members"] if m["active"]]
    if len(names) != len(set(names)) or "mediator" in names:
        raise ContractError("ROOM_DISPLAY_NAMES_NOT_UNIQUE")
    expected = {b.owner for b in config.briefs}
    if set(names) != expected or {s.owner for s in config.searches} != expected or len(config.searches) != len(names):
        raise ContractError("ROOM_CONFIG_MEMBER_MISMATCH")
    settings = context["room"].get("settings") or {}
    components = {"FLIGHT"}
    if config.trip and config.trip.stay:
        components.add("STAY")
    if config.trip and config.trip.transfer:
        components.add("TRANSFER")
    if set(settings.get("components", ["FLIGHT"])) != components:
        raise ContractError("ROOM_CONFIG_COMPONENT_MISMATCH")
    if any(not m["participation_preferences"]["confirmed"] for m in context["members"] if m["active"]):
        raise ContractError("ROOM_PARTICIPATION_NOT_CONFIRMED")
    flight_inputs = {c["member_id"] for c in context.get("constraints", []) if c["key"] == "flight_budget_total"}
    if any(m["member_id"] not in flight_inputs for m in context["members"] if m["active"]):
        raise ContractError("ROOM_CONSTRAINTS_NOT_CONFIRMED")


def run_live(config, settings, directory, *, room_id=None, clock=None, claude_client=None, apify_client=None):
    """Run the worker with live Claude and Apify against SpacetimeDB. Plans the
    newest pending job of the seeded room (see seed_live_room) or of `room_id`."""
    from .runner import Worker
    clock = clock or SystemClock()
    config.validate_dates(clock.now())
    settings.require()
    settings.require_backend()
    bind_session(directory, config, settings, "run", clock.now())
    directory = Path(directory)
    # Research steps per job stay at the default; paid Actor runs are capped by
    # config.limits.actor_runs in the provider budget (one per flight + one per shared search).
    limits = Limits(model_calls=config.limits.model_calls)
    backend, room_id = _spacetime(settings, directory, clock, limits, room_id)
    bind_room(directory, room_id)
    jobs = backend.pending_jobs()
    if not jobs:
        return {"mode": "LIVE_PROVIDERS", "backend": "SPACETIMEDB", "room_id": room_id,
                "result": {"outcome": "NO_ELIGIBLE_WORK"}, "report": _room_report(backend, room_id)}
    job = jobs[-1]
    # Research runs are bound to this job's room and revisions.
    snapshot = Snapshot(room_id=str(room_id), input_revision=job["input_revision"],
        membership_revision=job["membership_revision"], permission_revision=job["permission_revision"],
        members=tuple(b.owner for b in config.briefs))
    providers = make_providers(config.model_copy(update={"room_id": str(room_id)}), settings, directory, clock=clock,
        claude_client=claude_client, apify_client=apify_client, snapshot=snapshot)
    def before_run(claim):
        context = backend._context(claim)
        validate_app_room(config, context)
        providers.preflight()

    worker = Worker(backend, providers.model, providers.research, directory / "checkpoints.sqlite", clock=clock, limits=limits,
                    worker_id="live-worker-" + uuid.uuid4().hex[:8], before_run=before_run)
    result = worker.run_one(str(job["id"]))
    return {"mode": "LIVE_PROVIDERS", "room_id": room_id, "result": result, "report": _room_report(backend, room_id),
            **providers.report(backend="SPACETIMEDB")}


def live_questions(settings, directory, owner):
    """Questions the agents asked this seeded owner (what the app would show)."""
    backend, room_id = _spacetime(settings, directory, None, Limits())
    token = backend.owner_tokens.get(owner)
    if token is None:
        raise ContractError("NOT_AUTHORIZED")
    return [{k: q[k] for k in ("request_key", "kind", "version", "prompt", "state")}
            for q in backend.http.view(token, "my_questions") if q["room_id"] == room_id]


def live_answer(settings, directory, owner, question_ref, version, answer):
    """Answer as a seeded owner; the next run_live continues from it."""
    backend, _ = _spacetime(settings, directory, None, Limits())
    return {"answer_ref": backend.answer_question(owner, question_ref, version, answer, uuid.uuid4().hex)}
