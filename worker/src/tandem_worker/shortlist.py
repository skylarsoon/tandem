"""Shortlisting: the step between research and the solver.

Research can return dozens of options per traveller, and the multi-selection
optimizer tries every combination of one option per group. So each group is
cut to a few meaningfully different options first: the cheapest, one that
lands with the rest of the group, then the most distinct alternatives.

This is a heuristic, not the objective. It never invents or alters a
candidate, never silently drops a locked one, and says when a group has
nothing valid left. An infeasible shortlist proves nothing about the options
left out: the caller shortlists differently or searches again.
"""
from __future__ import annotations

from collections import Counter
from typing import Callable, Iterable

from .contracts import Candidate, GroupShortlist, Shortlist
from .solver import flight_violation

# (name, key): each slot takes the not-yet-chosen option with the smallest key;
# a key of None means the option doesn't qualify for that slot.
Slot = tuple[str, Callable[[Candidate], tuple | None]]


def dedupe(candidates: Iterable[Candidate], prefer: Iterable[str] = ()) -> list[Candidate]:
    """Repeat searches return the same offer under new observation ids; keep the
    latest, unless one of them is a preferred (locked) id."""
    prefer, latest = set(prefer), {}
    for c in candidates:
        if c.category != "FLIGHT":
            key = (c.category, c.source_url, c.total_cents)  # the listing, place or vehicle offer
        else:
            key = (c.owner, c.flight_identity, c.total_cents, c.baggage_included) if c.flight_identity else c.id
        held = latest.get(key)
        if held is None or (held.id not in prefer and (c.id in prefer or c.observed_at > held.observed_at)):
            latest[key] = c
    return sorted(latest.values(), key=lambda c: c.id)


def shortlist(candidates: Iterable[Candidate], *, include: Callable[[Candidate], bool],
              group_of: Callable[[Candidate], str | None], groups: Iterable[str],
              invalid: Callable[[Candidate], str | None], slots: list[Slot],
              distance: Callable[[Candidate, Candidate], float], limit: int, now: float,
              locked: Iterable[str] = (), tried: Iterable[str] = ()) -> Shortlist:
    """At most `limit` options per group for the candidates `include` selects;
    everything else passes through untouched. Deterministic in its inputs, in
    any order. `tried` holds ids earlier passes already showed the solver: the
    diversity places prefer options not tried yet."""
    groups, locked, tried = list(groups), set(locked), set(tried)
    pool = dedupe(sorted(candidates, key=lambda c: c.id), prefer=locked)
    present = {c.id for c in pool}
    found = Counter(group_of(c) for c in candidates if include(c))
    kept_found = Counter(group_of(c) for c in pool if include(c))
    excluded = {g: Counter() for g in groups}
    valid: dict[str, list[Candidate]] = {g: [] for g in groups}
    lock_conflicts = sorted(i for i in locked if i not in present)
    passthrough = []
    for c in pool:
        if not include(c):
            passthrough.append(c)
            continue
        group = group_of(c)
        reason = "EXPIRED" if c.valid_until <= now or c.observed_at > now else invalid(c)
        if group not in valid:
            reason = "UNKNOWN_GROUP"  # e.g. a traveller who left
        if reason:
            if group in excluded:
                excluded[group][reason] += 1
            if c.id in locked:
                lock_conflicts.append(c.id)
            continue
        valid[group].append(c)
    for g in groups:
        if found[g] > kept_found[g]:
            excluded[g]["DUPLICATE"] += found[g] - kept_found[g]

    def order(c):
        return (c.total_cents if c.total_cents is not None else float("inf"), c.id)

    def pick(options: list[Candidate]) -> list[Candidate]:
        if len(options) <= limit:
            return list(options)
        chosen = [c for c in options if c.id in locked]
        if len(chosen) > limit:
            lock_conflicts.extend(c.id for c in sorted(chosen, key=order)[limit:])
            chosen = sorted(chosen, key=order)[:limit]
        taken = {c.id for c in chosen}
        for _, key in slots:
            if len(chosen) >= limit:
                break
            ranked = sorted(((k, c.id, c) for c in options if c.id not in taken and (k := key(c)) is not None),
                            key=lambda x: (x[0], x[1]))
            if ranked:
                chosen.append(ranked[0][2])
                taken.add(ranked[0][1])
        while len(chosen) < limit:
            rest = [c for c in options if c.id not in taken]
            spread = {c.id: round(min((distance(c, x) for x in chosen), default=0.0), 9) for c in rest}
            best = min(rest, key=lambda c: (c.id in tried, -spread[c.id], *order(c)))
            chosen.append(best)
            taken.add(best.id)
        return chosen

    reports, kept = [], []
    for g in groups:
        chosen = pick(valid[g])
        ids = {c.id for c in chosen}
        kept += chosen
        reports.append(GroupShortlist(
            group=g, found=found[g], valid=len(valid[g]), kept=tuple(sorted(ids)),
            excluded=dict(sorted(excluded[g].items())),
            untried_valid=sum(1 for c in valid[g] if c.id not in tried and c.id not in ids)))
    return Shortlist(candidates=tuple(sorted(passthrough + kept, key=lambda c: c.id)), groups=tuple(reports),
                     empty_groups=tuple(g for g in groups if not valid[g]), lock_conflicts=tuple(sorted(set(lock_conflicts))))


