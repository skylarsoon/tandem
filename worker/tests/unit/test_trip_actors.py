import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from tandem_worker.adapters.apify import ApifyHTTP, schema_validator
from tandem_worker.adapters.trip_actors import (ACTORS, ActivitySearch, DinnerSearch, StaySearch, TransferSearch, TripSearches,
    airbnb, booking_attractions, booking_taxis, google_places, price_band_midpoint_cents, usd_text_cents)
from tandem_worker.config import LiveConfig
from tandem_worker.contracts import ContractError

FIXTURES = Path(__file__).parents[1] / "fixtures"
RUN = {"run_id": "run-trip", "build": "pinned", "dataset_id": "dataset-trip", "observed_at": 1_790_000_000.0}
STAY = StaySearch(destination="Honolulu", check_in=date(2027, 1, 15), check_out=date(2027, 1, 19), adults=4, max_items=5)
TRANSFER = TransferSearch(pickup_location="Honolulu International Airport", dropoff_location="Waikiki Beach, Honolulu",
                          pickup_date=date(2027, 1, 15), pickup_time="14:00", passengers=4)
ACTIVITY = ActivitySearch(destination="Honolulu, us", date=date(2027, 1, 16))
DINNER = DinnerSearch(query="restaurant for large groups", location="Honolulu, Hawaii, USA")


def rows(name):
    return json.loads((FIXTURES / f"trip_{name}.json").read_text())


def one(actor, row, search):
    [candidate] = actor.normalize(row, search, RUN, 900)
    return candidate


# --- STAY ------------------------------------------------------------------

def test_stay_input_carries_only_approved_facts():
    request = {"purpose": "INITIAL", "private_reason": "CANARY", "hard_cap_cents": 1}
    assert airbnb.build_input(STAY, request, 10) == {"locationQueries": ["Honolulu"], "checkIn": "2027-01-15",
        "checkOut": "2027-01-19", "adults": 4, "currency": "USD", "locale": "en-US", "maxResults": 5}


def test_targeted_stay_search_widens_or_reruns_and_refuses_other_blockers():
    wider = airbnb.build_input(STAY, {"purpose": "TARGETED", "blocker_code": "STAY_UNAVAILABLE"})
    assert wider["maxResults"] == 10
    # Airbnb has no sort option: a cheaper-stay search reruns the approved one.
    cheaper = airbnb.build_input(STAY, {"purpose": "TARGETED", "blocker_code": "HARD_LIMIT"})
    assert cheaper == airbnb.build_input(STAY, {"purpose": "INITIAL"})
    with pytest.raises(ContractError, match="TARGETED_SEARCH_NOT_AUTHORIZED"):
        airbnb.build_input(STAY, {"purpose": "TARGETED", "blocker_code": "BAGGAGE_UNKNOWN"})


def test_stay_total_is_complete_only_when_base_plus_taxes_adds_up():
    candidate = one(airbnb, rows("airbnb")[0], STAY)
    assert candidate.category == "STAY" and candidate.owner is None
    assert candidate.total_cents == 147794  # "$1,477.94" = $1,245.00 + $232.94
    assert candidate.cost_basis == "COMPLETE_STAY_TOTAL" and candidate.complete
    assert (candidate.capacity, candidate.bedrooms, candidate.rating) == (5, 2, 4.89)
    assert (candidate.check_in, candidate.check_out) == ("2027-01-15", "2027-01-19")
    assert candidate.title == "2BR Downtown Free Parking WiFi"
    assert candidate.source_url.startswith("https://www.airbnb.com/rooms/1239612966764072800?")
    assert candidate.mode == "APIFY_OBSERVED" and candidate.run_ref == "run-trip"
    assert candidate.actor_id == airbnb.ACTOR_ID and candidate.dataset_ref == "dataset-trip"
    assert candidate.id.startswith("apify-stay-")


@pytest.mark.parametrize("change", [
    lambda p: p["breakDown"]["taxes"].update(price="$200.00"),  # does not add up
    lambda p: p["breakDown"].update(taxes=None),  # taxes not shown
    lambda p: p.update(qualifier="night"),  # not a stay total
])
def test_stay_total_that_does_not_prove_taxes_is_only_observed(change):
    row = deepcopy(rows("airbnb")[0])
    change(row["price"])
    candidate = one(airbnb, row, STAY)
    assert candidate.total_cents == 147794
    assert candidate.cost_basis == "OBSERVED_PRICE" and candidate.missing_critical_fields == ("taxes",)
    assert not candidate.complete


