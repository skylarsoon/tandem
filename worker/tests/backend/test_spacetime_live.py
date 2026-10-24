"""Live runs (Claude and Apify ports, faked at the client boundary) planning
against the real SpacetimeDB module."""
import json

import pytest

from tandem_worker.contracts import ContractError, Decision
from tandem_worker.live import live_answer, live_questions, run_live, seed_live_room

from conftest import DB, URL, WORKER_TOKEN


@pytest.fixture
def live(live_case):
    from tandem_worker.config import Settings
    # Same provider test keys as live_case, plus the disposable database.
    live_case.settings = Settings(anthropic_api_key="private-test-key", claude_model="claude-test-model",
        apify_token="private-apify-token", stdb_url=URL, stdb_database=DB, stdb_worker_token=WORKER_TOKEN)
    return live_case


def kwargs(case):
    return dict(clock=case.clock, claude_client=case.claude, apify_client=case.apify)


def test_live_run_needs_a_seeded_room(live):
    with pytest.raises(ContractError, match="ROOM_NOT_SEEDED"):
        run_live(live.config, live.settings, live.directory, **kwargs(live))


def test_live_run_blocks_approval_for_observed_incomplete_prices(live):
    seeded = seed_live_room(live.config, live.settings, live.directory)
    assert seeded["owners"] == ["p1"]
    result = run_live(live.config, live.settings, live.directory, **kwargs(live))
    assert result["backend"] == "SPACETIMEDB" and result["room_id"] == seeded["room_id"]
    assert result["result"]["outcome"] == "EXPLORATORY_ONLY"
    assert result["result"]["validation_checks"] == ["INCOMPLETE_COST_EVIDENCE"]
    assert result["report"] == {"proposals": [], "consent_count": 0}
    assert len(live.apify.starts) == 1
    # The private brief went to the decision call only, never to Apify or the room.
    assert "hard_cap" not in json.dumps(live.apify.starts)


def test_live_run_after_consultation_reuses_evidence_and_keeps_budget(live):
    from types import SimpleNamespace
    seed_live_room(live.config, live.settings, live.directory)
    live.claude.overrides = [SimpleNamespace(id="msg-consult", stop_reason="end_turn", usage=None,
                                             parsed_output=Decision(concession=True))]
    first = run_live(live.config, live.settings, live.directory, **kwargs(live))
    assert first["result"]["outcome"] == "WAITING_FOR_INPUT"
    question = live_questions(live.settings, live.directory, "p1")[0]
    assert question["kind"] == "CONCESSION_CONSULTATION" and question["state"] == "OPEN"
    live_answer(live.settings, live.directory, "p1", question["request_key"], question["version"], True)
    second = run_live(live.config, live.settings, live.directory, **kwargs(live))
    assert second["result"]["outcome"] == "EXPLORATORY_ONLY"
    assert second["usage"]["used"] == {"claude": 3, "apify": 1}, "the granted run reuses the stored flights"
    assert len(live.apify.starts) == 1
    assert second["report"]["consent_count"] == 0


def test_airline_booking_prices_reach_human_approval(live):
    from tandem_worker.ports import SystemClock
    # memo23 booking options: the airline sells it at $129.40 including taxes.
    live.apify.rows[0] = {**live.apify.rows[0], "booking-options-count": 1, "booking-option-1-is-airline": True,
                          "booking-option-1-price-usd": 129.40, "booking-option-1-first-bag-fee": 35}
    seeded = seed_live_room(live.config, live.settings, live.directory)
    # Real time: the database checks evidence freshness against its own clock.
    result = run_live(live.config, live.settings, live.directory, clock=SystemClock(),
                      claude_client=live.claude, apify_client=live.apify)
    assert result["result"]["outcome"] == "AWAITING_REVIEW", result["result"]
    proposal = result["report"]["proposals"][0]
    assert proposal["state"] == "AWAITING_REVIEW" and result["report"]["consent_count"] == 0
    assert live.apify.starts[0][2]["resolveBookingOptions"] is True

    from tandem_worker.adapters.spacetime import StdbHttp
    state = json.loads((live.directory / "spacetime-room.json").read_text())
    StdbHttp(URL, DB).call(state["owner_tokens"]["p1"], "respond_to_proposal", proposal_id=proposal["id"],
                           version=proposal["version"], terms_digest=proposal["terms_digest"], response="ACCEPT", note=None)
    approved = [p for p in StdbHttp(URL, DB).view(state["owner_tokens"]["p1"], "room_proposals")
                if p["room_id"] == seeded["room_id"]]
    assert approved[0]["state"] == "AGREED"
    assert "12940" in approved[0]["terms_json"], "the airline's complete price is what people approved"