def closest_window(options_by_owner: dict[str, list[Candidate]]) -> tuple[int, int] | None:
    """The smallest arrival window holding one option per traveller (a sweep), or
    None if someone has no option. If any combination lands within the room's
    allowed spread, the options inside this window form one."""
    if not options_by_owner or any(not o for o in options_by_owner.values()):
        return None
    events = sorted((c.arrival_epoch, owner, c.id) for owner, options in options_by_owner.items() for c in options)
    need, counts, have, best, left = len(options_by_owner), Counter(), 0, None, 0
    for arrival, owner, _ in events:
        counts[owner] += 1
        have += counts[owner] == 1
        while have == need:
            start = events[left][0]
            if best is None or arrival - start < best[1] - best[0]:
                best = (start, arrival)
            gone = events[left][1]
            counts[gone] -= 1
            have -= counts[gone] == 0
            left += 1
    return best


def shortlist_flights(candidates: Iterable[Candidate], snapshot, briefs, *, limit: int, now: float,
                      locked: Iterable[str] = (), tried: Iterable[str] = ()) -> Shortlist:
    """Each traveller's flights cut to `limit`: the cheapest, the one nearest the
    group's closest common landing window, then the most distinct by arrival time
    and price. Excluded first: duplicates, expired, malformed, and fares that
    break the owner's confirmed hard limits (exactly as the solver judges them)."""
    candidates = list(candidates)
    by_owner = {b.owner: b for b in briefs}

    def invalid(c):
        if c.total_cents is None or c.arrival_epoch is None:
            return "MALFORMED"
        return flight_violation(c, by_owner[c.owner]) if c.owner in by_owner else "UNKNOWN_GROUP"

    def usable(c):
        return c.valid_until > now and c.observed_at <= now and invalid(c) is None

    flights = {o: [c for c in candidates if c.category == "FLIGHT" and c.owner == o and usable(c)] for o in snapshot.members}
    window = closest_window(flights)
    middle = None if window is None else (window[0] + window[1]) / 2

    def anchor(c):
        if window is None or not window[0] <= c.arrival_epoch <= window[1]:
            return None
        return (abs(c.arrival_epoch - middle), c.total_cents)

    spans = {o: (max((c.arrival_epoch for c in f), default=0) - min((c.arrival_epoch for c in f), default=0),
                 max((c.total_cents for c in f), default=0) - min((c.total_cents for c in f), default=0))
             for o, f in flights.items()}

    def distance(a, b):
        arrival_span, price_span = spans.get(a.owner, (0, 0))
        return ((abs(a.arrival_epoch - b.arrival_epoch) / arrival_span if arrival_span else 0.0)
                + (abs(a.total_cents - b.total_cents) / price_span if price_span else 0.0))

    slots: list[Slot] = [("cheapest", lambda c: (c.total_cents, c.arrival_epoch)), ("group_anchor", anchor)]
    return shortlist(candidates, include=lambda c: c.category == "FLIGHT", group_of=lambda c: c.owner,
                     groups=snapshot.members, invalid=invalid, slots=slots, distance=distance, limit=limit,
                     now=now, locked=locked, tried=tried)
