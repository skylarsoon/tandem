"""Pinned Apify Actor contracts for the shared parts of a trip.

Each Actor has a pinned build, an input builder that receives only confirmed
search facts (never caps, reasons or transcripts), an input-schema check run
during preflight, and a normalizer that quarantines (raises ValueError on)
any row that does not match the approved search. A missing price is never
read as zero.

- STAY: tri_angle/airbnb-scraper. A total is complete only when the listing's
  own breakdown proves it includes taxes (total == base + taxes, to the cent).
- TRANSFER: voyager/booking-airport-taxis-scraper. Booking taxis are a fixed
  price for the whole vehicle.
- ACTIVITY: axlymxp/booking-attractions-scraper. A per-person "from" price.
- DINNER: compass/crawler-google-places. Google's per-person price band; the
  midpoint is the estimate.

Car rental is deliberately absent: the inspected Actors do not say whether
taxes and fees are included.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
import datetime as dt
from decimal import Decimal
from typing import Annotated, Callable
from urllib.parse import parse_qs, urlparse

from pydantic import Field, model_validator

from ..contracts import Candidate, ContractError, Record, digest
from .memo23 import money_cents

# Free text the Actors receive: a place name or a search phrase, nothing else.
SafeText = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")]


class StaySearch(Record):
    destination: SafeText
    check_in: dt.date
    check_out: dt.date
    adults: int = Field(gt=0, le=16)
    max_items: int = Field(default=10, ge=1, le=25)

    @model_validator(mode="after")
    def valid_dates(self):
        nights = (self.check_out - self.check_in).days
        if nights < 1 or nights > 30:
            raise ValueError("INVALID_STAY_DATES")
        return self


class TransferSearch(Record):
    pickup_location: SafeText
    dropoff_location: SafeText
    pickup_date: dt.date
    pickup_time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    passengers: int = Field(gt=0, le=16)  # the Actor's own maximum

    @model_validator(mode="after")
    def distinct_places(self):
        if self.pickup_location.strip().lower() == self.dropoff_location.strip().lower():
            raise ValueError("IDENTICAL_TRANSFER_PLACES")
        return self


class ActivitySearch(Record):
    destination: SafeText  # as Booking expects it, e.g. "Honolulu, us"
    date: dt.date


class DinnerSearch(Record):
    query: SafeText  # e.g. "restaurant for large groups"
    location: SafeText  # e.g. "Honolulu, Hawaii, USA"


class TripSearches(Record):
    stay: StaySearch | None = None
    transfer: TransferSearch | None = None
    activities: ActivitySearch | None = None
    dinner: DinnerSearch | None = None

    def by_category(self):
        """Configured searches keyed by Candidate category."""
        pairs = (("STAY", self.stay), ("TRANSFER", self.transfer), ("ACTIVITY", self.activities), ("DINNER", self.dinner))
        return {category: search for category, search in pairs if search is not None}

    def dates(self):
        return [d for d in (self.stay and self.stay.check_in, self.transfer and self.transfer.pickup_date,
                            self.activities and self.activities.date) if d]


# --- shared helpers -------------------------------------------------------

USD_TEXT = re.compile(r"^\$(\d{1,3}(?:,\d{3})+|\d+)(\.\d{2})?$")


def usd_text_cents(value):
    """"$1,477.94" -> 147794. Only a plain US-dollar amount; "CA$", "€" or prose is unknown."""
    if not isinstance(value, str):
        raise ValueError("PRICE_UNKNOWN")
    match = USD_TEXT.match(value.strip())
    if not match:
        raise ValueError("INVALID_PRICE")
    return money_cents(match.group(1).replace(",", "") + (match.group(2) or ""))


def https_url(value, hosts, path_prefix):
    if not isinstance(value, str):
        raise ValueError("INVALID_SOURCE_URL")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in hosts or not parsed.path.startswith(path_prefix):
        raise ValueError("INVALID_SOURCE_URL")
    return parsed


def rating_0_5(value):
    """Scores already on a 0-5 scale; anything else is unknown, not clamped."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 5:
        return None
    return round(float(value), 2)


def text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def candidate_id(category, identity, run, price):
    return f"apify-{category.lower()}-" + digest([category, identity, run["run_id"], run["observed_at"], price])[:28]


