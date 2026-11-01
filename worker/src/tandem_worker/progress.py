"""Room-safe execution telemetry. No prompts, decisions, owner questions or costs."""
from contextlib import contextmanager
from contextvars import ContextVar

from .contracts import ContractError, digest


def activity(backend, claim, owner, value):
    try:
        backend.set_agent_activity(claim, owner, value)
    except ContractError as exc:
        if exc.code in {"LEASE_LOST", "STALE_SNAPSHOT"}:
            raise
    except Exception:
        pass  # Observability outages must not change a planning decision.


def report(backend, claim, stage, phase, *, round=0, cycle=0, step_ref="", draft_ref=""):
    try:
        snapshot = backend.load_execution_context(claim)
        payload = dict(schema_version=1, stage=stage, phase=phase, job_ref=claim.job_id,
                       generation=claim.generation, attempt=claim.attempt_token,
                       input_revision=snapshot.input_revision, membership_revision=snapshot.membership_revision,
                       permission_revision=snapshot.permission_revision, round=round, cycle=cycle,
                       step_ref=step_ref or f"{stage}:{round}:{cycle}")
        if draft_ref:
            payload["draft_ref"] = draft_ref
        backend.room_event(claim, "WORKFLOW_PROGRESS", payload, "progress:" + digest(payload))
    except ContractError as exc:
        if exc.code in {"LEASE_LOST", "STALE_SNAPSHOT"}:
            raise
    except Exception:
        pass

# Request-scoped observers also reach gateways constructed dynamically by the
# room research router. ContextVar prevents one concurrent room seeing another.

_research_observer = ContextVar('research_observer', default=None)
RESEARCH_PHASES = {'QUEUED', 'STARTING', 'RUNNING', 'COLLECTING', 'COMPLETED', 'CACHED', 'FAILED'}
RESEARCH_ERRORS = {'APIFY_AUTH_FAILED', 'APIFY_RESOURCE_UNAVAILABLE', 'APIFY_RATE_LIMITED',
    'APIFY_REQUEST_REFUSED', 'APIFY_REQUEST_FAILED', 'APIFY_CONNECTION_FAILED', 'APIFY_INVALID_RESPONSE',
    'APIFY_POLL_DEADLINE_EXCEEDED', 'APIFY_POLL_BUDGET_EXHAUSTED', 'APIFY_RUN_FAILED',
    'APIFY_START_AMBIGUOUS', 'ACTOR_RUN_BUDGET_EXHAUSTED', 'APIFY_SCHEMA_CHANGED', 'APIFY_SCHEMA_INCOMPATIBLE'}


def search_context(job):
    """A projection of the submitted actor input, never the entire request.

    No owner link, private cap, stop/bag preference, prompt, credential or reason.
    Keep provider-neutral keys so the UI doesn't depend on actor input schemas.
    """
    source = getattr(job, 'actor_input', {}) or {}
    category = job.gateway.category
    fields = {
        'FLIGHT': {'origin': 'origin', 'destination': 'destination', 'departDate': 'departure_date', 'adults': 'travellers', 'cabinClass': 'cabin', 'maxItems': 'result_limit'},
        'STAY': {'locationQueries': 'locations', 'checkIn': 'check_in', 'checkOut': 'check_out', 'adults': 'travellers', 'maxResults': 'result_limit'},
        'TRANSFER': {'pickupLocation': 'origin', 'dropoffLocation': 'destination', 'pickupDate': 'departure_date', 'pickupTime': 'departure_time', 'numberOfPassengers': 'travellers'},
        'ACTIVITY': {'destinations': 'locations', 'startDate': 'start_date', 'endDate': 'end_date', 'maxItemsPerLocation': 'result_limit'},
        'DINNER': {'searchStringsArray': 'queries', 'locationQuery': 'location', 'maxCrawledPlacesPerSearch': 'result_limit'},
    }.get(category, {})
    result = {}
    def clean(value):
        return isinstance(value, str) and 0 < len(value) <= 160 and not any(ord(c) < 32 for c in value)
    for key, target in fields.items():
        value = source.get(key)
        if clean(value):
            result[target] = value
        elif isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 1000:
            result[target] = value
        elif isinstance(value, list) and value and len(value) <= 5 and all(clean(x) for x in value):
            result[target] = list(value)
    leg = getattr(getattr(job, 'search', None), 'leg', None)
    if category == 'FLIGHT' and leg in {'outbound', 'return'}:
        result['leg'] = leg
    purpose = getattr(job, 'purpose', None)
    if purpose in {'INITIAL', 'TARGETED', 'REFRESH'}:
        result['purpose'] = purpose
    return result


def research_update(job, phase, *, error=None):
    callback = _research_observer.get()
    if callback is None or phase not in RESEARCH_PHASES:
        return
    run = job.run or {}
    # Never serialize full search inputs, traveller identity, raw provider errors,
    # datasets, credentials, private requirements, or model reasoning.
    payload = dict(provider='APIFY', search_ref=job.key, category=job.gateway.category,
                   actor_id=job.gateway.actor_id, phase=phase, polls=run.get('polls', 0))
    context = search_context(job)
    if context:
        payload['search_context'] = context
    if run.get('run_id'):
        payload['run_ref'] = run['run_id']
    if phase in {'COMPLETED', 'CACHED'}:
        payload['result_count'] = len(job.results)
        payload['excluded_count'] = run.get('quarantined', 0)
        payload['duplicate_count'] = run.get('duplicates', 0)
        payload['collection_truncated'] = bool(run.get('collection_truncated', False))
    if error is not None:
        payload['error_code'] = error.code if isinstance(error, ContractError) and error.code in RESEARCH_ERRORS else 'RESEARCH_UNAVAILABLE'
    try:
        callback(payload)
    except ContractError as exc:
        if exc.code in {'LEASE_LOST', 'STALE_SNAPSHOT'}:
            raise
    except Exception:
        pass


@contextmanager
def research_reporting(backend, claim, *, round=0):
    snapshot = backend.load_execution_context(claim)
    sequence = 0
    def publish(data):
        nonlocal sequence
        sequence += 1
        payload = dict(schema_version=1, job_ref=claim.job_id, generation=claim.generation,
                       attempt=claim.attempt_token, input_revision=snapshot.input_revision,
                       membership_revision=snapshot.membership_revision,
                       permission_revision=snapshot.permission_revision, round=round, **data)
        try:
            backend.room_event(claim, 'RESEARCH_ACTIVITY', payload,
                               'research-activity:' + digest([payload, sequence]))
        except ContractError as exc:
            if exc.code != 'PRIVATE_CONTENT_DETECTED' or 'search_context' not in payload:
                raise
            # A place/query can coincide with a protected private quote. Keep
            # progress available without exposing or retrying that text.
            safe = {k: v for k, v in payload.items() if k != 'search_context'}
            backend.room_event(claim, 'RESEARCH_ACTIVITY', safe,
                               'research-activity:' + digest([safe, sequence]))
    token = _research_observer.set(publish)
    try:
        yield
    finally:
        _research_observer.reset(token)
