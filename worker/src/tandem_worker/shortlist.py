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
from .rules import RoomRules, decisions, evaluate

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


def closest_window(options_by_owner: dict[str, list[Candidate]], attribute: str = "arrival_epoch") -> tuple[int, int] | None:
    """The smallest window of `attribute` holding one option per traveller (a sweep),
    or None if someone has no option. If any combination meets the group's spread
    rule, the options inside this window form one."""
    if not options_by_owner or any(not o for o in options_by_owner.values()):
        return None
    events = sorted((getattr(c, attribute), owner, c.id) for owner, options in options_by_owner.items() for c in options)
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


def shortlist_choices(candidates: Iterable[Candidate], snapshot, rules: RoomRules, *, limit: int, now: float,
                      locked: Iterable[str] = (), tried: Iterable[str] = ()) -> Shortlist:
    """Every choice each traveller makes for themselves (contracts/decisions.json:
    one_per_person) cut to `limit` options per person: the cheapest, the one nearest
    the group's closest common window for any group rule that spreads across that
    choice, then the most distinct. Excluded first: duplicates, expired, malformed,
    and options that break the owner's own rules (evaluated as the database does)."""
    candidates = list(candidates)
    shapes = decisions()["categories"]
    personal = [c for c in snapshot.components if shapes[c]["chooses"] == "one_per_person"]
    result = None
    for category in personal:
        result = _shortlist_category(candidates if result is None else list(result.candidates), category, snapshot, rules,
                                     limit=limit, now=now, locked=locked, tried=tried, earlier=result)
    if result is None:
        return Shortlist(candidates=tuple(sorted(candidates, key=lambda c: c.id)), groups=())
    return result


def _shortlist_category(candidates, category, snapshot, rules, *, limit, now, locked, tried, earlier):
    spread = next((g["spread"] for g in decisions()["group_rules"] if g["across"] == category), None)

    def invalid(c):
        if c.total_cents is None or (spread and getattr(c, spread, None) is None):
            return "MALFORMED"
        if c.owner not in snapshot.members:
            return "UNKNOWN_GROUP"
        for rule in rules.binding(c.owner, category):
            if evaluate(rule, c.model_dump(mode="json")) == "VIOLATED":
                return f"RULE:{rule.attribute}"
        return None

    def usable(c):
        return c.category == category and c.valid_until > now and c.observed_at <= now and invalid(c) is None

    options = {o: [c for c in candidates if c.owner == o and usable(c)] for o in snapshot.members}
    slots: list[Slot] = [("cheapest", lambda c: (c.total_cents, getattr(c, spread) if spread else 0))]
    # Each person's best option for each of their preferences (a nonstop, the shortest), so the
    # optimizer can weigh them; not only the cheapest survives the cut.
    from .compiler import score
    prefs = {o: [r for r in rules.soft(o, category) if r.aggregate is None] for o in snapshot.members}
    for k in range(max((len(p) for p in prefs.values()), default=0)):
        def best(c, k=k):
            mine = prefs.get(c.owner, [])
            return None if k >= len(mine) else (-score(mine[k], c, options[c.owner]), c.total_cents)
        slots.append((f"preference_{k}", best))
    if spread:
        window = closest_window(options, spread)
        middle = None if window is None else (window[0] + window[1]) / 2

        def anchor(c):
            at = getattr(c, spread)
            return None if window is None or not window[0] <= at <= window[1] else (abs(at - middle), c.total_cents)
        slots.insert(1, ("group_anchor", anchor))

    def span(values):
        values = list(values)
        return (max(values) - min(values)) if values else 0
    spans = {o: (span(getattr(c, spread) for c in f) if spread else 0, span(c.total_cents for c in f)) for o, f in options.items()}

    def distance(a, b):
        along, price = spans.get(a.owner, (0, 0))
        return ((abs(getattr(a, spread) - getattr(b, spread)) / along if spread and along else 0.0)
                + (abs(a.total_cents - b.total_cents) / price if price else 0.0))

    picked = shortlist(candidates, include=lambda c: c.category == category, group_of=lambda c: c.owner,
                       groups=snapshot.members, invalid=invalid, slots=slots, distance=distance, limit=limit,
                       now=now, locked=locked, tried=tried)
    if earlier is None:
        return picked
    return Shortlist(candidates=picked.candidates, groups=earlier.groups + picked.groups,
                     empty_groups=tuple(dict.fromkeys(earlier.empty_groups + picked.empty_groups)),
                     lock_conflicts=tuple(sorted(set(earlier.lock_conflicts + picked.lock_conflicts))))
