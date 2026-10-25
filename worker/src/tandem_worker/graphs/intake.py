"""Conversational onboarding: one pass per INTAKE or INTERPRET job.

The organizer's description becomes a proposed trip brief. Each traveller's
agent opens with one trip-aware question, then reads every answer, keeps only
what was actually said (intake_policy.verify), and either asks about the
essentials still open or sends one summary to confirm. The origin is proposed
as soon as it's said, so searches can start early; everything else waits for
the summary. Model calls go through operation receipts, so a retried job never
asks twice."""
from __future__ import annotations

from datetime import date

from pydantic import ValidationError

from ..contracts import BriefDraft, ContractError, IntakeContext, IntakeExtraction, digest
from ..intake_policy import MAX_QUESTIONS, fallback_question, open_topics, opening_question, patch_json, summary_text, verify
from ..profile_suggest import asks_first, suggestions
from ..rules import attributes


def _model(backend, claim, kind, context, call, schema):
    """A receipt-backed model call; invalid output is retried once, then None."""
    for attempt in range(2):
        try:
            raw = backend.operation(claim, f"intake-{kind}:{digest(context)}:{attempt}", context, "model", lambda: call(context))
            return schema.model_validate(raw)
        except (ValidationError, ValueError):
            continue
    return None


def checkable() -> dict:
    """What onboarding can turn into rules: each category's attributes with their types."""
    catalog = attributes()
    return {"categories": {c: {a: s["type"] for a, s in attrs.items()} for c, attrs in catalog["categories"].items()},
            "aggregates": {a: s["type"] for a, s in catalog["aggregates"].items()}}