def evidence(category, identity, run, price, freshness_seconds, actor_id, **fields):
    return Candidate(id=candidate_id(category, identity, run, price), category=category, owner=None,
        total_cents=price, observed_at=run["observed_at"], valid_until=run["observed_at"] + freshness_seconds,
        run_ref=run["run_id"], mode="APIFY_OBSERVED", actor_id=actor_id, actor_build=run["build"],
        dataset_ref=run["dataset_id"], **fields)


def require_fields(openapi, required, enums=()):
    """The pinned build must accept every field we send, with the same type, and
    must not require anything we do not send."""
    try:
        schema = openapi["components"]["schemas"]["inputSchema"]
        fields = schema["properties"]
    except (KeyError, TypeError):
        raise ContractError("APIFY_SCHEMA_INCOMPATIBLE") from None
    for name, kind in required.items():
        if not isinstance(fields.get(name), dict) or fields[name].get("type") != kind:
            raise ContractError("APIFY_SCHEMA_INCOMPATIBLE")
    for name, value in enums:
        if "enum" in fields[name] and value not in fields[name]["enum"]:
            raise ContractError("APIFY_SCHEMA_INCOMPATIBLE")
    if set(schema.get("required") or ()) - set(required):
        raise ContractError("APIFY_SCHEMA_INCOMPATIBLE")


def targeted_blocker(request, allowed):
    """None for an initial/refresh search; the blocker for an authorized targeted one."""
    if request["purpose"] != "TARGETED":
        return None
    blocker = request.get("blocker_code")
    if blocker not in allowed:
        raise ContractError("TARGETED_SEARCH_NOT_AUTHORIZED")
    return blocker


# --- STAY: tri_angle/airbnb-scraper ----------------------------------------

class airbnb:
    ACTOR_ID = "GsNzxEKzE2vQ5d9HN"
    BUILD = "0.0.102"
    BUILD_ID = "ZKEC1xxy7wgCWKZe5"
    NORMALIZER_VERSION = "airbnb-0.0.102-v1"
    MAX_TARGETED_ITEMS = 50

    @staticmethod
    def max_results(search: StaySearch, request):
        # No sort option exists: a cheaper-stay search (HARD_LIMIT) re-runs the
        # approved search; a stay that fits nobody (STAY_UNAVAILABLE) looks wider.
        blocker = targeted_blocker(request, {"STAY_UNAVAILABLE", "HARD_LIMIT"})
        if blocker == "STAY_UNAVAILABLE":
            return min(search.max_items * 2, airbnb.MAX_TARGETED_ITEMS)
        return search.max_items

    @staticmethod
    def build_input(search: StaySearch, request, max_items=None):
        return {"locationQueries": [search.destination], "checkIn": search.check_in.isoformat(),
                "checkOut": search.check_out.isoformat(), "adults": search.adults, "currency": "USD",
                "locale": "en-US", "maxResults": airbnb.max_results(search, request)}

    @staticmethod
    def validate_input_schema(openapi):
        require_fields(openapi, {"locationQueries": "array", "checkIn": "string", "checkOut": "string",
            "adults": "integer", "currency": "string", "locale": "string", "maxResults": "integer"},
            (("currency", "USD"), ("locale", "en-US")))

    @staticmethod
    def bedrooms(row):
        """From the listing facts, e.g. ["5 guests", "2 bedrooms", ...]; "Studio" is 0."""
        items = (row.get("subDescription") or {}).get("items")
        if not isinstance(items, list):
            return None
        found = set()
        for item in items:
            if not isinstance(item, str):
                continue
            item = item.strip().lower()
            if item == "studio":
                found.add(0)
            elif match := re.fullmatch(r"(\d+) bedrooms?", item):
                found.add(int(match.group(1)))
        return found.pop() if len(found) == 1 else None

    @staticmethod
    def normalize(row, search: StaySearch, run, freshness_seconds=900):
        if row.get("isAvailable") is not True:
            raise ValueError("UNAVAILABLE")
        if row.get("checkIn") != search.check_in.isoformat() or row.get("checkOut") != search.check_out.isoformat():
            raise ValueError("RETURNED_DATES_MISMATCH")
        url = https_url(row.get("url"), {"www.airbnb.com", "airbnb.com"}, "/rooms/")
        if parse_qs(url.query).get("currency") != ["USD"]:
            raise ValueError("UNSUPPORTED_CURRENCY")
        listing = row.get("id")
        if not isinstance(listing, str) or not listing.isdigit() or url.path != f"/rooms/{listing}":
            raise ValueError("INVALID_LISTING")
        price = row.get("price") or {}
        breakdown = price.get("breakDown") or {}
        total = usd_text_cents((breakdown.get("total") or {}).get("price"))
        # Complete only when the listing's own arithmetic shows the total
        # includes taxes; otherwise an observed price with taxes unproven.
        try:
            proven = (price.get("qualifier") == "total"
                      and usd_text_cents((breakdown.get("basePrice") or {}).get("price"))
                      + usd_text_cents((breakdown.get("taxes") or {}).get("price")) == total)
        except ValueError:
            proven = False
        capacity = row.get("personCapacity")
        return [evidence("STAY", listing, run, total, freshness_seconds, airbnb.ACTOR_ID,
            source_url=url.geturl(), title=text(row.get("title")),
            capacity=capacity if type(capacity) is int and capacity >= 1 else None,
            bedrooms=airbnb.bedrooms(row), rating=rating_0_5((row.get("rating") or {}).get("guestSatisfaction")),
            check_in=search.check_in.isoformat(), check_out=search.check_out.isoformat(),
            cost_basis="COMPLETE_STAY_TOTAL" if proven else "OBSERVED_PRICE",
            missing_critical_fields=() if proven else ("taxes",))]


