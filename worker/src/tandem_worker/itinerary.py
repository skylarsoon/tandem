"""Round trips from one-way searches. The flight actor searches one way, so a person's
outbound and return results are paired here into itineraries: one candidate each, priced
as both legs together, before anything is stored or planned. Pairs are pruned first: a
person's cheapest few outbound legs with their cheapest few return legs."""
from __future__ import annotations

from .contracts import Candidate

OUTBOUND_KEPT, RETURN_KEPT = 6, 4


def pair_itineraries(candidates: list[Candidate]) -> list[Candidate]:
    """Each person's one-way legs as round-trip itineraries; everything else unchanged. A
    person with no return leg found keeps their outbound legs alone (it says so: no return)."""
    legs: dict[str, dict[str, list[Candidate]]] = {}
    rest = []
    for c in candidates:
        if c.category == "FLIGHT" and c.leg is not None and c.owner is not None:
            legs.setdefault(c.owner, {"outbound": [], "return": []})[c.leg].append(c)
        else:
            rest.append(c)
    for found in legs.values():
        outbound = sorted(found["outbound"], key=lambda c: (c.total_cents is None, c.total_cents or 0, c.id))[:OUTBOUND_KEPT]
        back = sorted(found["return"], key=lambda c: (c.total_cents is None, c.total_cents or 0, c.id))[:RETURN_KEPT]
        if not back:
            rest += [o.model_copy(update={"leg": None}) for o in found["outbound"]]
            continue
        rest += [pair(o, r) for o in outbound for r in back if r.departure_epoch and o.arrival_epoch
                 and r.departure_epoch > o.arrival_epoch]
    return rest


def pair(out: Candidate, back: Candidate) -> Candidate:
    """One itinerary: the outbound's identity and times, the return's as the way home, both
    prices together. Complete only if both legs' prices are; a bag only if both include one."""
    both = [out, back]
    total = None if any(c.total_cents is None for c in both) else out.total_cents + back.total_cents
    bags = [c.baggage_included for c in both]
    data = out.model_dump(exclude={"departure_date", "arrival_date", "duration_minutes", "return_departure_date",
                                   "return_arrival_date", "return_duration_minutes"})
    data.update(id=f"{out.id}+{back.id}", total_cents=total, leg=None,
                cost_basis=out.cost_basis if out.cost_basis == back.cost_basis else "OBSERVED_FLIGHT_PRICE",
                missing_critical_fields=tuple(dict.fromkeys(out.missing_critical_fields + back.missing_critical_fields)),
                baggage_included=False if False in bags else None if None in bags else True,
                checked_bags_included=min((c.checked_bags_included for c in both if c.checked_bags_included is not None), default=None),
                return_departure_epoch=back.departure_epoch, return_arrival_epoch=back.arrival_epoch, return_stops=back.stops,
                flight_identity=f"{out.flight_identity}|{back.flight_identity}",
                observed_at=min(c.observed_at for c in both), valid_until=min(c.valid_until for c in both))
    return Candidate.model_validate(data)
