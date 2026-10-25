"""The generic compiler: a room's decisions, rules and needs, and the candidates
found, as one multi-selection request for the optimizer.

Nothing here knows what a flight or a stay is. contracts/decisions.json says
who picks each category and who pays, and which group rules apply;
contracts/attributes.json says what candidates carry; people's rules and needs
say what each pick must satisfy. The same evaluator as the database's
publication check decides which candidates are usable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .contracts import Candidate
from .rules import RANKINGS, RELATIONS, RoomRules, Rule, attribute_type, attributes, comparable, decisions, evaluate, importance


@dataclass
class Group:
    """One choice the plan makes: a category, for one person or for everyone."""
    id: str
    category: str
    owner: str | None
    options: list[Candidate]
    excluded: dict[str, int] = field(default_factory=dict)  # why candidates were left out, by rule attribute


@dataclass
class Preference:
    """One person's preference as the optimizer weighs it: the soft constraint's id, whose it
    is, and the rule (or need) in their own words, to explain what a plan gave up."""
    id: str
    owner: str
    rule: Rule | None = None
    need: object | None = None


@dataclass
class Compiled:
    groups: list[Group]
    request: dict
    preferences: list[Preference] = field(default_factory=list)

    @property
    def empty(self) -> list[Group]:
        return [g for g in self.groups if not g.options]


def facts(candidate: Candidate) -> dict:
    return candidate.model_dump(mode="json")


def usable(candidate: Candidate, rules: list[Rule]) -> str | None:
    """Why a candidate can't be picked by these people's rules, or None. Only a
    proven violation excludes it: a missing fact stays, so an agent can ask."""
    for rule in rules:
        if evaluate(rule, facts(candidate)) == "VIOLATED":
            return rule.attribute
    return None


def required(needs, members: list[str], category: str, attribute: str, settings: dict) -> int:
    """What a shared pick must hold: explicit needs plus, for attributes a room setting
    shares, everyone else's share, rounded up (the database's rule, too). Only firm needs
    count here; a soft one is weighed as a preference."""
    explicit = [n for n in needs if n.category == category and n.attribute == attribute and n.owner in members
                and getattr(n, "kind", "hard") == "hard"]
    setting = attributes()["categories"][category][attribute].get("shared_by_setting")
    per = settings.get(setting, 2) if setting else 0
    others = len([m for m in members if not any(n.owner == m for n in explicit)])
    return math.ceil(sum(n.needs for n in explicit) + (others / per if per else 0) - 1e-9)


def _groups(category, members, candidates, room_rules, settings) -> list[Group]:
    """The choices one category makes: one per person, or one for the group (which fits
    everyone and holds what they need), each with what their rules allow."""
    shape, catalog, n = decisions()["categories"][category], attributes()["categories"], len(members)
    pool = [c for c in candidates if c.category == category and c.total_cents is not None]
    if shape["chooses"] == "one_per_person":
        return [_group(f"{category.lower()}_{i}", category, owner, [c for c in pool if c.owner == owner],
                       room_rules.binding(owner, category)) for i, owner in enumerate(members)]
    group = _group(category.lower(), category, None, pool, room_rules.binding(None, category))
    if shape.get("fits_everyone"):
        group.options = _keep(group, lambda c: (getattr(c, shape["fits_everyone"]) or 0) >= n, shape["fits_everyone"])
    for attribute, spec in catalog[category].items():
        explicit = any(x.category == category and x.attribute == attribute and x.kind == "hard" for x in room_rules.needs)
        if explicit or spec.get("shared_by_setting"):
            need = required(room_rules.needs, members, category, attribute, settings)
            group.options = _keep(group, lambda c, a=attribute, k=need: (getattr(c, a) or 0) >= k, attribute)
    return [group]


def score(rule: Rule, candidate: Candidate, options: list[Candidate]) -> float:
    """How well one option meets a preference, 0 to 1. Met is 1; missed earns partial credit up
    to its tolerance; "as low/high as possible" ranks among the options; unknown is 0, so an
    option missing the fact is still an option (the optimizer would otherwise drop it)."""
    kind = attribute_type(rule)
    value = getattr(candidate, rule.attribute, None)
    if value is None:
        return 0.0
    if rule.operator in RANKINGS:
        known = [comparable(v, kind) for o in options if (v := getattr(o, rule.attribute, None)) is not None]
        low, high, x = min(known), max(known), comparable(value, kind)
        if high == low:
            return 1.0
        return (high - x) / (high - low) if rule.operator == "lowest" else (x - low) / (high - low)
    verdict = evaluate(rule.model_copy(update={"importance": None, "tolerance": None}), facts(candidate))
    if verdict == "SATISFIED":
        return 1.0
    limit = (rule.tolerance or {}).get("limit")
    if verdict != "VIOLATED" or limit is None:
        return 0.0
    target, limit, x = comparable(rule.value, kind), comparable(limit, kind), comparable(value, kind)
    share = (limit - x) / (limit - target) if rule.operator in ("lt", "lte") else (x - limit) / (target - limit)
    return min(1.0, max(0.0, share))


def person_cost(candidate: Candidate, n: int) -> int:
    """What one person pays for a pick, scaled by n (exact): n × their own pick or a per-head
    price, or a split pick's whole price."""
    pays = decisions()["categories"][candidate.category]["pays"]
    return candidate.total_cents if pays == "split" else n * candidate.total_cents


