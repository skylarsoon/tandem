"""How the optimizer treats a preference whose fact a candidate doesn't report
(e.g. a flight with no stop count). Pinned because the compiler must not send
preferences in a shape that makes an option disappear or stalls planning."""
from optimizer.multi_solver import optimize_multi


def request(rows):
    return {"schema_version": "1.0", "session_id": "s", "task_id": "t", "task_type": "multi_selection", "revision": 0,
            "participants": [{"id": "a"}], "candidates": rows, "compromise_history": {}, "locked_candidate_ids": [],
            "max_results": 3, "aggregate_rules": [],
            "selection_groups": [{"id": "g", "required_count": 1, "candidate_ids": [r["id"] for r in rows]}],
            "constraints": [{"id": "nonstop", "kind": "soft", "owner_id": "a", "selection_group_id": "g",
                             "attribute": "stops", "operator": "lte", "value": 0, "importance": 3}]}


def test_an_option_with_an_unknown_preference_fact_is_dropped_from_the_ranking():
    known = {"id": "known", "attributes": {"price_cents": 500, "stops": 1}}
    unknown = {"id": "unknown", "attributes": {"price_cents": 100}}  # cheaper, but no stop count
    out = optimize_multi(request([known, unknown]), cost_attribute="price_cents")
    assert out["status"] == "FEASIBLE"
    assert [p["candidate_ids"] for p in out["proposals"]] == [["known"]]


def test_when_every_option_lacks_the_fact_nothing_is_planned():
    out = optimize_multi(request([{"id": "unknown", "attributes": {"price_cents": 100}}]), cost_attribute="price_cents")
    assert out["status"] == "NEEDS_DATA" and out["proposals"] == []
