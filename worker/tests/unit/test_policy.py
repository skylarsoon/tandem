import pytest
from pydantic import ValidationError

from tandem_worker.contracts import Act, Brief, ContractError, Decision, Message, Payload, Preferences, VoiceDraft, VoiceSegment
from tandem_worker.policy import fallback_voice, needed_permissions, permitted_facts, review_controls, validate_voice


def brief(**settings):
    return Brief(owner="p1", hard_cap_cents=50000, preferences=Preferences(confirmed=True, **settings), reminders_opted_in=True)


def test_unconfirmed_settings_have_no_authority():
    assert needed_permissions(Brief(owner="p1", hard_cap_cents=50000), Decision()) == ["PARTICIPATION"]


@pytest.mark.parametrize("enabled,expected", [(True, []), (False, ["DISCLOSURE"])])
def test_speak_up_changes_objection_routing(enabled, expected):
    assert needed_permissions(brief(speak_up_for_me=enabled), Decision(act=Act.OBJECT)) == expected


@pytest.mark.parametrize("enabled,expected", [(True, ["CONSULTATION"]), (False, [])])
def test_ask_first_within_range(enabled, expected):
    assert needed_permissions(brief(ask_me_first=enabled), Decision(concession=True)) == expected


@pytest.mark.parametrize("enabled", [True, False])
def test_outside_range_always_requires_permission(enabled):
    assert "EXPLORE" in needed_permissions(brief(ask_me_first=enabled), Decision(exceeds_exploration=True))


def test_give_me_time_suppresses_opted_in_reminders():
    assert not review_controls(brief(give_me_time=True))["reminder_allowed"]
    assert review_controls(brief(give_me_time=False))["reminder_allowed"]
    assert review_controls(brief())["allow_decide_later"]


@pytest.mark.parametrize("text,ref", [
    ("I approve this trip for everyone.", "act:EVALUATE"),
    ("The price is $1 and the flight is booked.", "act:EVALUATE"),
    ("PRIVATE_CANARY_p1", "act:EVALUATE"),
    ("My evaluation is complete; human review is still required.", "invented"),
])
def test_claim_reference_does_not_license_arbitrary_prose(text, ref):
    facts = permitted_facts(Act.EVALUATE, Payload(draft_ref="d", evaluation="ELIGIBLE"))
    with pytest.raises(ContractError, match="UTTERANCE_PAYLOAD_MISMATCH"):
        validate_voice(VoiceDraft(segments=(VoiceSegment(fact_ref=ref, text=text),)), facts)


def test_template_only_uses_permitted_fact_text():
    facts = permitted_facts(Act.OBJECT, Payload(draft_ref="d", evaluation="INELIGIBLE", blocker="HARD_LIMIT"))
    text, refs = fallback_voice(facts)
    assert "500" not in text
    assert tuple(facts) == refs


def test_extra_fields_and_free_chat_are_rejected():
    with pytest.raises(ValidationError):
        Decision.model_validate({"act": "FREE_CHAT", "approve_for_human": True})


def test_unknown_message_schema_is_not_reinterpreted():
    with pytest.raises(ValidationError):
        Message.model_validate({"schema_version": 1})


def test_candidate_facts_cite_typed_evidence_fields():
    candidate = {"id": "apify-x", "total_cents": 62900, "arrival_epoch": 1_800_052_000, "arrival_timezone": "Pacific/Honolulu",
                 "cost_basis": "COMPLETE_FLIGHT_TOTAL", "missing_critical_fields": [], "baggage_included": False}
    facts = permitted_facts(Act.PROPOSE, Payload(draft_ref="d1", evaluation="REQUESTED"), candidate,
                            (1_800_050_000, 1_800_052_000, "Pacific/Honolulu"))
    assert facts == {
        "act:PROPOSE": "Could you evaluate this option?",
        "apify-x#price": "The fare is $629.00 including taxes.",
        "apify-x#arrival": "It lands at 12:26 local time.",
        "apify-x#bag": "No checked bag is included.",
        "draft:d1#arrival_window": "Everyone would land between 11:53 and 12:26 local time.",
    }
    observed = {**candidate, "cost_basis": "OBSERVED_FLIGHT_PRICE", "baggage_included": None}
    facts = permitted_facts(Act.PROPOSE, Payload(draft_ref="d1", evaluation="REQUESTED"), observed)
    assert facts["apify-x#price"] == "The listed fare is $629.00; some fees are not confirmed."
    assert facts["apify-x#bag"] == "Checked-bag coverage is not confirmed."


@pytest.mark.parametrize("act,blocker,sentence", [
    (Act.OBJECT, "HARD_LIMIT", "This option is outside what my traveller can commit to."),
    (Act.OBJECT, "BAGGAGE_UNKNOWN", "My traveller needs a checked bag, and this fare doesn't confirm one."),
    (Act.CLARIFY, "BAGGAGE_UNKNOWN", "Could you confirm whether this fare includes a checked bag?"),
])
def test_objections_name_the_kind_of_requirement_never_its_value(act, blocker, sentence):
    facts = permitted_facts(act, Payload(draft_ref="d", evaluation="INELIGIBLE", blocker=blocker))
    assert facts == {f"act:{act.value}": sentence}