def run_intake(backend, claim, model) -> str:
    ctx: IntakeContext = backend.load_intake(claim)
    if ctx.purpose == "BRIEF" or (ctx.is_organizer and ctx.brief_state in ("DRAFT", "PROPOSED") and ctx.purpose == "MESSAGE"):
        return _brief(backend, claim, model, ctx)
    if ctx.brief is None or ctx.intake_state not in ("ASKING", "SUMMARY"):
        return "NOT_HANDLED"  # owner chat outside onboarding isn't handled yet
    suggested = suggestions(ctx.profile)
    if ctx.question_count == 0 and ctx.purpose == "INTAKE":
        # The first message is always the same kind of trip-aware opener. A usual airport
        # from their profile is offered for one-tap confirmation instead of asked.
        usual = suggested.get("origin_airport")
        coverage = dict(ctx.coverage)
        if usual and "origin_airport" not in ctx.confirmed_keys:
            backend.propose_patch(claim, ctx.owner, usual.patch(), None, f"intake:{claim.job_id}:usual-origin")
            coverage["origin"] = "ANSWERED"
        backend.notify_owner(claim, ctx.owner, opening_question(ctx, usual.value if usual else None), f"intake:{claim.job_id}:open")
        backend.update_intake(claim, ctx.owner, coverage, "ASKING", True, f"intake:{claim.job_id}:open")
        return "ASKED"
    reopened = ctx.purpose == "INTAKE"  # the brief changed: ask again about what it affects
    if not reopened and (not ctx.messages or ctx.messages[-1].sender != "HUMAN"):
        return "NOTHING_NEW"

    transcript = [m.model_dump() for m in ctx.messages]
    extraction = _model(backend, claim, "extract", {
        "owner": ctx.owner, "brief": ctx.brief.model_dump(), "messages": transcript,
        "already_confirmed": list(ctx.confirmed_keys), "checkable": checkable()}, model.extract, IntakeExtraction) or IntakeExtraction()
    items, settings, coverage, reasons = verify(extraction, ctx)
    # The usual airport offered from their profile covers "where from" until they say otherwise.
    if coverage["origin"] != "ANSWERED" and any(p.get("key") == "origin_airport" and p.get("source_profile") for p in ctx.pending):
        coverage["origin"] = "ANSWERED"
    # "Check with me before paying more" can come from their profile's consultation style.
    settings_from_profile = False
    profile_asks = asks_first(ctx.profile)
    if (not ctx.preferences_confirmed and settings is not None and settings.speak_up_for_me is not None
            and settings.ask_me_first is None and profile_asks is not None):
        settings, settings_from_profile = settings.model_copy(update={"ask_me_first": profile_asks}), True
        coverage["settings"] = "ANSWERED"
    if reopened:
        coverage = {t: ("OPEN" if ctx.coverage.get(t) == "OPEN" else state) for t, state in coverage.items()}
    by_key = {i.key: i for i in items}
    step = f"intake:{claim.job_id}"

    # Where they fly from is confirmed on its own, right away, so searches can start.
    origin = by_key.get("origin_airport")
    origin_line = ""
    pending_origin = next((p for p in ctx.pending if p.get("key") == "origin_airport"), None)
    if origin and "origin_airport" not in ctx.confirmed_keys:
        if not pending_origin or pending_origin.get("value") != origin.value:
            backend.propose_patch(claim, ctx.owner, patch_json(origin), origin.source_message_id, f"{step}:origin")
        origin_line = f"Flying from {origin.value}, right? Tap confirm and I'll start looking at flights."

    topics = open_topics(coverage)
    if topics and ctx.question_count < MAX_QUESTIONS:
        # Ask exactly about the open essentials. The wording is ours; anything specific
        # to what they said comes from extraction's `unclear` reasons. (A model-written
        # question drifted to off-topic small talk in live runs and doubled latency.)
        question = fallback_question(topics, ctx, reasons)
        text = f"{origin_line} {question.text}".strip()
        backend.notify_owner(claim, ctx.owner, text, f"{step}:ask")
        backend.update_intake(claim, ctx.owner, coverage, "ASKING", True, f"{step}:ask")
        return "ASKED"

    # Everything essential is covered (or the question budget is spent): one summary.
    backend.withdraw_patches(claim, ctx.owner, f"{step}:withdraw")
    for item in items:
        if item.key == "origin_airport" and "origin_airport" in ctx.confirmed_keys:
            continue
        backend.propose_patch(claim, ctx.owner, patch_json(item), item.source_message_id, f"{step}:{item.key}")
    # Profile defaults they didn't mention here, offered for this trip (confirmed with the rest).
    offered = [s for k, s in suggested.items() if k not in by_key and k not in ctx.confirmed_keys]
    for suggestion in offered:
        backend.propose_patch(claim, ctx.owner, suggestion.patch(), None, f"{step}:profile:{suggestion.key}")
    if settings is not None and not ctx.preferences_confirmed:
        # Give me time stays on (no nudging reminders) unless they change it in the summary.
        backend.propose_preferences(claim, ctx.owner, bool(settings.speak_up_for_me), bool(settings.ask_me_first), True,
                                    f"{step}:settings")
    missing_money = coverage["money"] != "ANSWERED"
    backend.notify_owner(claim, ctx.owner, summary_text(items, settings, ctx, missing_money, offered, settings_from_profile),
                         f"{step}:summary")
    backend.update_intake(claim, ctx.owner, coverage, "SUMMARY", False, f"{step}:summary")
    return "SUMMARY"


def _brief(backend, claim, model, ctx: IntakeContext) -> str:
    """Turn the organizer's description (and any follow-ups) into a brief, or ask
    about what's missing. Nothing unstated is filled in."""
    draft = _model(backend, claim, "brief", {"description": ctx.description, "today": date.today().isoformat(),
                                            "follow_ups": [m.body for m in ctx.messages if m.sender == "HUMAN"]},
                   model.brief, BriefDraft) or BriefDraft(unclear=("Where are you going, and on which dates?",))
    step = f"brief:{claim.job_id}"
    if draft.brief is None or draft.unclear:
        ask = " ".join(draft.unclear) or "Where are you going, and on which dates?"
        backend.notify_owner(claim, ctx.owner, ask[:400], f"{step}:ask")
        return "ASKED"
    try:
        backend.propose_trip_brief(claim, draft.brief, list(draft.unclear), f"{step}:propose")
    except ContractError as error:
        if error.code != "INVALID_STATE":
            raise
        return "NOT_HANDLED"
    b = draft.brief
    plan = {"FLIGHT": "flights", "STAY": "a place to stay", "TRANSFER": "an airport transfer"}
    backend.notify_owner(claim, ctx.owner,
        f"Here's the trip as I understand it: {b.destination} ({b.destination_airport}), {b.start_date} to {b.end_date}, "
        f"planning {', '.join(plan[c] for c in b.components)}." + (f" Vibe: {b.vibe}." if b.vibe else "") +
        " Confirm it and everyone's agents will start getting to know them.", f"{step}:summary")
    return "BRIEF_PROPOSED"
