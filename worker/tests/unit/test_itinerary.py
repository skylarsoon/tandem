from tandem_worker.contracts import Candidate
from tandem_worker.itinerary import pair_itineraries
from tandem_worker.serve import searches_for

DAY = 86400


def leg(cid, owner, leg_, price, depart, arrive, bag=True, stops=0, basis="COMPLETE_FLIGHT_TOTAL"):
    out = leg_ == "outbound"
    return Candidate(id=cid, owner=owner, total_cents=price, leg=leg_, departure_epoch=depart, arrival_epoch=arrive,
                     departure_timezone="America/Los_Angeles" if out else "Pacific/Honolulu",
                     arrival_timezone="Pacific/Honolulu" if out else "America/Los_Angeles", stops=stops, baggage_included=bag,
                     flight_identity=cid, observed_at=0, valid_until=1000, source_url="https://fixtures.invalid", run_ref="r",
                     mode="FIXTURE", cost_basis=basis)


T0 = 1_795_000_000


def test_one_way_legs_become_round_trip_itineraries():
    found = [leg("out1", "chris", "outbound", 30_000, T0, T0 + 5 * 3600),
             leg("back1", "chris", "return", 25_000, T0 + 3 * DAY, T0 + 3 * DAY + 6 * 3600, bag=False, stops=1)]
    [trip] = pair_itineraries(found)
    assert trip.id == "out1+back1" and trip.leg is None and trip.total_cents == 55_000
    assert (trip.return_departure_epoch, trip.return_arrival_epoch, trip.return_stops) == (T0 + 3 * DAY, T0 + 3 * DAY + 6 * 3600, 1)
    assert trip.baggage_included is False, "a bag only if both legs include one"
    assert trip.return_arrival_date is not None and trip.return_duration_minutes == 360


def test_a_way_home_before_arriving_is_no_itinerary_and_no_return_leaves_one_way():
    out = leg("out1", "chris", "outbound", 30_000, T0, T0 + 5 * 3600)
    early = leg("back0", "chris", "return", 20_000, T0 - DAY, T0 - DAY + 3600)
    assert pair_itineraries([out, early]) == []
    [alone] = pair_itineraries([leg("out2", "dana", "outbound", 31_000, T0, T0 + 3600)])
    assert alone.leg is None and alone.return_arrival_epoch is None


def test_an_incomplete_leg_makes_an_incomplete_itinerary():
    out = leg("o", "a", "outbound", 30_000, T0, T0 + 3600)
    back = leg("b", "a", "return", 20_000, T0 + DAY, T0 + DAY + 3600, basis="OBSERVED_FLIGHT_PRICE")
    [trip] = pair_itineraries([out, back])
    assert trip.cost_basis == "OBSERVED_FLIGHT_PRICE" and not trip.complete


def test_a_route_with_a_return_date_searches_the_way_home_too():
    flights, _ = searches_for({"routes": [{"owner": "chris", "origin": "SMF", "destination": "HNL", "date": "2026-11-21",
                                           "return_date": "2026-11-24", "max_stops": None}]}, "dinner")
    assert [(f.origin, f.destination, f.departure_date.isoformat(), f.leg) for f in flights] == [
        ("SMF", "HNL", "2026-11-21", "outbound"), ("HNL", "SMF", "2026-11-24", "return")]
