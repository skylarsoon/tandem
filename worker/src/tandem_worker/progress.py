"""Room-safe execution telemetry. No prompts, decisions, owner questions or costs."""
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