# --- TRANSFER: voyager/booking-airport-taxis-scraper -----------------------

class booking_taxis:
    ACTOR_ID = "HqvTOe7lS2kYNRfgV"
    BUILD = "0.0.49"
    BUILD_ID = "Emrc7eKrydQaXH8VR"
    NORMALIZER_VERSION = "booking-taxis-0.0.49-v1"

    @staticmethod
    def build_input(search: TransferSearch, request, max_items=None):
        # The Actor has neither a sort nor a size option: a targeted search
        # re-runs the approved one for a fresh observation.
        targeted_blocker(request, {"TRANSFER_UNAVAILABLE", "HARD_LIMIT"})
        return {"pickupLocation": search.pickup_location, "dropoffLocation": search.dropoff_location,
                "pickupDate": search.pickup_date.isoformat(), "pickupTime": search.pickup_time,
                "numberOfPassengers": search.passengers, "currency": "USD"}

    @staticmethod
    def validate_input_schema(openapi):
        require_fields(openapi, {"pickupLocation": "string", "dropoffLocation": "string", "pickupDate": "string",
            "pickupTime": "string", "numberOfPassengers": "integer", "currency": "string"}, (("currency", "USD"),))

    @staticmethod
    def normalize(row, search: TransferSearch, run, freshness_seconds=900):
        info = row.get("taxiInfo")
        if not isinstance(info, dict):
            raise ValueError("UNSUPPORTED_ROW")
        if info.get("currency") != "USD":
            raise ValueError("UNSUPPORTED_CURRENCY")
        if info.get("publicTransport") is True:
            raise ValueError("NOT_A_PRIVATE_VEHICLE")
        price = money_cents(info.get("price"))
        url = https_url(info.get("link"), {"taxis.booking.com"}, "/")
        query = parse_qs(url.query)
        # The booking link encodes what Booking actually priced.
        if (query.get("date") != [search.pickup_date.isoformat()] or query.get("time") != [search.pickup_time]
                or query.get("passengers") != [str(search.passengers)] or query.get("currency", ["USD"]) != ["USD"]):
            raise ValueError("RETURNED_SEARCH_MISMATCH")
        seats = info.get("maxPassenger")
        seats = int(seats) if isinstance(seats, (int, str)) and not isinstance(seats, bool) and str(seats).isdigit() else None
        if seats is not None and seats < search.passengers:
            raise ValueError("PASSENGER_COUNT_MISMATCH")
        car = info.get("carDetails") or {}
        vehicle = text(car.get("description"))
        model = text(car.get("modelDescription")) or text(car.get("model"))
        supplier = text(info.get("supplierName"))
        title = " · ".join(p for p in (supplier, f"{vehicle} ({model})" if vehicle and model else vehicle or model) if p) or None
        cancellation = info.get("twentyFourHourCancellation")
        identity = [info.get("supplierID"), info.get("type"), model, search.model_dump(mode="json")]
        return [evidence("TRANSFER", identity, run, price, freshness_seconds, booking_taxis.ACTOR_ID,
            source_url=url.geturl(), title=title, capacity=seats if seats else None,
            free_cancellation=cancellation if isinstance(cancellation, bool) else None,
            cost_basis="COMPLETE_TRANSFER_TOTAL")]


