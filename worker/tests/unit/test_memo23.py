from copy import deepcopy

import pytest

from tandem_worker.adapters.memo23 import build_input, local_epoch, money_cents, normalize_item, normalize_offers, validate_input_schema
from tandem_worker.contracts import ContractError


def test_explicit_one_way_input_has_no_private_caps(live_case):
    data = build_input(live_case.config.searches[0], {"purpose": "INITIAL"})
    assert data == {"origin": "SFO", "destination": "LAX", "departDate": "2027-01-15", "adults": 1,
        "cabinClass": "economy", "currency": "USD", "maxItems": 10, "sortBy": "top",
        "resolveBookingOptions": True, "maxBookingItineraries": 10}


def test_targeted_search_changes_only_approved_criteria(live_case):
    search = live_case.config.searches[0]
    request = {"purpose": "TARGETED", "blocker_code": "BAGGAGE_UNKNOWN", "private_reason": "CANARY", "hard_cap": 50000}
    data = build_input(search, request)
    assert data["checkedBags"] == 1 and data["sortBy"] == "price"
    assert data["origin"] == search.origin
    assert "private_reason" not in data and "maxPrice" not in data
    with pytest.raises(ContractError, match="TARGETED_SEARCH_NOT_AUTHORIZED"):
        build_input(search, {"purpose": "TARGETED", "blocker_code": "ARRIVAL_OVERLAP"})


@pytest.mark.parametrize("value,expected", [(214, 21400), ("120.25", 12025), (0.01, 1)])
def test_price_conversion(value, expected):
    assert money_cents(value) == expected


@pytest.mark.parametrize("value", [None, True, "unknown", "1.001", float("nan"), -1])
def test_unknown_or_invalid_prices_are_not_zero(value):
    with pytest.raises(ValueError):
        money_cents(value)


def run_record(case, run_id="run-test"):
    return {"run_id": run_id, "build": "0.0.141", "dataset_id": "dataset-test", "observed_at": case.clock.now()}


def test_normalization_preserves_observation_and_unknown_costs(live_case):
    candidate = normalize_item(live_case.apify.rows[0], live_case.config.searches[0], run_record(live_case))
    assert candidate.total_cents == 12025
    assert candidate.checked_bags_included == 0 and candidate.baggage_included is False
    assert candidate.cost_basis == "OBSERVED_FLIGHT_PRICE"
    assert candidate.missing_critical_fields == ("mandatory_fees",)
    assert candidate.provider_expires_at is None
    assert candidate.valid_until - candidate.observed_at == 900
    assert candidate.actor_id == "ulPbFXCmcjxx3iPnp"
    assert candidate.dataset_ref == "dataset-test"
    other = normalize_item(live_case.apify.rows[0], live_case.config.searches[0], run_record(live_case, "new-observation"))
    assert candidate.id != other.id and candidate.flight_identity == other.flight_identity


def test_missing_baggage_is_unknown(live_case):
    row = deepcopy(live_case.apify.rows[0])
    row.pop("checked-bags-included")
    assert normalize_item(row, live_case.config.searches[0], run_record(live_case)).baggage_included is None


@pytest.mark.parametrize("bags,expected", [(None, "BAGGAGE_UNKNOWN"), (0, "BAGGAGE_REQUIRED")])
def test_validator_distinguishes_unknown_baggage_from_known_exclusion(live_case, bags, expected):
    from tandem_worker.contracts import Snapshot
    from tandem_worker.solver import FlightValidator
    row = {**live_case.apify.rows[0], "checked-bags-included": bags}
    candidate = normalize_item(row, live_case.config.searches[0], run_record(live_case))
    brief = live_case.config.briefs[0].model_copy(update={"baggage_required": True})
    report = FlightValidator().validate(Snapshot(room_id="r", members=("p1",)), [brief], [candidate], (candidate.id,), live_case.clock.now())
    assert expected in report.checks


@pytest.mark.parametrize("field,value", [("record-type", "price-graph"), ("currency", "EUR"), ("from-code", "JFK"),
    ("depart-date", "2027-02-01"), ("google-flights-url", "https://untrusted.invalid/"), ("return-date", "2027-01-20"),
    ("passengers", "2 adults")])
def test_mismatched_or_non_flight_rows_are_quarantined(live_case, field, value):
    row = {**live_case.apify.rows[0], field: value}
    with pytest.raises(ValueError):
        normalize_item(row, live_case.config.searches[0], run_record(live_case))


def test_timezones_and_overnight_boundary():
    departed = local_epoch("2027-01-15", "23:00", "America/Los_Angeles")
    arrived = local_epoch("2027-01-16", "07:30", "America/New_York")
    assert arrived - departed == 5.5 * 3600


@pytest.mark.parametrize("day,clock", [("2026-11-01", "01:30"), ("2027-03-14", "02:30")])
def test_dst_ambiguity_is_not_silently_resolved(day, clock):
    with pytest.raises(ValueError, match="AMBIGUOUS_OR_NONEXISTENT_TIME"):
        local_epoch(day, clock, "America/Los_Angeles")


