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
NORMALIZER_VERSION = "memo23-0.0.141-v2"


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
        "currency": "USD", "maxItems": max_items, "sortBy": filters.sort_by}
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
                "maxStops": "integer", "checkedBags": "integer"}
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


def normalize_item(row, search: MemoSearch, run, *, freshness_seconds=900):
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
    price = money_cents(row["price"])
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
    identity = digest([search.origin, search.destination, departed, arrived, row.get("carrier-code"),
                       sorted((k, v) for k, v in row.items() if k.startswith("segments-") and k.endswith("flight-number"))])
    candidate_id = "apify-" + digest([search.owner, identity, run["run_id"], run["observed_at"], price])[:28]
    return Candidate(id=candidate_id, owner=search.owner, total_cents=price,
        arrival_epoch=arrived, departure_epoch=departed, departure_timezone=search.origin_timezone,
        arrival_timezone=search.destination_timezone, observed_at=run["observed_at"],
        valid_until=run["observed_at"] + freshness_seconds, source_url=url, run_ref=run["run_id"],
        actor_id=ACTOR_ID, actor_build=run["build"], dataset_ref=run["dataset_id"], flight_identity=identity,
        mode="APIFY_OBSERVED", cost_basis="OBSERVED_FLIGHT_PRICE",
        # A search listing alone does not establish complete mandatory costs.
        missing_critical_fields=("mandatory_fees",), checked_bags_included=bags,
        baggage_included=None if bags is None else bags >= 1)
