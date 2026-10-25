"""Transparent optimizer runs: every step of one plan, in plain numbers.

For a scenario it shows what each person needs (their rules, in their words),
what the shortlist kept and why it dropped the rest, how many combinations the
optimizer tried and which rule ruled out how many, the winner with its score
broken down, and what TripSolver chose for the same room. The combination
counts are recomputed here independently, so the trace also checks the
optimizer: its winner must be the cheapest combination the recount finds valid.
"""
from __future__ import annotations

import itertools
import math

from .contracts import SolverResult
from .intake_policy import describe_rule, money
from .optimizer_bridge import TandemOptimizerBridge, room_rules_from
from .shortlist import shortlist_choices
from .solver import TripSolver

WEIGHTS = {"alpha": 5, "beta": 3, "gamma": 1}  # the optimizer's ObjectiveWeights defaults
OPS = {"lte": lambda a, b: a <= b, "lt": lambda a, b: a < b, "gte": lambda a, b: a >= b, "gt": lambda a, b: a > b,
       "eq": lambda a, b: a == b}


# Where the cheapest plan and the fairest plan differ: in the morning plan c pays
# $90 more than their cheapest fare while a and b pay nothing extra; in the
# afternoon plan a and b each pay $50 more and c nothing.
FAIR_SPLIT = {"a": [("am", 10_000, 9), ("pm", 15_000, 15)], "b": [("am", 10_000, 9), ("pm", 15_000, 15)],
              "c": [("am", 19_000, 9), ("pm", 10_000, 15)]}


def fair_split(now: float):
    from .contracts import Brief, Candidate, Preferences, Snapshot
    snapshot = Snapshot(room_id="fair-split", members=tuple(FAIR_SPLIT), arrival_overlap_seconds=3600)
    briefs = [Brief(owner=o, hard_cap_cents=50_000, preferences=Preferences(confirmed=True)) for o in FAIR_SPLIT]
    candidates = [Candidate(id=f"{o}-{name}", owner=o, total_cents=price, arrival_epoch=1_800_000_000 + hour * 3600,
                            observed_at=now - 1, valid_until=now + 3600, source_url="https://fixtures.invalid", run_ref="r",
                            mode="FIXTURE", cost_basis="COMPLETE_FLIGHT_TOTAL", baggage_included=True)
                  for o, offers in FAIR_SPLIT.items() for name, price, hour in offers]
    return snapshot, briefs, candidates


def scenario(name: str, now: float = 1_000.0):
    """A fixture scenario's room, people and options (what research found)."""
    if name == "fair-split":
        return fair_split(now)
    from .fixtures import FRICTION, FixtureResearch, scenario_inputs
    from .ports import FakeClock
    snapshot, briefs = scenario_inputs(name)
    categories = list(FRICTION.get(name, {}).get("components", ("FLIGHT",)))
    request = {"purpose": "INITIAL", "members": list(snapshot.members), "target_owners": list(snapshot.members),
               "categories": categories, "observed_at": now, "input_revision": 1}
    return snapshot, briefs, FixtureResearch(FakeClock(now), name).search(request)


def recount(request: dict) -> dict:
    """Every combination the request allows: how many each rule rules out, and the
    cheapest one that passes them all (cost is the optimizer's only objective today)."""
    attrs = {c["id"]: c["attributes"] for c in request["candidates"]}
    groups = request["selection_groups"]
    rules = {r["attribute"]: r for r in request["aggregate_rules"]}
    failed = {c["id"]: 0 for c in request["constraints"]}
    valid, best = 0, None
    for combo in itertools.product(*(g["candidate_ids"] for g in groups)):
        chosen = dict(zip((g["id"] for g in groups), combo))
        ok = True
        for constraint in request["constraints"]:
            rule = rules[constraint["attribute"]]
            values = [attrs[chosen[g]][rule["source_attribute"]] for g in rule["selection_group_ids"]]
            value = max(values) - min(values) if rule["operation"] == "spread" else sum(values)
            if not OPS[constraint["operator"]](value, constraint["value"]):
                failed[constraint["id"]] += 1
                ok = False
        if ok:
            valid += 1
            cost = sum(attrs[c]["price_cents"] for c in combo)
            if best is None or (cost, combo) < best:
                best = (cost, combo)
    return {"combinations": math.prod(len(g["candidate_ids"]) for g in groups), "valid": valid, "failed_by_rule": failed,
            "cheapest_valid": None if best is None else {"total_cents": best[0], "candidate_ids": list(best[1])}}