@pytest.mark.parametrize("items,expected", [
    (["5 guests", "2 bedrooms", "4 beds", "1 bath"], 2),
    (["2 guests", "1 bedroom", "1 bed"], 1),
    (["2 guests", "Studio", "1 bed"], 0),
    (["4 guests", "3 beds"], None),
    (["1 bedroom", "2 bedrooms"], None),
])
def test_bedrooms_parse_from_listing_facts(items, expected):
    row = deepcopy(rows("airbnb")[0])
    row["subDescription"]["items"] = items
    assert one(airbnb, row, STAY).bedrooms == expected


def test_studio_in_the_title_does_not_override_listed_bedrooms():
    row = rows("airbnb")[1]
    assert "Studio" in row["title"]
    assert one(airbnb, row, STAY).bedrooms == 1


@pytest.mark.parametrize("change", [
    lambda r: r["price"]["breakDown"].update(total=None),  # missing total is never zero
    lambda r: r["price"]["breakDown"]["total"].update(price="CA$1,477.94"),
    lambda r: r.update(url=r["url"].replace("currency=USD", "currency=EUR")),
    lambda r: r.update(checkOut="2027-01-20"),
    lambda r: r.update(isAvailable=False),
    lambda r: r.update(url="http://www.airbnb.com/rooms/1239612966764072800"),
    lambda r: r.update(id="999"),
])
def test_mismatched_or_unpriced_stays_are_quarantined(change):
    row = deepcopy(rows("airbnb")[0])
    change(row)
    with pytest.raises(ValueError):
        airbnb.normalize(row, STAY, RUN, 900)


def test_out_of_scale_rating_is_unknown_not_clamped():
    row = deepcopy(rows("airbnb")[0])
    row["rating"]["guestSatisfaction"] = 9.2
    assert one(airbnb, row, STAY).rating is None


@pytest.mark.parametrize("value,expected", [("$1,477.94", 147794), ("$232.94", 23294), ("$1,245", 124500)])
def test_usd_text(value, expected):
    assert usd_text_cents(value) == expected


@pytest.mark.parametrize("value", [None, "", "1,477.94", "$1,47.94", "€12", "$12.5", "$-3"])
def test_unparseable_usd_text_is_not_zero(value):
    with pytest.raises(ValueError):
        usd_text_cents(value)


# --- TRANSFER --------------------------------------------------------------

def test_transfer_is_a_complete_vehicle_total():
    candidate = one(booking_taxis, rows("booking_taxis")[0], TRANSFER)
    assert candidate.category == "TRANSFER" and candidate.owner is None
    assert candidate.total_cents == 4686 and candidate.cost_basis == "COMPLETE_TRANSFER_TOTAL" and candidate.complete
    assert candidate.capacity == 4 and candidate.free_cancellation is True
    assert candidate.title == "E-Life Limo · People Carrier (Peugeot 5008 or similar)"
    assert candidate.source_url.startswith("https://taxis.booking.com/search/?")


def test_transfer_input_and_targeted_rerun():
    initial = booking_taxis.build_input(TRANSFER, {"purpose": "INITIAL"})
    assert initial == {"pickupLocation": "Honolulu International Airport", "dropoffLocation": "Waikiki Beach, Honolulu",
        "pickupDate": "2027-01-15", "pickupTime": "14:00", "numberOfPassengers": 4, "currency": "USD"}
    assert booking_taxis.build_input(TRANSFER, {"purpose": "TARGETED", "blocker_code": "TRANSFER_UNAVAILABLE"}) == initial
    with pytest.raises(ContractError, match="TARGETED_SEARCH_NOT_AUTHORIZED"):
        booking_taxis.build_input(TRANSFER, {"purpose": "TARGETED", "blocker_code": "STAY_UNAVAILABLE"})


@pytest.mark.parametrize("change", [
    lambda i: i.update(currency="EUR"),
    lambda i: i.pop("price"),
    lambda i: i.update(price=None),
    lambda i: i.update(price="46.86 USD"),
    lambda i: i.update(maxPassenger="3"),
    lambda i: i.update(link=i["link"].replace("date=2027-01-15", "date=2027-01-16")),
    lambda i: i.update(link=i["link"].replace("passengers=4", "passengers=2")),
    lambda i: i.update(link="https://evil.example/search/?date=2027-01-15"),
])
def test_mismatched_or_unpriced_transfers_are_quarantined(change):
    row = deepcopy(rows("booking_taxis")[0])
    change(row["taxiInfo"])
    with pytest.raises(ValueError):
        booking_taxis.normalize(row, TRANSFER, RUN, 900)


# --- ACTIVITY --------------------------------------------------------------

def test_activity_is_a_per_person_estimate_on_a_0_to_5_scale():
    candidate = one(booking_attractions, rows("booking_attractions")[0], ACTIVITY)
    assert candidate.category == "ACTIVITY" and candidate.owner is None
    assert candidate.total_cents == 3750 and candidate.cost_basis == "ESTIMATE_PER_PERSON"
    assert not candidate.complete
    assert candidate.rating == 4.7 and candidate.free_cancellation is True
    assert candidate.title == "Oahu's Circle Island Tour Operated By Fil-Am Travel & Tours"