# --- ACTIVITY: axlymxp/booking-attractions-scraper ------------------------

class booking_attractions:
    ACTOR_ID = "boYdMVP9TW1cfQxuE"
    BUILD = "1.0.1"
    BUILD_ID = "0P9yIvu9gbsgwzEiw"
    NORMALIZER_VERSION = "booking-attractions-1.0.1-v1"

    @staticmethod
    def build_input(search: ActivitySearch, request, max_items=10):
        # Suggestions never block a plan, so nothing targets them.
        targeted_blocker(request, set())
        day = search.date.isoformat()
        return {"destinations": [search.destination], "startDate": day, "endDate": day,
                "maxItemsPerLocation": max_items, "includeDetails": True, "includeAvailability": True,
                "includeReviews": False, "currency": "USD", "language": "en-us"}

    @staticmethod
    def validate_input_schema(openapi):
        require_fields(openapi, {"destinations": "array", "startDate": "string", "endDate": "string",
            "maxItemsPerLocation": "integer", "includeDetails": "boolean", "includeAvailability": "boolean",
            "includeReviews": "boolean", "currency": "string", "language": "string"},
            (("currency", "USD"), ("language", "en-us")))

    @staticmethod
    def normalize(row, search: ActivitySearch, run, freshness_seconds=900):
        if row.get("price_currency") != "USD":
            raise ValueError("UNSUPPORTED_CURRENCY")
        if row.get("destination_query") != search.destination:
            raise ValueError("RETURNED_DESTINATION_MISMATCH")
        dates = row.get("available_dates")
        if dates is not None and (not isinstance(dates, list) or search.date.isoformat() not in dates):
            raise ValueError("NOT_AVAILABLE_ON_DATE")
        # Booking shows a per-person "from" price: an estimate, never a total.
        price = money_cents(row.get("price_amount"))
        title, identity = text(row.get("name")), row.get("id")
        if not title or not isinstance(identity, str) or not identity:
            raise ValueError("UNSUPPORTED_ROW")
        url = https_url(row.get("url"), {"www.booking.com"}, "/attractions/")
        cancellation = row.get("has_free_cancellation")
        # review_score is Booking's attractions scale, 0-5 (e.g. 4.7 next to
        # category averages of the same magnitude), unlike its 0-10 hotel score.
        return [evidence("ACTIVITY", identity, run, price, freshness_seconds, booking_attractions.ACTOR_ID,
            source_url=url.geturl(), title=title, rating=rating_0_5(row.get("review_score")),
            free_cancellation=cancellation if isinstance(cancellation, bool) else None,
            cost_basis="ESTIMATE_PER_PERSON")]


# --- DINNER: compass/crawler-google-places ---------------------------------

PRICE_BAND = re.compile(r"^\$(\d{1,4})(?:\s*[–-]\s*\$?(\d{1,4}))?$")


def price_band_midpoint_cents(value):
    """Google's per-person band "$30–90" -> 6000; "$100+", "$$" or none -> None."""
    match = PRICE_BAND.match(value.strip()) if isinstance(value, str) else None
    if not match:
        return None
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) else low
    if high < low:
        return None
    return int((Decimal(low + high) * 50).to_integral_value())


