"""Trip CP-SAT solver with a fairness policy, plus an independent validator.

A plan is one flight per traveller plus, when the room asks for them, one
shared stay and one shared airport transfer split equally.

Policy minimax_v1:
1. Every confirmed hard limit holds: flight cap, whole-trip cap (own flight +
   equal share), required checked bag, room needs (guests, bedrooms, rating),
   transfer seats, and all arrivals within the room's window.
2. A traveller's burden is how much more they pay than the cheapest trip
   available to them. Minimize the largest burden first, so nobody absorbs the
   whole compromise, then minimize the group's total cost.

Shares make costs fractional, so the model works in party-size-scaled cents
(n x own flight + shared totals): exact integers, same ordering.

Results explain themselves: per-traveller impacts (private), a room-safe group
summary, and, when nothing fits, which limit blocks it and by how much.
"""
from __future__ import annotations

from ortools.sat.python import cp_model

from .contracts import Conflict, GroupSummary, Impact, SolverResult, ValidationReport, digest

POLICY = "minimax_v1"
STATUS = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE", cp_model.INFEASIBLE: "INFEASIBLE",
          cp_model.MODEL_INVALID: "MODEL_INVALID", cp_model.UNKNOWN: "UNKNOWN"}


def closest_spread(options_by_owner):
    """Smallest arrival window that contains one option per traveller (sweep)."""
    events = sorted((c.arrival_epoch, owner) for owner, options in options_by_owner.items() for c in options)
    need, counts, have, best, left = len(options_by_owner), {}, 0, None, 0
    for right, (arrival, owner) in enumerate(events):
        counts[owner] = counts.get(owner, 0) + 1
        have += counts[owner] == 1
        while have == need:
            span = arrival - events[left][0]
            best = span if best is None else min(best, span)
            gone = events[left][1]
            counts[gone] -= 1
            have -= counts[gone] == 0
            left += 1
    return best


def minutes(hhmm):
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


def lands_in_window(candidate, window) -> bool:
    """Is the local arrival inside {earliest, latest}? A window may wrap midnight."""
    if not window:
        return True
    if candidate.arrival_local is None:
        return False
    at = minutes(candidate.arrival_local)
    start, end = minutes(window.get("earliest") or "00:00"), minutes(window.get("latest") or "23:59")
    return start <= at <= end if start <= end else at >= start or at <= end


def stops_ok(candidate, max_stops) -> bool:
    return max_stops is None or (candidate.stops is not None and candidate.stops <= max_stops)


def flight_violation(candidate, brief) -> str | None:
    """Which of the owner's confirmed hard limits a priced flight is known to
    break, if any. Unknown baggage isn't a violation: the owner's agent can
    object and ask for evidence. The solver and the shortlist share this."""
    if candidate.total_cents > brief.hard_cap_cents:
        return "OVER_FLIGHT_LIMIT"
    if brief.baggage_required and candidate.baggage_included is False:
        return "NO_BAG"
    if not lands_in_window(candidate, brief.arrival_window):
        return "ARRIVAL_TIME"
    if not stops_ok(candidate, brief.max_stops):
        return "TOO_MANY_STOPS"
    return None