def held_for_later(snapshot, members, candidates, room_rules, settings) -> dict[str, int]:
    """For each person, the cheapest the decisions still to come can cost them (scaled by n),
    from what was found for them; nothing is held for a decision with nothing found yet."""
    from .rules import remaining_stages
    n, held = len(members), {m: 0 for m in members}
    for category, count in remaining_stages(snapshot).items():
        for group in _groups(category, members, candidates, room_rules, settings):
            if not group.options:
                continue
            cheapest = min(person_cost(c, n) for c in group.options)
            for m in members if group.owner is None else [group.owner]:
                held[m] += count * cheapest
    return held


def compile_request(snapshot, room_rules: RoomRules, candidates: list[Candidate], history: dict[str, float],
                    settings: dict | None = None, max_results: int = 3, max_combinations: int | None = None) -> Compiled:
    members, n = list(snapshot.members), len(snapshot.members)
    # The room settings the catalogs refer to (group rule limits, how attributes are
    # shared), read from the room by name.
    named = {g["value_setting"] for g in decisions()["group_rules"]} | {
        spec["shared_by_setting"] for attrs in attributes()["categories"].values() for spec in attrs.values()
        if spec.get("shared_by_setting")}
    settings = {**{k: getattr(snapshot, k) for k in named if hasattr(snapshot, k)}, **(settings or {})}
    shapes, catalog = decisions()["categories"], attributes()["categories"]
    groups: list[Group] = []
    for category in snapshot.components:
        groups += _groups(category, members, candidates, room_rules, settings)

    if max_combinations:
        # The optimizer tries every combination. Per-person picks come shortlisted; shared picks
        # keep their cheapest options within the budget (with cost the only objective, a pricier
        # shared pick never makes a combination better: it raises everyone's share).
        personal = math.prod(len(g.options) for g in groups if g.owner is not None) or 1
        shared = [g for g in groups if g.owner is None]
        per_part = int((max_combinations // personal) ** (1 / len(shared))) if shared else 0
        for g in shared:
            keep = sorted(g.options, key=lambda c: (c.total_cents, c.id))[:max(1, per_part)]
            # A pricier shared pick can be what someone prefers: keep each preference's best too.
            for rule in room_rules.soft(None, g.category):
                best = max(g.options, key=lambda c, r=rule: (score(r, c, g.options), -c.total_cents))
                if best not in keep:
                    keep.append(best)
            g.options = keep

    rows, aggregates, constraints = [], [], []
    for g in groups:
        pays = shapes[g.category]["pays"]
        for c in g.options:
            attrs = {k: v for k, v in facts(c).items() if k in catalog[g.category] and v is not None}
            # person_cost is party-size scaled: n × your own price, the whole price for a split one,
            # n × a per-person price; so a person's sum compares exactly against n × their limit.
            attrs["price_cents"] = c.total_cents
            attrs["person_cost"] = c.total_cents if pays == "split" else n * c.total_cents
            rows.append({"id": c.id, "attributes": attrs})
    row_of = {r["id"]: r["attributes"] for r in rows}
    preferences: list[Preference] = []

    def prefer(owner, group, scores, weight, rule=None, need=None):
        """A preference as a 0..1 score on each option of `group`, owned and weighted: the
        optimizer gives partial credit for the score, so a 0.6 match counts as 0.6."""
        name = f"pref_{len(preferences)}"
        for c in group.options:
            row_of[c.id][name] = round(scores(c), 6)
        constraints.append({"id": f"soft:{owner}:{len(preferences)}", "kind": "soft", "owner_id": owner,
                            "selection_group_id": group.id, "attribute": name, "operator": "gte", "value": 1,
                            "importance": weight, "tolerance": {"limit": 0}})
        preferences.append(Preference(id=f"soft:{owner}:{len(preferences)}", owner=owner, rule=rule, need=need))

    for g in groups:
        for owner in [g.owner] if g.owner is not None else members:
            ranking = room_rules.ranking.get(owner, ())
            for rule in room_rules.soft(owner, g.category):
                if rule.aggregate is None and rule.operator not in RELATIONS:
                    prefer(owner, g, lambda c, r=rule: score(r, c, g.options),
                           rule.importance or importance(ranking, attribute=rule.attribute), rule=rule)
            # A must on a fact options rarely report (unknown_ok): it excludes options known not to
            # meet it, and of the rest, one confirmed to meet it beats one that doesn't say.
            for rule in room_rules.binding(owner, g.category):
                if rule.owner == owner and catalog[g.category].get(rule.attribute, {}).get("unknown_ok"):
                    prefer(owner, g, lambda c, r=rule: score(r, c, g.options), 5, rule=rule)
            # "Own room if possible": a shared pick that also holds this person's own share.
            for need in (x for x in room_rules.needs if x.owner == owner and x.kind == "soft" and x.category == g.category):
                with_mine = required([*room_rules.needs, need.model_copy(update={"kind": "hard"})], members, g.category,
                                     need.attribute, settings)
                prefer(owner, g, lambda c, a=need.attribute, k=with_mine: 1.0 if (getattr(c, a) or 0) >= k else 0.0,
                       importance(ranking, attribute=need.attribute), need=need)
    # "The same as someone": how many different picks the two of them make, ideally one.
    own_group = {(g.owner, g.category): g for g in groups if g.owner is not None}
    for owner in members:
        for rule in (r for r in room_rules.soft(owner) if r.operator in RELATIONS):
            mine, theirs = own_group.get((owner, rule.category)), own_group.get((rule.value, rule.category))
            if mine is None or theirs is None:
                continue  # not both choosing this now
            for c in (*mine.options, *theirs.options):
                row_of[c.id].setdefault(rule.attribute, f"unknown:{c.id}")  # unknown is never "the same"
            name = f"together_{len(preferences)}"
            aggregates.append({"attribute": name, "operation": "count_distinct", "source_attribute": rule.attribute,
                               "selection_group_ids": [mine.id, theirs.id]})
            constraints.append({"id": f"soft:{owner}:{len(preferences)}", "kind": "soft", "owner_id": owner, "attribute": name,
                                "operator": "lte", "value": 1, "importance": rule.importance
                                or importance(room_rules.ranking.get(owner, ()), relation=rule.operator)})
            preferences.append(Preference(id=f"soft:{owner}:{len(preferences)}", owner=owner, rule=rule))
    for rule in decisions()["group_rules"]:
        across = [g.id for g in groups if g.category == rule["across"]]
        if len(across) > 1:
            name = rule["id"]
            aggregates.append({"attribute": name, "operation": "spread", "source_attribute": rule["spread"], "selection_group_ids": across})
            constraints.append({"id": name, "kind": "hard", "attribute": name, "operator": rule["operator"],
                                "value": settings.get(rule["value_setting"], rule["default"])})
    reserve = held_for_later(snapshot, members, candidates, room_rules, settings)
    fairness = decisions().get("fairness")
    cheapest = {g.id: min((r["attributes"]["person_cost"] for r in rows if r["id"] in {c.id for c in g.options}), default=0)
                for g in groups}
    for i, owner in enumerate(members):
        mine = [g.id for g in groups if g.owner == owner or (g.owner is None and shapes[g.category]["pays"] != "each")]
        alone = sum(cheapest[g] for g in mine)
        if fairness and alone > 0:
            # Their compromise: what they pay over the cheapest plan open to them, as a share of
            # it (a soft limit with partial credit: 0 at their cheapest, 1 at full_at times it).
            name = f"pays_{i}"
            aggregates.append({"attribute": name, "operation": "sum", "source_attribute": "person_cost", "selection_group_ids": mine})
            constraints.append({"id": f"fair_{i}", "kind": "soft", "owner_id": owner, "attribute": name, "operator": "lte",
                                "value": alone, "importance": importance(room_rules.ranking.get(owner, ()), fairness=True),
                                "tolerance": {"limit": fairness["full_at"] * alone}})
        for j, rule in enumerate(r for r in room_rules.binding(owner) if r.aggregate == "my_cost"):
            # What's left of their budget: less what the group already agreed, and less the cheapest
            # the decisions still to come can cost them (so this one can't spend the rest).
            name = f"my_cost_{i}_{j}"
            aggregates.append({"attribute": name, "operation": "sum", "source_attribute": "person_cost", "selection_group_ids": mine})
            constraints.append({"id": name, "kind": "hard", "owner_id": owner, "attribute": name, "operator": rule.operator,
                                "value": n * rule.value - snapshot.committed_scaled.get(owner, 0) - reserve.get(owner, 0)})
        for j, rule in enumerate(r for r in room_rules.soft(owner) if r.aggregate == "my_cost" and r.operator in ("lt", "lte")):
            # A whole-trip budget they'd like to keep to: partial credit up to its tolerance.
            name, spent = f"my_cost_soft_{i}_{j}", snapshot.committed_scaled.get(owner, 0) + reserve.get(owner, 0)
            aggregates.append({"attribute": name, "operation": "sum", "source_attribute": "person_cost", "selection_group_ids": mine})
            limit = (rule.tolerance or {}).get("limit")
            constraints.append({"id": f"soft:{owner}:{len(preferences)}", "kind": "soft", "owner_id": owner, "attribute": name,
                                "operator": rule.operator, "value": n * rule.value - spent,
                                "importance": rule.importance or importance(room_rules.ranking.get(owner, ()), aggregate="my_cost"),
                                **({"tolerance": {"limit": n * limit - spent}} if limit is not None and limit > rule.value else {})})
            preferences.append(Preference(id=f"soft:{owner}:{len(preferences)}", owner=owner, rule=rule))
    request = {"schema_version": "1.0", "session_id": snapshot.room_id, "task_id": "plan", "task_type": "multi_selection",
               "revision": snapshot.input_revision, "participants": [{"id": m} for m in members],
               "candidates": rows, "constraints": constraints,
               "compromise_history": {m: float(history.get(m, 0.0)) for m in members},
               "locked_candidate_ids": [], "max_results": max_results,
               "selection_groups": [{"id": g.id, "required_count": 1, "candidate_ids": [c.id for c in g.options]} for g in groups],
               "aggregate_rules": aggregates}
    return Compiled(groups=groups, request=request, preferences=preferences)


def _group(group_id, category, owner, pool, rules) -> Group:
    group = Group(group_id, category, owner, [])
    for c in pool:
        why = usable(c, rules)
        if why:
            group.excluded[why] = group.excluded.get(why, 0) + 1
        else:
            group.options.append(c)
    return group


def _keep(group: Group, test, reason: str) -> list[Candidate]:
    kept = [c for c in group.options if test(c)]
    if len(kept) < len(group.options):
        group.excluded[reason] = group.excluded.get(reason, 0) + len(group.options) - len(kept)
    return kept
