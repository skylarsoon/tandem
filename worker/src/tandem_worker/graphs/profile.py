"""The agent's "Does this sound like you?" summary: one pass per PROFILE job.

It reads only this person's answers (and their correction, if they asked for
another try), writes from those facts alone, and proposes the summary for them
to confirm. A correction is context for the wording, never a requirement."""
from __future__ import annotations

from pydantic import ValidationError

from ..contracts import ProfileSummary, digest
from ..profile_policy import facts, fallback_summary, summary_ok


def run_profile(backend, claim, model) -> str:
    ctx = backend.load_profile(claim)
    stated = facts(ctx["answers"])
    context = {"facts": stated, "correction": (ctx.get("calibration") or {}).get("correction")}
    summary = None
    try:
        raw = backend.operation(claim, f"profile-summary:{digest(context)}", context, "model", lambda: model.profile_summary(context))
        summary = ProfileSummary.model_validate(raw).summary
    except (ValidationError, ValueError):
        summary = None
    if summary is None or not summary_ok(summary):
        summary = fallback_summary(stated)
    backend.propose_profile_summary(claim, summary, f"profile:{claim.job_id}")
    return "PROPOSED"