def test_pinned_schema_validation_rejects_changed_input_contract():
    with pytest.raises(ContractError, match="APIFY_SCHEMA_INCOMPATIBLE"):
        validate_input_schema({"components": {"schemas": {"inputSchema": {"properties": {}}}}})


def with_booking_options(row, *options):
    """Adds memo23 booking-option fields (numbered from 0): (is_airline, price, first_bag_fee)."""
    row = {**row, "booking-options-count": len(options)}
    for n, (is_airline, price, bag) in enumerate(options):
        row.update({f"booking-option-{n}-is-airline": is_airline, f"booking-option-{n}-price-usd": price,
                    f"booking-option-{n}-provider": "Alaska" if is_airline else "Agency"})
        if bag is not None:
            row[f"booking-option-{n}-first-bag-fee"] = bag
    return row


def test_airline_booking_price_is_a_complete_total(live_case):
    row = with_booking_options(live_case.apify.rows[0], (False, 110.00, None), (True, 129.40, 35), (True, 131.00, 35))
    candidate = normalize_item(row, live_case.config.searches[0], run_record(live_case))
    # Cheapest airline-direct price, not the cheaper agency fare or the listing price.
    assert candidate.total_cents == 12940
    assert candidate.cost_basis == "COMPLETE_FLIGHT_TOTAL" and candidate.missing_critical_fields == ()


def test_agency_only_offers_stay_observed(live_case):
    row = with_booking_options(live_case.apify.rows[0], (False, 110.00, 30))
    candidate = normalize_item(row, live_case.config.searches[0], run_record(live_case))
    assert candidate.cost_basis == "OBSERVED_FLIGHT_PRICE" and candidate.missing_critical_fields == ("mandatory_fees",)
    assert normalize_offers(row, live_case.config.searches[0], run_record(live_case)) == [candidate]


@pytest.mark.parametrize("fee,expected", [(35, 16440), ("$35", 16440), ("$35.00", 16440), (0, 12940)])
def test_with_bag_variant_adds_the_airline_bag_fee(live_case, fee, expected):
    row = with_booking_options(live_case.apify.rows[0], (True, 129.40, fee))
    base, with_bag = normalize_offers(row, live_case.config.searches[0], run_record(live_case))
    assert base.baggage_included is False and base.total_cents == 12940
    assert with_bag.id == base.id + "-bag1" and with_bag.total_cents == expected
    assert with_bag.baggage_included is True and with_bag.checked_bags_included == 1
    assert with_bag.cost_basis == "COMPLETE_FLIGHT_TOTAL" and with_bag.flight_identity == base.flight_identity


@pytest.mark.parametrize("bag_fee,bags", [(None, 0), ("varies", 0), (35, 1)])
def test_no_bag_variant_when_fee_unknown_or_bag_already_included(live_case, bag_fee, bags):
    row = with_booking_options({**live_case.apify.rows[0], "checked-bags-included": bags}, (True, 129.40, bag_fee))
    assert len(normalize_offers(row, live_case.config.searches[0], run_record(live_case))) == 1


def test_solver_skips_known_bagless_fares_for_travellers_who_need_a_bag(live_case):
    from tandem_worker.contracts import Snapshot
    from tandem_worker.solver import FlightSolver
    row = with_booking_options(live_case.apify.rows[0], (True, 129.40, 35))
    base, with_bag = normalize_offers(row, live_case.config.searches[0], run_record(live_case))
    snapshot = Snapshot(room_id="r", members=("p1",))
    needs_bag = live_case.config.briefs[0].model_copy(update={"baggage_required": True})
    assert FlightSolver().solve(snapshot, [needs_bag], [base, with_bag]).selected == (with_bag.id,)
    assert FlightSolver().solve(snapshot, [live_case.config.briefs[0]], [base, with_bag]).selected == (base.id,)
    unknown = base.model_copy(update={"baggage_included": None})
    assert FlightSolver().solve(snapshot, [needs_bag], [unknown]).selected == (unknown.id,), "unknown stays selectable"


def test_real_booking_option_rows_yield_every_airline_direct_fare(live_case):
    """Rows captured from a real memo23 0.0.141 run (SFO→LAX). Options are numbered
    from 0, and option 0 is usually the airline's basic fare."""
    import json
    from pathlib import Path
    rows = json.loads((Path(__file__).parents[1] / "fixtures" / "memo23_booking_options_sfo_lax.json").read_text())
    search, run = live_case.config.searches[0], run_record(live_case)

    def fares(row):
        return [(c.total_cents, c.baggage_included) for c in normalize_offers(row, search, run)]

    frontier, alaska, united = rows
    assert fares(frontier) == [(2900, False), (6100, False)]                     # Basic $29; no bag fee listed
    assert fares(alaska)[:3] == [(5900, False), (10400, True), (10400, False)]    # Saver $59 (+$45 bag), Main $104
    assert fares(united)[:3] == [(7300, False), (12300, True), (11800, False)]    # Basic Economy $73 (+$50 bag)
    assert all(c.cost_basis == "COMPLETE_FLIGHT_TOTAL" for row in rows for c in normalize_offers(row, search, run))
    assert normalize_item(united, search, run).total_cents == 7300
