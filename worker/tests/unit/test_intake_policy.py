"""Onboarding keeps only what was said; ambiguity is a question, never a default."""
import pytest

from tandem_worker.contracts import IntakeContext, IntakeExtraction, IntakeItem, IntakeMessage, IntakeQuestion, TripBrief, Unclear
from tandem_worker.intake_policy import amounts, fallback_question, open_topics, question_ok, verify

BRIEF = TripBrief(destination="Honolulu", destination_airport="HNL", start_date="2027-02-12", end_date="2027-02-16")


def ctx(*said, **extra):
    return IntakeContext(member_id=1, owner="ana", purpose="MESSAGE", brief=BRIEF,
                         messages=tuple(IntakeMessage(id=i + 1, sender="HUMAN", body=b) for i, b in enumerate(said)), **extra)


def item(key, value, quote, msg=1, **kw):
    return IntakeItem(key=key, value=value, source_quote=quote, source_message_id=msg, **kw)


@pytest.mark.parametrize("text,expected", [("$1,200 tops", {1200}), ("around 900", {900}), ("1.5k all in", {1500}),
                                           ("$450 and $1,100", {450, 1100})])
def test_amounts(text, expected):
    assert amounts(text) == expected


def test_items_need_the_owners_exact_words_and_numbers():
    c = ctx("Keep it under $700 all in")
    kept, *_ = verify(IntakeExtraction(items=(
        item("trip_budget_total", 70000, "under $700 all in"),
        item("flight_budget_total", 80000, "under $700 all in"),      # number they didn't say
        item("origin_airport", "JFK", "Flying from JFK"),            # quote not in the message
        item("baggage_required", True, "under $700", msg=9),          # message that doesn't exist
    )), c)
    assert [i.key for i in kept] == ["trip_budget_total"]


@pytest.mark.parametrize("said,key,hardness,reason", [
    ("Maybe $800?", "trip_budget_total", "HARD", "flight only, or the whole trip"),
    ("$800 for everything", "trip_budget_total", "HARD", "firm limit"),
    ("$800 for the flight", "flight_budget_total", "SOFT", "firm limit"),
])
def test_a_budget_without_scope_or_firmness_becomes_a_question(said, key, hardness, reason):
    kept, _, coverage, reasons = verify(IntakeExtraction(items=(item(key, 80000, said, hardness=hardness),)), ctx(said))
    assert kept == () and coverage["money"] == "UNCLEAR" and reason in reasons["money"]


def test_readings_must_be_marked_as_readings():
    said = "please no red-eye"
    window = {"earliest": "07:00", "latest": "22:00"}
    assert verify(IntakeExtraction(items=(item("arrival_window", window, said),)), ctx(said))[0] == ()
    assert len(verify(IntakeExtraction(items=(item("arrival_window", window, said, interpreted=True),)), ctx(said))[0]) == 1


def test_coverage_counts_earlier_confirmed_answers_and_unclear_topics():
    _, _, coverage, _ = verify(IntakeExtraction(unclear=(Unclear(topic="money", reason="How much?"),)),
                               ctx("hi", confirmed_keys=("origin_airport",), preferences_confirmed=True))
    assert coverage == {"origin": "ANSWERED", "dates": "OPEN", "money": "UNCLEAR", "settings": "ANSWERED"}
    assert open_topics(coverage) == ["dates", "money"]


def test_questions_never_invent_amounts_and_fallbacks_fit_one_message():
    c = ctx("Around $900 for everything")
    assert question_ok(IntakeQuestion(text="Is $900 a firm limit?", topics=("money",)), c)
    assert not question_ok(IntakeQuestion(text="Would $1,000 be OK?", topics=("money",)), c)
    assert not question_ok(IntakeQuestion(text="Tell me your budget.", topics=("money",)), c)
    q = fallback_question(["money", "settings"], c, {})
    assert len(q.text) <= 300 and q.text.endswith("?") and q.topics == ("money", "settings")