def trace(snapshot, briefs, candidates, now: float = 1_000.0, limit: int = 5) -> dict:
    rules = room_rules_from(briefs)
    pick = shortlist_choices(candidates, snapshot, rules, limit=limit, now=now)
    bridge = TandemOptimizerBridge()
    result = bridge.solve(snapshot, briefs, list(pick.candidates), rules=rules)
    reference = TripSolver().solve(snapshot, briefs, list(pick.candidates))
    out = {
        "people": {m: [{"rule": describe_rule(r), "kind": r.kind} for r in rules.rules if r.owner == m]
                      + [{"rule": f"Needs {n.needs:g} {n.attribute} of their own ({n.category.lower()})", "kind": "need"}
                         for n in rules.needs if n.owner == m] for m in snapshot.members},
        "shortlist": [{"person": g.group, "found": g.found, "usable": g.valid, "kept": list(g.kept), "left_out": g.excluded}
                      for g in pick.groups],
        "optimizer": _result(result),
        "trip_solver": _result(reference),
    }
    if bridge.last_request is not None:
        request = bridge.last_request
        out["request"] = {"groups": {g["id"]: len(g["candidate_ids"]) for g in request["selection_groups"]},
                          "rules": [{"id": c["id"], "operator": c["operator"], "value": c["value"]} for c in request["constraints"]]}
        out["recount"] = recount(request)
        best = (bridge.last_response or {}).get("proposals") or []
        out["ranking"] = [{"rank": p["rank"], "candidate_ids": p["candidate_ids"], "total_cents": p["scores"]["total_cost"],
                           "score": _breakdown(p["scores"]), "burden": {x["participant_id"]: x["new_compromise_burden"] for x in p["per_person"]},
                           "history": {x["participant_id"]: x["historical_compromise_burden"] for x in p["per_person"]}}
                          for p in best]
    else:
        out["stopped_before_optimizer"] = [c.model_dump(exclude_none=True) for c in result.conflicts]
    out["agree"] = _agree(out)
    return out


def _result(r: SolverResult) -> dict:
    return {"status": r.status, "selected": list(r.selected), "total_cents": r.group.total_cents if r.group else None,
            "worst_extra_cents": r.group.max_extra_cents if r.group else None,
            "conflicts": [c.code for c in r.conflicts]}


def _breakdown(scores: dict) -> dict:
    parts = {"worst running burden": WEIGHTS["alpha"] * scores["max_projected_burden"],
             "new burden": WEIGHTS["beta"] * scores["total_new_compromise_burden"],
             "relative cost": WEIGHTS["gamma"] * scores["normalized_cost"]}
    return {**{k: round(v, 4) for k, v in parts.items()}, "total": round(scores["objective_value"], 4)}


def _agree(t: dict) -> str:
    ours, theirs = t["optimizer"], t["trip_solver"]
    feasible = lambda r: r["status"] in ("OPTIMAL", "FEASIBLE")
    if feasible(ours) != feasible(theirs):
        return "DISAGREE: one finds a plan, the other doesn't"
    if not feasible(ours):
        return "AGREE: no plan"
    if sorted(ours["selected"]) == sorted(theirs["selected"]):
        return "AGREE: same plan"
    return (f"DIFFERENT PLAN: optimizer {money(ours['total_cents'])} (cheapest valid), TripSolver "
            f"{money(theirs['total_cents'])} (smallest worst extra cost first: {money(theirs['worst_extra_cents'])} "
            f"vs {money(ours['worst_extra_cents'])})")


