from copy import deepcopy

import pytest

from tandem_worker.adapters.memo23 import build_input, local_epoch, money_cents, normalize_item, validate_input_schema
from tandem_worker.contracts import ContractError


def test_explicit_one_way_input_has_no_private_caps(live_case):
    data = build_input(live_case.config.searches[0], {"purpose": "INITIAL"})
    assert data == {"origin": "SFO", "destination": "LAX", "departDate": "2027-01-15", "adults": 1,
        "cabinClass": "economy", "currency": "USD", "maxItems": 10, "sortBy": "top"}


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