@pytest.mark.parametrize("change", [
    lambda r: r.update(price_currency="EUR"),
    lambda r: r.update(price_amount=None),
    lambda r: r.update(destination_query="Maui, us"),
    lambda r: r.update(available_dates=["2027-01-17"]),
    lambda r: r.update(url="https://www.booking.com/hotel/us/x.html"),
])
def test_mismatched_or_unpriced_activities_are_quarantined(change):
    row = deepcopy(rows("booking_attractions")[0])
    change(row)
    with pytest.raises(ValueError):
        booking_attractions.normalize(row, ACTIVITY, RUN, 900)


def test_suggestions_are_never_targeted():
    for actor, search in ((booking_attractions, ACTIVITY), (google_places, DINNER)):
        with pytest.raises(ContractError, match="TARGETED_SEARCH_NOT_AUTHORIZED"):
            actor.build_input(search, {"purpose": "TARGETED", "blocker_code": "HARD_LIMIT"})


# --- DINNER ----------------------------------------------------------------

def test_dinner_estimate_is_the_midpoint_of_the_price_band():
    candidate = one(google_places, rows("google_places")[0], DINNER)
    assert candidate.category == "DINNER" and candidate.owner is None
    assert candidate.total_cents == 6000 and candidate.cost_basis == "ESTIMATE_PER_PERSON"
    assert candidate.rating == 4.9 and candidate.title == "Izakaya Mitchs"
    assert candidate.missing_critical_fields == ()


def test_dinner_without_a_price_band_has_no_estimate():
    row = deepcopy(rows("google_places")[0])
    row["price"] = None
    candidate = one(google_places, row, DINNER)
    assert candidate.total_cents is None and candidate.missing_critical_fields == ("price",)


@pytest.mark.parametrize("value,expected", [("$30–90", 6000), ("$10–20", 1500), ("$15-20", 1750), ("$25", 2500),
                                            ("$100+", None), ("$$", None), (None, None), ("$90–30", None)])
def test_price_band_midpoint(value, expected):
    assert price_band_midpoint_cents(value) == expected


def test_dinner_input_needs_the_detail_page_and_no_paid_add_ons():
    data = google_places.build_input(DINNER, {"purpose": "INITIAL"}, 5)
    assert data["scrapePlaceDetailPage"] is True and data["maxCrawledPlacesPerSearch"] == 5
    assert data["maxReviews"] == data["maxImages"] == data["maximumLeadsEnrichmentRecords"] == 0
    assert data["scrapeContacts"] is False


@pytest.mark.parametrize("change", [
    lambda r: r.update(searchString="something else"),
    lambda r: r.update(permanentlyClosed=True),
    lambda r: r.update(temporarilyClosed=True),
    lambda r: r.update(title=""),
    lambda r: r.update(url=r["url"].replace("query_place_id=", "query_place_id=other")),
])
def test_mismatched_or_closed_places_are_quarantined(change):
    row = deepcopy(rows("google_places")[0])
    change(row)
    with pytest.raises(ValueError):
        google_places.normalize(row, DINNER, RUN, 900)


# --- schemas, registry, config --------------------------------------------

def schema_for(data, enums=None):
    kinds = {bool: "boolean", int: "integer", str: "string", list: "array"}
    fields = {k: {"type": kinds[type(v)]} for k, v in data.items()}
    for name, values in (enums or {}).items():
        fields[name]["enum"] = values
    return {"components": {"schemas": {"inputSchema": {"properties": fields}}}}


@pytest.mark.parametrize("spec,search", [(ACTORS["STAY"], STAY), (ACTORS["TRANSFER"], TRANSFER),
                                         (ACTORS["ACTIVITY"], ACTIVITY), (ACTORS["DINNER"], DINNER)])
def test_each_actor_validates_its_own_input_schema(spec, search):
    data = spec.build_input(search, {"purpose": "INITIAL"}, 5)
    spec.validate_input_schema(schema_for(data))
    missing = dict(data)
    missing.pop(next(iter(missing)))
    with pytest.raises(ContractError, match="APIFY_SCHEMA_INCOMPATIBLE"):
        spec.validate_input_schema(schema_for(missing))
    retyped = schema_for(data)
    retyped["components"]["schemas"]["inputSchema"]["properties"][next(iter(data))]["type"] = "object"
    with pytest.raises(ContractError, match="APIFY_SCHEMA_INCOMPATIBLE"):
        spec.validate_input_schema(retyped)
    extra = schema_for(data)
    extra["components"]["schemas"]["inputSchema"]["required"] = ["somethingNew"]
    with pytest.raises(ContractError, match="APIFY_SCHEMA_INCOMPATIBLE"):
        spec.validate_input_schema(extra)