class google_places:
    ACTOR_ID = "nwua9Gu5YrADL7ZDj"
    BUILD = "0.14.775"
    BUILD_ID = "EH35UvIrsV0hnZG0Y"
    NORMALIZER_VERSION = "google-places-0.14.775-v1"
    MIN_CHARGE_USD = 0.5  # Apify refuses lower maxTotalChargeUsd for this Actor.

    @staticmethod
    def build_input(search: DinnerSearch, request, max_items=10):
        targeted_blocker(request, set())
        # Price bands appear only on the detail page; every paid add-on stays off.
        return {"searchStringsArray": [search.query], "locationQuery": search.location,
                "maxCrawledPlacesPerSearch": max_items, "language": "en", "scrapePlaceDetailPage": True,
                "skipClosedPlaces": True, "maxReviews": 0, "maxImages": 0, "maxQuestions": 0,
                "scrapeContacts": False, "maximumLeadsEnrichmentRecords": 0}

    @staticmethod
    def validate_input_schema(openapi):
        require_fields(openapi, {"searchStringsArray": "array", "locationQuery": "string",
            "maxCrawledPlacesPerSearch": "integer", "language": "string", "scrapePlaceDetailPage": "boolean",
            "skipClosedPlaces": "boolean", "maxReviews": "integer", "maxImages": "integer", "maxQuestions": "integer",
            "scrapeContacts": "boolean", "maximumLeadsEnrichmentRecords": "integer"}, (("language", "en"),))

    @staticmethod
    def normalize(row, search: DinnerSearch, run, freshness_seconds=900):
        if row.get("searchString") != search.query:
            raise ValueError("RETURNED_SEARCH_MISMATCH")
        if row.get("permanentlyClosed") is not False or row.get("temporarilyClosed") is True:
            raise ValueError("CLOSED")
        title, place = text(row.get("title")), row.get("placeId")
        if not title or not isinstance(place, str) or not place:
            raise ValueError("UNSUPPORTED_ROW")
        url = https_url(row.get("url"), {"www.google.com", "google.com"}, "/maps")
        if parse_qs(url.query).get("query_place_id", [place]) != [place]:
            raise ValueError("INVALID_SOURCE_URL")
        estimate = price_band_midpoint_cents(row.get("price"))
        # Candidate has no field for reserveTableUrl, so it is not kept.
        return [evidence("DINNER", place, run, estimate, freshness_seconds, google_places.ACTOR_ID,
            source_url=url.geturl(), title=title, rating=rating_0_5(row.get("totalScore")),
            cost_basis="ESTIMATE_PER_PERSON", missing_critical_fields=() if estimate is not None else ("price",))]


# --- registry ---------------------------------------------------------------

@dataclass(frozen=True)
class ActorSpec:
    category: str
    actor_id: str
    build: str
    build_id: str
    normalizer_version: str
    build_input: Callable  # (search, request, max_items) -> dict
    normalize: Callable  # (item, search, run, freshness_seconds) -> list[Candidate]
    validate_input_schema: Callable  # (openapi) -> None, raising ContractError
    min_charge_usd: float = 0.0


ACTORS = {spec.category: spec for spec in (
    ActorSpec("STAY", airbnb.ACTOR_ID, airbnb.BUILD, airbnb.BUILD_ID, airbnb.NORMALIZER_VERSION,
              airbnb.build_input, airbnb.normalize, airbnb.validate_input_schema),
    ActorSpec("TRANSFER", booking_taxis.ACTOR_ID, booking_taxis.BUILD, booking_taxis.BUILD_ID, booking_taxis.NORMALIZER_VERSION,
              booking_taxis.build_input, booking_taxis.normalize, booking_taxis.validate_input_schema),
    ActorSpec("ACTIVITY", booking_attractions.ACTOR_ID, booking_attractions.BUILD, booking_attractions.BUILD_ID,
              booking_attractions.NORMALIZER_VERSION, booking_attractions.build_input, booking_attractions.normalize,
              booking_attractions.validate_input_schema),
    ActorSpec("DINNER", google_places.ACTOR_ID, google_places.BUILD, google_places.BUILD_ID, google_places.NORMALIZER_VERSION,
              google_places.build_input, google_places.normalize, google_places.validate_input_schema,
              google_places.MIN_CHARGE_USD),
)}

SCHEMA_VALIDATORS = {spec.actor_id: spec.validate_input_schema for spec in ACTORS.values()}
