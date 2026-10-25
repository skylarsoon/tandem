"""Pinned memo23 Google Flights Actor input/output contract.

Published fields: https://apify.com/memo23/google-flights-scraper/input-schema
Only explicit one-way, one-adult USD searches are supported here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from ..contracts import Candidate, ContractError, Record, digest
from .apify import ApprovedFlightSearch

ACTOR_ID = "ulPbFXCmcjxx3iPnp"
ACTOR_BUILD = "0.0.141"
BUILD_ID = "eByt1QXZwUhbeMtQr"
NORMALIZER_VERSION = "memo23-0.0.141-v4"


class MemoFilters(Record):
    sort_by: Literal["top", "price", "departure", "arrival", "duration"] = "top"
    max_stops: int | None = Field(default=None, ge=0, le=2)
    checked_bags: int | None = Field(default=None, ge=0, le=2)


class MemoSearch(ApprovedFlightSearch):
    travelers: Literal[1] = 1
    origin_timezone: str
    destination_timezone: str
    initial_filters: MemoFilters = Field(default_factory=MemoFilters)
    targeted_filters: dict[Literal["HARD_LIMIT", "BAGGAGE_UNKNOWN", "MISSING_FACT", "NO_MATCHING_CANDIDATE", "ARRIVAL_OVERLAP"], MemoFilters] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_zones_and_route(self):
        try:
            ZoneInfo(self.origin_timezone)
            ZoneInfo(self.destination_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("INVALID_AIRPORT_TIMEZONE") from None
        if self.origin == self.destination:
            raise ValueError("IDENTICAL_AIRPORTS")
        return self


def build_input(search: MemoSearch, request: dict, max_items=10):
    filters = search.initial_filters
    if request["purpose"] == "TARGETED":
        filters = search.targeted_filters.get(request.get("blocker_code"))
        if filters is None:
            raise ContractError("TARGETED_SEARCH_NOT_AUTHORIZED")
        if filters == search.initial_filters:
            raise ContractError("TARGETED_CRITERIA_UNCHANGED")
        # Targeting may narrow an approved search, never drop a prior hard filter.
        old = search.initial_filters
        if old.max_stops is not None and (filters.max_stops is None or filters.max_stops > old.max_stops):
            raise ContractError("TARGETED_SEARCH_RELAXES_FILTER")
        if old.checked_bags is not None and (filters.checked_bags is None or filters.checked_bags < old.checked_bags):
            raise ContractError("TARGETED_SEARCH_RELAXES_FILTER")
    result = {"origin": search.origin, "destination": search.destination,
        "departDate": search.departure_date.isoformat(), "adults": 1, "cabinClass": "economy",
        "currency": "USD", "maxItems": max_items, "sortBy": filters.sort_by,
        # Per-seller prices and bag fees for every returned itinerary (see airline_offer).
        "resolveBookingOptions": True, "maxBookingItineraries": max_items}
    if filters.max_stops is not None:
        result["maxStops"] = filters.max_stops
    if filters.checked_bags is not None:
        result["checkedBags"] = filters.checked_bags
    return result


def validate_input_schema(openapi):
    try:
        schema = openapi["components"]["schemas"]["inputSchema"]
        fields = schema["properties"]
    except (KeyError, TypeError):
        raise ContractError("APIFY_SCHEMA_INCOMPATIBLE") from None
    required = {"origin": "string", "destination": "string", "departDate": "string", "adults": "integer",
                "cabinClass": "string", "currency": "string", "maxItems": "integer", "sortBy": "string",
                "maxStops": "integer", "checkedBags": "integer", "resolveBookingOptions": "boolean",
                "maxBookingItineraries": "integer"}
    for name, kind in required.items():
        if fields.get(name, {}).get("type") != kind:
            raise ContractError("APIFY_SCHEMA_INCOMPATIBLE")
    for name, value in (("cabinClass", "economy"), ("sortBy", "top"), ("sortBy", "price")):
        if "enum" in fields[name] and value not in fields[name]["enum"]:
            raise ContractError("APIFY_SCHEMA_INCOMPATIBLE")
    if set(schema.get("required", ())) - set(required):
        raise ContractError("APIFY_SCHEMA_INCOMPATIBLE")


def local_epoch(day, clock, zone):
    """Reject DST ambiguity/nonexistence rather than silently selecting a fold."""
    naive = datetime.fromisoformat(f"{day}T{clock}")
    if naive.tzinfo is not None:
        raise ValueError("EXPECTED_LOCAL_TIME")
    tz = ZoneInfo(zone)
    values = set()
    for fold in (0, 1):
        aware = naive.replace(tzinfo=tz, fold=fold)
        if aware.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == naive:
            values.add(int(aware.timestamp()))
    if len(values) != 1:
        raise ValueError("AMBIGUOUS_OR_NONEXISTENT_TIME")
    return values.pop()


def money_cents(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("PRICE_UNKNOWN")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount * 100 != (amount * 100).to_integral_value():
            raise ValueError("INVALID_PRICE")
        return int(amount * 100)
    except InvalidOperation:
        raise ValueError("INVALID_PRICE") from None


def fee_cents(value):
    """Bag fees may arrive as numbers or display strings like "$35"; unparseable means unknown."""
    if isinstance(value, str):
        value = value.strip().replace("$", "").replace(",", "")
    try:
        return money_cents(value)
    except ValueError:
        return None


def airline_offers(row):
    """Airline-direct fares for this itinerary, cheapest first.

    Google Flights prices include VAT/GST and airport taxes; only optional
    extras (bags, payment-card fees) are excluded
    (https://support.google.com/travel/answer/2475306). Buying direct from the
    airline avoids agency service fees, so these are treated as complete trip
    totals; card fees remain a disclosed limitation, not a hidden zero.

    memo23 lists the airline's fare families as booking options (e.g. United
    "Economy", "Economy Plus") with bag fees, and reports the overall cheapest
    seller separately; when that seller is the airline it is the basic fare.
    """
    count = row.get("booking-options-count")
    if type(count) is not int or count < 1:
        return []
    offers, airlines = [], set()
    # Options are numbered from 0 (booking-option-0-* is usually the basic fare).
    for n in range(count):
        if row.get(f"booking-option-{n}-is-airline") is not True:
            continue
        airlines.add(row.get(f"booking-option-{n}-provider"))
        price = fee_cents(row.get(f"booking-option-{n}-price-usd"))
        if price is not None:
            offers.append({"price": price, "first_bag": fee_cents(row.get(f"booking-option-{n}-first-bag-fee"))})
    cheapest = fee_cents(row.get("cheapest-booking-price-usd"))
    if cheapest is not None and row.get("cheapest-booking-provider") in airlines - {None}:
        offers.append({"price": cheapest, "first_bag": None})
    unique = {}
    for offer in sorted(offers, key=lambda o: (o["price"], o["first_bag"] is None)):
        unique.setdefault(offer["price"], offer)
    return list(unique.values())


def normalize_offers(row, search: MemoSearch, run, *, freshness_seconds=900):
    """One candidate per airline-direct fare, plus a with-one-checked-bag variant
    wherever the airline lists its first-bag fee. Without airline-direct fares,
    the listing as observed (incomplete cost)."""
    offers = airline_offers(row)
    if not offers:
        return [normalize_item(row, search, run, freshness_seconds=freshness_seconds)]
    candidates = []
    for offer in offers:
        fare = _candidate(row, search, run, offer["price"], complete=True, freshness_seconds=freshness_seconds)
        candidates.append(fare)
        if fare.baggage_included is not True and offer["first_bag"] is not None:
            candidates.append(fare.model_copy(update={"id": fare.id + "-bag1", "total_cents": fare.total_cents + offer["first_bag"],
                                                      "checked_bags_included": 1, "baggage_included": True}))
    return [Candidate.model_validate(c.model_dump()) for c in candidates]


def normalize_item(row, search: MemoSearch, run, *, freshness_seconds=900):
    """The cheapest airline-direct fare when known, otherwise the listing as observed."""
    offers = airline_offers(row)
    if offers:
        return _candidate(row, search, run, offers[0]["price"], complete=True, freshness_seconds=freshness_seconds)
    return _candidate(row, search, run, money_cents(row["price"]), complete=False, freshness_seconds=freshness_seconds)


def _candidate(row, search: MemoSearch, run, price, *, complete, freshness_seconds):
    if row.get("record-type") != "flight" or row.get("currency") != "USD":
        raise ValueError("UNSUPPORTED_ROW")
    if row.get("from-code") != search.origin or row.get("to-code") != search.destination or row.get("depart-date") != search.departure_date.isoformat():
        raise ValueError("RETURNED_ROUTE_MISMATCH")
    if row.get("return-date") or row.get("trip-type") not in (None, "one-way", "oneway", "one_way"):
        raise ValueError("NOT_ONE_WAY")
    # The live pinned build emits the human-readable string "1 adult".
    passengers = row.get("passengers")
    if isinstance(passengers, bool) or passengers not in (None, 1, "1 adult"):
        raise ValueError("PASSENGER_COUNT_MISMATCH")
    money_cents(row["price"])  # a listing without a valid price is malformed
    departed = local_epoch(row["depart-date"], row["depart-time"], search.origin_timezone)
    arrived = local_epoch(row["arrive-date"], row["arrive-time"], search.destination_timezone)
    if arrived < departed:
        raise ValueError("INVALID_CHRONOLOGY")
    url = row["google-flights-url"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"google.com", "www.google.com"} or not parsed.path.startswith("/travel/flights"):
        raise ValueError("INVALID_SOURCE_URL")
    bags = row.get("checked-bags-included")
    if bags is not None and (type(bags) is not int or bags < 0):
        bags = None
    # The legs listed are what the trip is; a stated "stops" that disagrees with them loses.
    legs = {k.split("-")[1] for k in row if k.startswith("segments-") and k.endswith("-flight-number")}
    stated = row["stops"] if type(row.get("stops")) is int and row["stops"] >= 0 else None
    stops = len(legs) - 1 if legs else stated
    identity = digest([search.origin, search.destination, departed, arrived, row.get("carrier-code"),
                       sorted((k, v) for k, v in row.items() if k.startswith("segments-") and k.endswith("flight-number"))])
    candidate_id = "apify-" + digest([search.owner, identity, run["run_id"], run["observed_at"], price])[:28]
    return Candidate(id=candidate_id, owner=search.owner, total_cents=price,
        arrival_epoch=arrived, departure_epoch=departed, departure_timezone=search.origin_timezone,
        arrival_timezone=search.destination_timezone, observed_at=run["observed_at"],
        arrival_local=datetime.fromtimestamp(arrived, ZoneInfo(search.destination_timezone)).strftime("%H:%M"),
        stops=stops,
        valid_until=run["observed_at"] + freshness_seconds, source_url=url, run_ref=run["run_id"],
        actor_id=ACTOR_ID, actor_build=run["build"], dataset_ref=run["dataset_id"], flight_identity=identity,
        mode="APIFY_OBSERVED",
        # A search listing alone does not establish complete mandatory costs;
        # an airline-direct booking price does (see airline_offers).
        cost_basis="COMPLETE_FLIGHT_TOTAL" if complete else "OBSERVED_FLIGHT_PRICE",
        missing_critical_fields=() if complete else ("mandatory_fees",), checked_bags_included=bags,
        baggage_included=None if bags is None else bags >= 1)
