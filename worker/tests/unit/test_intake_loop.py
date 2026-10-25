"""A spent interview budget must not trap replies in repeated summaries."""
from types import SimpleNamespace

import pytest

from tandem_worker.contracts import IntakeContext, IntakeExtraction, IntakeItem, IntakeMessage, TripBrief
from tandem_worker.graphs.intake import run_intake
from tandem_worker.intake_policy import MAX_QUESTIONS


class Backend:
    def __init__(self, ctx):
        self.ctx, self.messages, self.updates, self.patches = ctx, [], [], []
        self.withdrawn = False

    def load_intake(self, claim):
        return self.ctx

    def operation(self, claim, key, context, kind, call):
        return call()

    def notify_owner(self, claim, owner, text, key):
        self.messages.append(text)

    def update_intake(self, claim, owner, coverage, state, asked, key):
        self.updates.append((coverage, state, asked))

    def withdraw_patches(self, *args):
        self.withdrawn = True

    def propose_patch(self, claim, owner, patch, source_message_id, key):
        self.patches.append(patch)


def context(reply):
    return IntakeContext(member_id=1, owner="ana", purpose="MESSAGE", intake_state="SUMMARY",
        brief=TripBrief(destination="Honolulu", destination_airport="HNL", start_date="2027-02-12", end_date="2027-02-16"),
        question_count=MAX_QUESTIONS, preferences_confirmed=True, confirmed_keys=("origin_airport", "dates_ok"),
        pending=({"key": "own_bedroom", "value": True, "hardness": "SOFT"},),
        messages=(IntakeMessage(id=1, sender="AGENT", body="Confirm, or tell me what to change. Still needed: a budget."),
                  IntakeMessage(id=2, sender="HUMAN", body=reply)))


@pytest.mark.parametrize("reply", ["confirm", "1000 budget"])
def test_incomplete_summary_gets_a_targeted_question_without_dismissing_suggestions(reply):
    backend = Backend(context(reply))
    model = SimpleNamespace(extract=lambda _: IntakeExtraction())
    assert run_intake(backend, SimpleNamespace(job_id=1), model) == "ASKED"
    assert len(backend.messages) == 1
    assert "Here's what I've got" not in backend.messages[0]
    assert backend.messages[0].endswith("?")
    if reply == "1000 budget":
        assert "$1,000" in backend.messages[0]
    assert not backend.withdrawn and not backend.patches
    assert backend.updates[0][1:] == ("SUMMARY", False)


def test_completed_budget_moves_past_clarification_to_review():
    backend = Backend(context("1000 budget for the whole trip, firm limit"))
    model = SimpleNamespace(extract=lambda _: IntakeExtraction(items=(IntakeItem(
        key="trip_budget_total", value=100000, hardness="HARD", source_message_id=2,
        source_quote="1000 budget for the whole trip, firm limit"),)))
    assert run_intake(backend, SimpleNamespace(job_id=1), model) == "SUMMARY"
    assert backend.patches[0]["value"] == 100000
    assert "Still needed" not in backend.messages[0]
