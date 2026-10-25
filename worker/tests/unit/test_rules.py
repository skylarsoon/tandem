"""One rule language, one meaning: the Python and database (TypeScript)
evaluators give the same answers on the shared cases, agree with the
optimizer's own evaluator, and the old named limits convert as data."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from optimizer.constraint_evaluator import evaluate_candidate
from tandem_worker.contracts import Candidate
from tandem_worker.rules import Need, Rule, RuleError, SearchFact, attributes, evaluate, from_legacy, legacy

ROOT = Path(__file__).resolve().parents[3]
SHARED = json.loads((ROOT / "contracts" / "rule_cases.json").read_text())
CASES, LEGACY = SHARED["cases"], SHARED["legacy_cases"]


def python_conversion(key, row) -> dict:
    """from_legacy's output in the TypeScript twin's shape, for comparison."""
    out = {"rules": [], "needs": [], "facts": []}
    for item in from_legacy("x", key, row):
        if isinstance(item, Rule):
            out["rules"].append(item.model_dump(include={"category", "attribute", "aggregate", "operator", "value", "kind", "importance", "tolerance"},
                                                exclude_none=True))
        elif isinstance(item, Need):
            out["needs"].append({"category": item.category, "attribute": item.attribute, "needs": item.needs})
        else:
            out["facts"].append({"fact": item.fact, "value": item.value})
    return out


def verdict(case) -> str:
    try:
        return evaluate(Rule.model_validate(case["rule"]), case["facts"])
    except RuleError:
        return "INVALID_RULE"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_python_evaluator_on_the_shared_cases(case):
    assert verdict(case) == case["expect"]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node 22.18+ to run the module's TypeScript")
def test_the_database_evaluator_gives_the_same_answers():
    out = json.loads(subprocess.run(["node", str(ROOT / "scripts" / "check-rule-cases.mjs")], capture_output=True, text=True,
                                    check=True).stdout)
    assert out["verdicts"] == [c["expect"] for c in CASES]
    # The old named limits convert the same way on both sides.
    for case, theirs in zip(LEGACY, out["conversions"]):
        assert theirs == python_conversion(case["key"], case["row"]), case


def test_agrees_with_the_optimizers_own_evaluator():
    """Where both have the operator and type (not times or between), same verdicts."""
    checked = 0
    for case in CASES:
        rule = case["rule"]
        if case["expect"] in ("INVALID_RULE", "NOT_APPLICABLE") or rule["operator"] == "between" or rule.get("attribute") == "arrival_local":
            continue
        theirs = evaluate_candidate(
            {"id": "c", "attributes": {k: v for k, v in case["facts"].items() if k != "category"}},
            [{"id": "r", "kind": "hard", "attribute": rule["attribute"], "operator": rule["operator"], "value": rule["value"]}],
        ).evaluations[0].result
        assert theirs == verdict(case), case["name"]
        checked += 1
    assert checked >= 15


def test_every_attribute_is_a_real_candidate_fact():
    fields = set(Candidate.model_fields)
    for category, attrs in attributes()["categories"].items():
        assert set(attrs) <= fields, f"{category}: {set(attrs) - fields} aren't Candidate fields"


def test_catalog_copies_match_the_sources():
    for name in ("attributes.json", "legacy_keys.json"):
        source = json.loads((ROOT / "contracts" / name).read_text())
        assert json.loads((ROOT / "worker" / "src" / "tandem_worker" / name).read_text()) == source, "run scripts/sync-catalogs.py"
    assert attributes()["version"] == 1 and legacy()["version"] == 1


def test_old_named_limits_convert_as_data():
    [cap] = from_legacy("ava", "flight_budget_total", {"value": 50000, "hardness": "HARD", "source_quote": "max $500"})
    assert (cap.category, cap.attribute, cap.operator, cap.value, cap.kind, cap.source_quote) == \
        ("FLIGHT", "total_cents", "lte", 50000, "hard", "max $500")
    firm, wish = from_legacy("ava", "trip_budget_total", {"value": 80000, "hardness": "SOFT", "authorized_range": {"max": 90000}})
    assert (firm.aggregate, firm.value, firm.kind) == ("my_cost", 90000, "hard"), "firm at the top of their range"
    assert (wish.value, wish.kind) == (80000, "soft")
    [window] = from_legacy("ava", "arrival_window", {"value": {"latest": "22:00"}, "hardness": "HARD"})
    assert (window.operator, window.value) == ("between", ["00:00", "22:00"])
    assert from_legacy("ava", "baggage_required", {"value": False, "hardness": "HARD"}) == [], "no bag needed isn't a rule"
    [bag] = from_legacy("ava", "baggage_required", {"value": True, "hardness": "HARD"})
    assert (bag.attribute, bag.operator, bag.value) == ("baggage_included", "eq", True)
    assert from_legacy("ava", "own_bedroom", {"value": True, "hardness": "HARD"}) == [
        Need(owner="ava", category="STAY", attribute="bedrooms", needs=1)]
    assert from_legacy("ava", "origin_airport", {"value": "SFO", "hardness": "HARD"}) == [
        SearchFact(owner="ava", fact="origin", value="SFO")]
    assert from_legacy("ava", "no_budget_limit", {"value": True}) == [] and from_legacy("ava", "kept_own_bedroom", {"value": True}) == []
    with pytest.raises(RuleError):
        from_legacy("ava", "favourite_colour", {"value": "teal"})
