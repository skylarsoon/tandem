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


def research_update(job, phase, *, error=None):
    callback = _research_observer.get()
    if callback is None or phase not in RESEARCH_PHASES:
        return
    run = job.run or {}
    # Never serialize search inputs, traveller identity, raw provider errors,
    # datasets, credentials, private requirements, or model reasoning.
    payload = dict(provider='APIFY', search_ref=job.key, category=job.gateway.category,
                   actor_id=job.gateway.actor_id, phase=phase, polls=run.get('polls', 0))
    if run.get('run_id'):
        payload['run_ref'] = run['run_id']
    if phase in {'COMPLETED', 'CACHED'}:
        payload['result_count'] = len(job.results)
        payload['excluded_count'] = run.get('quarantined', 0)
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
        backend.room_event(claim, 'RESEARCH_ACTIVITY', payload,
                           'research-activity:' + digest([payload, sequence]))
    token = _research_observer.set(publish)
    try:
        yield
    finally:
        _research_observer.reset(token)