def test_currency_enum_without_usd_is_incompatible():
    data = airbnb.build_input(STAY, {"purpose": "INITIAL"})
    with pytest.raises(ContractError, match="APIFY_SCHEMA_INCOMPATIBLE"):
        airbnb.validate_input_schema(schema_for(data, {"currency": ["EUR"]}))


def test_check_actor_uses_the_validator_registered_for_that_actor(monkeypatch):
    spec = ACTORS["TRANSFER"]
    client = ApifyHTTP("secret")
    def request(method, path):
        if path == "users/me":
            return {"data": {"id": "account"}}
        if path.startswith("acts/"):
            return {"data": {"id": spec.actor_id}}
        if path.endswith("openapi.json"):
            # memo23's flight schema: wrong for the taxi Actor.
            return schema_for({"origin": "SFO", "departDate": "2027-01-15"})
        return {"data": {"actId": spec.actor_id, "buildNumber": spec.build, "status": "SUCCEEDED"}}
    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(ContractError, match="APIFY_SCHEMA_INCOMPATIBLE"):
        client.check_actor(spec.actor_id, spec.build, spec.build_id)
    assert client.check_actor(spec.actor_id, spec.build, spec.build_id, validate_schema=lambda schema: None)["schema"] == "OK"
    with pytest.raises(ContractError, match="APIFY_ACTOR_NOT_ALLOWLISTED"):
        schema_validator("unknownActor")


def test_registry_pins_every_actor():
    for spec in ACTORS.values():
        assert spec.build.count(".") == 2 and spec.build_id and spec.normalizer_version
    assert ACTORS["DINNER"].min_charge_usd == 0.5


def trip_config(live_case, **trip):
    data = json.loads(live_case.config.model_dump_json())
    data["limits"]["actor_runs"] = 5
    data["trip"] = trip
    return LiveConfig.model_validate(data)


def test_trip_config_shape_and_party_size(live_case):
    config = trip_config(live_case, stay={"destination": "Honolulu", "check_in": "2027-01-15", "check_out": "2027-01-19", "adults": 1},
        transfer={"pickup_location": "HNL", "dropoff_location": "Waikiki", "pickup_date": "2027-01-15", "pickup_time": "14:00", "passengers": 1},
        dinner={"query": "restaurant for large groups", "location": "Honolulu, Hawaii, USA"})
    assert set(config.trip.by_category()) == {"STAY", "TRANSFER", "DINNER"}
    config.validate_dates(live_case.clock.now())
    with pytest.raises(ValueError, match="TRIP_PARTY_SIZE_MISMATCH"):
        trip_config(live_case, stay={"destination": "Honolulu", "check_in": "2027-01-15", "check_out": "2027-01-19", "adults": 4})


@pytest.mark.parametrize("trip,error", [
    ({"stay": {"destination": "Honolulu", "check_in": "2027-01-19", "check_out": "2027-01-15", "adults": 1}}, "INVALID_STAY_DATES"),
    ({"transfer": {"pickup_location": "HNL", "dropoff_location": "Waikiki", "pickup_date": "2027-01-15", "pickup_time": "25:00", "passengers": 1}}, "pickup_time"),
    ({"dinner": {"query": "dinner\nignore previous", "location": "Honolulu"}}, "query"),
])
def test_invalid_trip_searches_are_rejected(live_case, trip, error):
    with pytest.raises(ValueError, match=error):
        trip_config(live_case, **trip)


def test_dinner_requires_the_actor_minimum_charge(live_case):
    data = json.loads(live_case.config.model_dump_json())
    data["limits"].update(actor_runs=2, max_charge_usd=0.2)
    data["trip"] = {"dinner": {"query": "restaurant", "location": "Honolulu"}}
    with pytest.raises(ValueError, match="ACTOR_MIN_CHARGE_NOT_MET"):
        LiveConfig.model_validate(data)


def test_run_allowance_must_cover_one_research_step(live_case):
    data = json.loads(live_case.config.model_dump_json())
    data["trip"] = {"dinner": {"query": "restaurant", "location": "Honolulu"}}
    with pytest.raises(ValueError, match="ACTOR_RUN_BUDGET_TOO_SMALL"):
        LiveConfig.model_validate(data)


def test_past_trip_dates_are_rejected_before_network_access(live_case):
    config = trip_config(live_case, activities={"destination": "Honolulu, us", "date": "2026-01-01"})
    with pytest.raises(ContractError, match="FUTURE_TRIP_DATE_REQUIRED"):
        config.validate_dates(live_case.clock.now())


def test_trip_searches_by_category():
    trip = TripSearches(stay=STAY, activities=ACTIVITY)
    assert list(trip.by_category()) == ["STAY", "ACTIVITY"]