def bedrooms_needed(briefs, per_room=2):
    """Own-room travellers get one each; everyone else shares `per_room` to a
    room (the room's people_per_bedroom setting)."""
    own = sum(1 for b in briefs if b.own_bedroom)
    return own + -(-(len(briefs) - own) // per_room)


def stay_filters(briefs, relax=(), per_room=2):
    """Room requirements in the order they're explained; `relax` drops some."""
    n, rooms = len(briefs), bedrooms_needed(briefs, per_room) if "BEDROOMS" not in relax else 0
    rating = max((b.stay_min_rating for b in briefs if b.stay_min_rating is not None), default=None)
    if "RATING" in relax:
        rating = None
    return [("CAPACITY", lambda c: c.capacity is not None and c.capacity >= n),
            ("BEDROOMS", lambda c: c.bedrooms is not None and c.bedrooms >= rooms),
            ("RATING", lambda c: rating is None or (c.rating is not None and c.rating >= rating))]


class TripSolver:
    def __init__(self, time_limit=5):
        self.time_limit = time_limit

    def solve(self, snapshot, briefs, candidates):
        members = list(snapshot.members)
        n, window = len(members), snapshot.arrival_overlap_seconds
        by_owner = {b.owner: b for b in briefs}
        priced = [c for c in candidates if c.total_cents is not None]
        # Known to break a hard requirement → not an option.
        flights = {o: [c for c in priced if c.category == "FLIGHT" and c.owner == o and flight_violation(c, by_owner[o]) is None]
                   for o in members}
        conflicts = [self.owner_conflict(o, by_owner[o], candidates) for o in members if not flights[o]]

        shared, shared_conflicts = {}, []
        if "STAY" in snapshot.components:
            stays = [c for c in priced if c.category == "STAY"]
            for requirement, ok in stay_filters(briefs, per_room=snapshot.people_per_bedroom):
                if not stays:
                    break
                fits = [c for c in stays if ok(c)]
                if not fits:
                    shared_conflicts.append(Conflict(code="STAY_UNAVAILABLE", requirement=requirement))
                stays = fits
            if not stays and not shared_conflicts:
                shared_conflicts.append(Conflict(code="STAY_UNAVAILABLE"))
            shared["STAY"] = stays
        if "TRANSFER" in snapshot.components:
            shared["TRANSFER"] = [c for c in priced if c.category == "TRANSFER" and c.capacity is not None and c.capacity >= n]
            if not shared["TRANSFER"]:
                shared_conflicts.append(Conflict(code="TRANSFER_UNAVAILABLE"))
        conflicts += shared_conflicts
        if conflicts:
            return SolverResult(status="INFEASIBLE", policy_version=POLICY, conflicts=tuple(conflicts))

        # Cheapest trip each traveller could have on their own, scaled by n.
        min_shared = sum(min(c.total_cents for c in options) for options in shared.values())
        alone = {o: n * min(c.total_cents for c in flights[o]) + min_shared for o in members}
        over = [o for o in members if by_owner[o].trip_cap_cents is not None and alone[o] > n * by_owner[o].trip_cap_cents]
        if over:
            cause = self._requirement_cause(over, by_owner, briefs, flights, shared, priced, n, snapshot.people_per_bedroom)
            return SolverResult(status="INFEASIBLE", policy_version=POLICY, conflicts=tuple(
                Conflict(code="TRIP_LIMIT", owner=o, cheapest_cents=-(-alone[o] // n),
                         trip_cap_cents=by_owner[o].trip_cap_cents, requirement=cause) for o in over))

        model = cp_model.CpModel()
        picks = {o: {c.id: model.new_bool_var(c.id) for c in flights[o]} for o in members}
        shared_picks = {k: {c.id: model.new_bool_var(c.id) for c in options} for k, options in shared.items()}
        by_id = {c.id: c for options in [*flights.values(), *shared.values()] for c in options}
        for choice in [*picks.values(), *shared_picks.values()]:
            model.add_exactly_one(choice.values())
        shared_cost = sum(by_id[i].total_cents * v for choice in shared_picks.values() for i, v in choice.items())
        arrival, burden = {}, {}
        for o in members:
            arrival[o] = sum(by_id[i].arrival_epoch * v for i, v in picks[o].items())
            scaled = sum(n * by_id[i].total_cents * v for i, v in picks[o].items()) + shared_cost
            burden[o] = scaled - alone[o]
            if by_owner[o].trip_cap_cents is not None:
                model.add(scaled <= n * by_owner[o].trip_cap_cents)
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                model.add(arrival[a] - arrival[b] <= window)
                model.add(arrival[b] - arrival[a] <= window)
        most = max(n * max(c.total_cents for c in flights[o]) + sum(max(c.total_cents for c in s) for s in shared.values()) - alone[o]
                   for o in members)
        worst = model.new_int_var(0, max(most, 0), "worst_burden")
        for o in members:
            model.add(worst >= burden[o])

        model.minimize(worst)
        status, solver = self._run(model)
        if status == "INFEASIBLE":
            spread = closest_spread(flights)
            if spread is not None and spread > window:
                conflict = Conflict(code="ARRIVAL_WINDOW", closest_spread_seconds=spread, required_seconds=window)
            else:
                # Each limit alone can be met, but not together with the arrival window.
                conflict = Conflict(code="TRIP_LIMIT", closest_spread_seconds=spread, required_seconds=window)
            return SolverResult(status="INFEASIBLE", policy_version=POLICY, conflicts=(conflict,))
        if status not in {"OPTIMAL", "FEASIBLE"}:
            return SolverResult(status=status, policy_version=POLICY)
        model.add(worst <= solver.value(worst))
        model.minimize(sum(by_id[i].total_cents * v for choice in [*picks.values(), *shared_picks.values()] for i, v in choice.items()))
        second, solver = self._run(model)
        if second not in {"OPTIMAL", "FEASIBLE"}:
            return SolverResult(status=second, policy_version=POLICY)
        # A best-found (not proven optimal) stage makes the whole result FEASIBLE.
        overall = "OPTIMAL" if status == second == "OPTIMAL" else "FEASIBLE"

        def chosen(choice):
            return by_id[next(i for i, v in choice.items() if solver.value(v))]
        flight_of = {o: chosen(picks[o]) for o in members}
        shared_of = {k: chosen(choice) for k, choice in shared_picks.items()}
        shared_total = sum(c.total_cents for c in shared_of.values())
        impacts = tuple(self._impact(o, n, flight_of, shared_total, alone, flights, shared, window) for o in members)
        return SolverResult(status=overall, policy_version=POLICY,
            selected=tuple(flight_of[o].id for o in members) + tuple(shared_of[k].id for k in ("STAY", "TRANSFER") if k in shared_of),
            impacts=impacts, group=self._summary(n, flight_of, shared_of, alone, flights, shared, impacts, window, briefs, priced))

    @staticmethod
    def _requirement_cause(over, by_owner, briefs, flights, shared, priced, n, per_room):
        """Would dropping one room requirement bring every over-limit trip back
        within its limit? That requirement, not the budget alone, is the clash."""
        if "STAY" not in shared:
            return None
        others = sum(min(c.total_cents for c in options) for k, options in shared.items() if k != "STAY")
        for requirement in ("BEDROOMS", "RATING"):
            stays = [c for c in priced if c.category == "STAY" and all(ok(c) for _, ok in stay_filters(briefs, (requirement,), per_room))]
            if stays and min(c.total_cents for c in stays) < min(c.total_cents for c in shared["STAY"]):
                cheapest = min(c.total_cents for c in stays) + others
                if all(n * min(c.total_cents for c in flights[o]) + cheapest <= n * by_owner[o].trip_cap_cents for o in over):
                    return requirement
        return None

    def _run(self, model):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit
        solver.parameters.num_search_workers = 1
        return STATUS[solver.solve(model)], solver

    @staticmethod
    def owner_conflict(owner, brief, candidates):
        priced = [c for c in candidates if c.category == "FLIGHT" and c.owner == owner and c.total_cents is not None]
        if brief.baggage_required:
            with_bag = [c for c in priced if c.baggage_included is not False]
            if priced and not with_bag:
                return Conflict(code="BAGGAGE_UNAVAILABLE", owner=owner)
            priced = with_bag
        # The owner's own timing limits; the cheapest fare that breaks them says what relaxing would cost.
        on_time = [c for c in priced if lands_in_window(c, brief.arrival_window)]
        if priced and not on_time:
            return Conflict(code="ARRIVAL_TIME", owner=owner, requirement="ARRIVAL_TIME",
                            cheapest_cents=min(c.total_cents for c in priced))
        direct = [c for c in on_time if stops_ok(c, brief.max_stops)]
        if on_time and not direct:
            return Conflict(code="TOO_MANY_STOPS", owner=owner, requirement="STOPS",
                            cheapest_cents=min(c.total_cents for c in on_time))
        priced = direct
        if not priced:
            return Conflict(code="NO_CANDIDATES", owner=owner)
        return Conflict(code="HARD_LIMIT", owner=owner, cheapest_cents=min(c.total_cents for c in priced))

    @staticmethod
    def _impact(owner, n, flight_of, shared_total, alone, flights, shared, window):
        pick = flight_of[owner]
        scaled_extra = n * pick.total_cents + shared_total - alone[owner]
        share = round(shared_total / n)
        extra = round(scaled_extra / n)
        reason = "NONE"
        if extra > 0:
            cheapest_fare = min(c.total_cents for c in flights[owner])
            # Would their own cheapest fare have broken the shared arrival rule?
            others = [c.arrival_epoch for o, c in flight_of.items() if o != owner]
            cheapest = [c for c in flights[owner] if c.total_cents == cheapest_fare]
            if pick.total_cents > cheapest_fare and all(
                    max(others + [c.arrival_epoch]) - min(others + [c.arrival_epoch]) > window for c in cheapest):
                reason = "ARRIVAL_WINDOW"
            elif pick.total_cents == cheapest_fare:
                reason = "SHARED_CHOICE"
        return Impact(owner=owner, candidate=pick.id, price_cents=pick.total_cents + share,
                      cheapest_alone_cents=pick.total_cents + share - extra, extra_cents=extra, reason=reason,
                      flight_cents=pick.total_cents, share_cents=share)

    @staticmethod
    def _summary(n, flight_of, shared_of, alone, flights, shared, impacts, window, briefs, priced):
        arrivals = [c.arrival_epoch for c in flight_of.values()]
        total = sum(c.total_cents for c in [*flight_of.values(), *shared_of.values()])
        cheapest_total = sum(min(c.total_cents for c in options) for options in [*flights.values(), *shared.values()])
        cheapest_spread = None
        if cheapest_total < total:
            cheapest = {o: [c for c in flights[o] if c.total_cents == min(x.total_cents for x in flights[o])] for o in flights}
            cheapest_spread = closest_spread(cheapest)
        stay, transfer = shared_of.get("STAY"), shared_of.get("TRANSFER")
        premium = 0
        if stay is not None:
            # Versus the cheapest approvable stay that merely sleeps everyone.
            sleeps = [c.total_cents for c in priced if c.category == "STAY" and c.complete and stay_filters(briefs)[0][1](c)]
            premium = max(0, stay.total_cents - min(sleeps, default=stay.total_cents))
        extras = [i.extra_cents for i in impacts]
        return GroupSummary(total_cents=total, earliest_arrival=min(arrivals), latest_arrival=max(arrivals),
            arrival_window_seconds=window, travellers_paying_extra=sum(1 for e in extras if e > 0),
            max_extra_cents=max(extras), cheapest_total_cents=cheapest_total if cheapest_total < total else None,
            cheapest_spread_seconds=cheapest_spread,
            stay=stay.id if stay else None, stay_total_cents=stay.total_cents if stay else None,
            transfer=transfer.id if transfer else None, transfer_total_cents=transfer.total_cents if transfer else None,
            share_each_cents=round(sum(c.total_cents for c in shared_of.values()) / n), stay_premium_cents=premium)


class TripValidator:
    def validate(self, snapshot, briefs, candidates, selected, now):
        checks = []
        by_id = {c.id: c for c in candidates}
        records = [by_id[r] for r in selected if r in by_id]
        flights = [c for c in records if c.category == "FLIGHT"]
        shared = {c.category: c for c in records if c.category != "FLIGHT"}
        n = len(snapshot.members)
        required = {k for k in snapshot.components if k != "FLIGHT"}
        if (len(set(selected)) != len(selected) or len(records) != len(selected) or len(flights) != n
                or {c.owner for c in flights} != set(snapshot.members)
                or set(shared) != required or len(shared) != len(records) - len(flights)):
            checks.append("INVALID_COVERAGE")
        by_owner = {b.owner: b for b in briefs}
        for candidate in records:
            if candidate.observed_at > now or candidate.valid_until <= now:
                checks.append("EVIDENCE_EXPIRED")
            if not candidate.complete:
                checks.append("INCOMPLETE_COST_EVIDENCE")
        for candidate in flights:
            brief = by_owner[candidate.owner]
            if candidate.total_cents is None or candidate.total_cents > brief.hard_cap_cents:
                checks.append("HARD_COST")
            if not lands_in_window(candidate, brief.arrival_window):
                checks.append("ARRIVAL_TIME")
            if not stops_ok(candidate, brief.max_stops):
                checks.append("TOO_MANY_STOPS")
            if brief.baggage_required and candidate.baggage_included is not True:
                checks.append("BAGGAGE_UNKNOWN" if candidate.baggage_included is None else "BAGGAGE_REQUIRED")
        if "STAY" in shared:
            if not all(ok(shared["STAY"]) for _, ok in stay_filters(briefs, per_room=snapshot.people_per_bedroom)):
                checks.append("STAY_REQUIREMENTS")
        if "TRANSFER" in shared and (shared["TRANSFER"].capacity or 0) < n:
            checks.append("TRANSFER_CAPACITY")
        shared_total = sum(c.total_cents or 0 for c in shared.values())
        for candidate in flights:
            cap = by_owner[candidate.owner].trip_cap_cents
            if cap is not None and candidate.total_cents is not None and n * candidate.total_cents + shared_total > n * cap:
                checks.append("TRIP_COST")
        if flights and max(c.arrival_epoch for c in flights) - min(c.arrival_epoch for c in flights) > snapshot.arrival_overlap_seconds:
            checks.append("ARRIVAL_OVERLAP")
        return ValidationReport(valid=not checks, snapshot_ref=digest(snapshot), candidate_digest=digest([c.model_dump(mode="json") for c in candidates]),
            selected=selected, checks=tuple(sorted(set(checks))),
            validator_version="trip-validator-v1" if required else "flight-validator-v1")


# Flight-only rooms are trips with one component.
FlightSolver, FlightValidator = TripSolver, TripValidator


# Requirements an owner can set aside for a trip: the constraint key and the value
# that means "set aside". Answering records it as their own confirmed constraint
# (yes → set aside, no → `kept_<key>`), so it lasts the trip and is asked once.
RELAXABLE = {"BEDROOMS": ("own_bedroom", False), "RATING": ("stay_min_rating", None),
             "ARRIVAL_TIME": ("arrival_window", None), "STOPS": ("max_stops", None)}


def relax_nudge(requirement: str) -> dict:
    key, value = RELAXABLE[requirement]
    return {"yes": {"key": key, "value": value, "hardness": "HARD", "unit": ""},
            "no": {"key": f"kept_{key}", "value": True, "hardness": "HARD", "unit": ""}}
