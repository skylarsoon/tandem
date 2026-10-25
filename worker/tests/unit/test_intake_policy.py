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


def test_live_run_regressions():
    # Maya: the stretch figure was outside the quoted span, so her whole budget was dropped.
    said = "budget-wise I'd like to keep the whole trip around $900, flight and stay together. I could stretch to maybe $1,000"
    kept, _, coverage, _ = verify(IntakeExtraction(items=(item("trip_budget_total", 90000, "keep the whole trip around $900",
        hardness="SOFT", authorized_range={"max": 100000}),)), ctx(said))
    assert coverage["money"] == "ANSWERED" and kept[0].authorized_range == {"max": 100000}
    # Sam: "would be nice, but I'm flexible" is a preference, not an own-room requirement.
    hedge = "A place with a few separate bedrooms would be nice, but I'm flexible on the layout."
    assert verify(IntakeExtraction(items=(item("own_bedroom", True, "a few separate bedrooms would be nice"),)), ctx(hedge))[0] == ()
    firm = "My main must-have is having my own bedroom, if that's possible."
    assert len(verify(IntakeExtraction(items=(item("own_bedroom", True, "having my own bedroom"),)), ctx(firm))[0]) == 1
    # Sam: "nonstop flights only" is 0 stops, shown as a reading.
    [stops] = verify(IntakeExtraction(items=(item("max_stops", 0, "nonstop flights only"),)), ctx("Must-have: nonstop flights only"))[0]
    assert stops.interpreted is True


def test_notes_about_the_traveller_are_never_sent_to_them():
    c = ctx("hi")
    notes = (Unclear(topic="dates", reason="Maya did not say whether Feb 12-16 works for her; ask if those dates are OK."),
             Unclear(topic="money", reason="No budget mentioned; ask if they have one?"))
    _, _, coverage, reasons = verify(IntakeExtraction(unclear=notes), c)
    assert reasons == {} and coverage["dates"] == "OPEN" and coverage["money"] == "OPEN"
    real = Unclear(topic="money", reason="Is that $900 for the flight only, or everything?")
    assert verify(IntakeExtraction(unclear=(real,)), c)[3] == {"money": real.reason}


def test_any_checkable_limit_becomes_a_rule_only_from_their_own_words():
    from tandem_worker.intake_policy import describe, patch_json
    c = ctx("Somewhere rated 4.5 or better, and land by 6pm please. Whole trip under $900.")
    rating = {"category": "STAY", "attribute": "rating", "operator": "gte", "value": 4.5}
    kept, _, coverage, _ = verify(IntakeExtraction(items=(
        item("rule", None, "rated 4.5 or better", rule=rating),
        item("rule", None, "rated 4.5 or better", rule={**rating, "value": 4.8}),                    # number they didn't say
        item("rule", None, "land by 6pm", rule={"category": "FLIGHT", "attribute": "arrival_local",
                                                "operator": "lte", "value": "18:00"}, interpreted=True),  # 6pm read as 18:00, shown as a reading
        item("rule", None, "rated 4.5", rule={"category": "STAY", "attribute": "pool", "operator": "eq", "value": True}),  # not checkable
        item("rule", None, "under $900", rule={"aggregate": "my_cost", "operator": "lte", "value": 90000}),
    )), c)
    assert [i.rule["attribute"] if "attribute" in i.rule else i.rule["aggregate"] for i in kept] == ["rating", "arrival_local", "my_cost"]
    assert coverage["money"] == "ANSWERED", "a money rule covers money"
    assert patch_json(kept[0]) == {"rule": {**rating, "kind": "hard"}, "source_quote": "rated 4.5 or better"}
    assert describe(kept[0]) == "Stay: rating at least 4.5"
    assert describe(kept[2]) == "Your share of the whole trip at most $900"