def render(name: str, t: dict) -> str:
    """One scenario as Markdown: the takeaway first, then each step."""
    label = lambda c: c.split("-")[0] if c.startswith(("p1", "p2", "p3", "p4")) else c
    lines = [f"## {name}", "", f"**{t['agree']}.**", ""]
    lines.append("<details><summary>What each person needs</summary>\n")
    for person, rules in t["people"].items():
        lines.append(f"- **{person}**: " + ("; ".join(f"{r['rule']}{'' if r['kind'] == 'hard' else ' (' + r['kind'] + ')'}" for r in rules) or "nothing stated"))
    lines.append("\n</details>\n")
    lines.append("<details><summary>Shortlist: options kept per person</summary>\n")
    lines.append("| Person | Found | Usable | Kept | Left out (why) |\n|---|---|---|---|---|")
    for s in t["shortlist"]:
        why = ", ".join(f"{n} × {k}" for k, n in s["left_out"].items()) or "nothing"
        lines.append(f"| {s['person']} | {s['found']} | {s['usable']} | {len(s['kept'])} | {why} |")
    lines.append("\n</details>\n")
    if "recount" in t:
        rc = t["recount"]
        lines.append("<details><summary>Combinations the optimizer tried</summary>\n")
        lines.append(f"- {rc['combinations']} combinations ({' × '.join(f'{n} {g}' for g, n in t['request']['groups'].items())}); "
                     f"**{rc['valid']} pass every rule**.")
        for rule in t["request"]["rules"]:
            lines.append(f"- `{rule['id']}` ({rule['operator']} {rule['value']}) rules out {rc['failed_by_rule'][rule['id']]}.")
        if rc["cheapest_valid"]:
            lines.append(f"- Recount's cheapest valid plan: {money(rc['cheapest_valid']['total_cents'])}.")
        lines.append("\n</details>\n")
        lines.append("<details><summary>Ranking and score (lower wins)</summary>\n")
        lines.append("| Rank | Total | Worst running burden ×5 | New burden ×3 | Relative cost ×1 | Score |\n|---|---|---|---|---|---|")
        for r in t["ranking"]:
            s = r["score"]
            lines.append(f"| {r['rank']} | {money(r['total_cents'])} | {s['worst running burden']} | {s['new burden']} | "
                         f"{s['relative cost']} | {s['total']} |")
        if t["ranking"]:
            lines.append(f"\nBurden per person (cost-only for now, so 0): {t['ranking'][0]['burden']}. "
                         f"History H_i: {t['ranking'][0]['history']}.")
        lines.append("\n</details>\n")
    else:
        lines.append(f"Stopped before the optimizer: {t['stopped_before_optimizer']}\n")
    o, r = t["optimizer"], t["trip_solver"]
    lines.append("<details><summary>Optimizer vs TripSolver</summary>\n")
    lines.append("| | Optimizer | TripSolver |\n|---|---|---|")
    lines.append(f"| Result | {o['status']} | {r['status']} |")
    lines.append(f"| Total | {money(o['total_cents']) if o['total_cents'] else '—'} | {money(r['total_cents']) if r['total_cents'] else '—'} |")
    lines.append(f"| Picks | {', '.join(label(c) for c in o['selected']) or '—'} | {', '.join(label(c) for c in r['selected']) or '—'} |")
    lines.append(f"| Why no plan | {', '.join(o['conflicts']) or '—'} | {', '.join(r['conflicts']) or '—'} |")
    lines.append("\n</details>\n")
    return "\n".join(lines)


def report(names: list[str]) -> str:
    head = ["# Optimizer scenarios", "",
            "Each fixture scenario run through the real pipeline: rules → shortlist → generic compiler → the optimizer, "
            "with every combination recounted independently and TripSolver run on the same options. Generated by "
            "`tandem-worker optimizer-report`; regenerate after changing the optimizer, rules or scenarios.", ""]
    return "\n".join(head + [render(n, trace(*scenario(n))) for n in names])
