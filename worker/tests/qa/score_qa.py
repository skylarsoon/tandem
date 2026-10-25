"""Scores a live QA run (flight_stage_qa.py) against the QA brief: PASS / FAIL per scenario
with its evidence, and Claude's extraction next to the brief's expected extraction.

    cd worker && uv run python tests/qa/score_qa.py <run dir>   # writes <run dir>/score.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PERSONAS = json.loads((HERE.parents[1] / "fixtures" / "qa" / "personas.json").read_text())
BUDGETS = {"Alex": 120000, "Bella": 250000, "Chris": 180000, "Dana": 170000}


def load(run: Path, label: str) -> dict:
    return {p.stem: json.loads(p.read_text()) for p in (run / "rows" / label).glob("*.json")}


def names(rows, room_id) -> dict:
    return {str(m["member"]).lower().removeprefix("0x"): m["display_name"] for m in rows["membership"] if m["room_id"] == room_id}


def member_names(rows, room_id) -> dict:
    return {m["id"]: m["display_name"] for m in rows["membership"] if m["room_id"] == room_id}


def who(ids, identity) -> str:
    return ids.get(str(identity).lower().removeprefix("0x"), "?")


def slot(item: dict) -> str:
    rule = item.get("rule")
    if rule:
        return f"rule:{rule.get('category')}.{rule.get('attribute') or rule.get('aggregate')}:{rule.get('operator')}"
    return item["key"]


def extraction(rows, room_id) -> dict:
    """Per persona: what the QA expects from their first message, next to what Claude proposed."""
    ids, out = names(rows, room_id), {}
    patches = [p for p in rows["constraint_patch"] if p["room_id"] == room_id]
    for t in PERSONAS["travellers"]:
        got = {}
        for p in patches:
            if who(ids, p["owner"]) != t["name"] or p["state"] not in ("CONFIRMED", "PENDING"):
                continue
            body = json.loads(p["patch_json"])
            if body.get("source_profile"):
                got.setdefault("from profile", []).append(body)
                continue
            got[slot(body if "rule" in body else {"key": body["key"]})] = body
        expected = {slot(i): i for i in t["replies"][0]["items"]}
        rows_out = []
        for s, e in expected.items():
            g = got.get(s)
            want = e.get("rule", {}).get("value") if e.get("rule") else e.get("value")
            have = None if g is None else (g["rule"].get("value") if "rule" in g else g.get("value"))
            hard_want = e.get("hardness", "HARD")
            hard_have = None if g is None else (g["rule"].get("kind", "hard").upper() if "rule" in g else g.get("hardness"))
            rows_out.append({"what": s, "quote": e["source_quote"], "expected": want, "expected_hardness": hard_want,
                             "got": have, "got_hardness": hard_have, "got_quote": None if g is None else g.get("source_quote"),
                             "match": g is not None and have == want and hard_have == hard_want})
        extra = [{"what": s, "got": g} for s, g in got.items() if s not in expected and s != "from profile"]
        out[t["name"]] = {"items": rows_out, "extra": extra, "from_profile": got.get("from profile", [])}
    return out


def proposals(rows, room_id, decision=None):
    found = [p for p in rows["proposal"] if p["room_id"] == room_id]
    decisions = {d["proposal_id"]: d for d in rows["proposal_decision"]}
    for p in found:
        p["decision_id"] = (decisions.get(p["id"]) or {}).get("decision_id")
        p["scope"] = (decisions.get(p["id"]) or {}).get("scope")
        p["terms"] = json.loads(p["terms_json"])
    return [p for p in found if decision is None or p["decision_id"] == decision]


def picks(rows, room_id, proposal) -> dict:
    members = member_names(rows, room_id)
    return {members.get(c.get("owner_member_id"), c.get("owner") or c["category"]): c for c in proposal["terms"]["selected"]}


def score(run: Path) -> dict:
    results = json.loads((run / "results.json").read_text())
    rooms = results["rooms"]
    first, after = load(run, "01-initial"), load(run, "03-after-changes")
    verdicts = {}

    # QA-01: storage as said, and a feasible flight plan keeping every hard limit (room A, first plan).
    extracted = extraction(first, rooms["A"])
    flight = [p for p in proposals(first, rooms["A"], "flight")]
    checks = {"extraction_matches": sum(r["match"] for e in extracted.values() for r in e["items"]),
              "extraction_items": sum(len(e["items"]) for e in extracted.values()), "flight_plan_published": bool(flight)}
    if flight:
        chosen = picks(first, rooms["A"], flight[-1])
        chris = chosen.get("Chris", {})
        checks.update(
            scope=flight[-1]["scope"],
            chris_leaves_on_or_after_21=chris.get("departure_date", "") >= "2026-11-21",
            chris_home_by_24=(chris.get("return_arrival_date") or "9999") <= "2026-11-24",
            chris_under_600=(chris.get("total_cents") or 10**9) <= 60000,
            nothing_after_the_trip=all((c.get("arrival_date") or "") <= "2026-11-24" for c in chosen.values()),
            picks={n: {k: c.get(k) for k in ("flight_identity", "total_cents", "stops", "departure_date", "return_arrival_date")}
                   for n, c in chosen.items()})
    hard = ("flight_plan_published", "chris_leaves_on_or_after_21", "chris_home_by_24", "chris_under_600", "nothing_after_the_trip")
    verdicts["QA-01"] = {"pass": all(checks.get(k) for k in hard) and checks["extraction_matches"] == checks["extraction_items"],
                         "checks": checks, "extraction": extracted}

    # QA-02: Alex's $900 replans what isn't agreed; agreed flights stand where they still fit (room A).
    a_props = proposals(after, rooms["A"])
    alex_budget = [c for c in after["participant_constraint"] if c["room_id"] == rooms["A"]
                   and who(names(after, rooms["A"]), c["owner"]) == "Alex" and c["key"] == "trip_budget_total"]
    agreed_flights = [p for p in a_props if p["decision_id"] == "flight" and p["state"] == "AGREED"]
    live_stay = [p for p in a_props if p["decision_id"] == "stay" and p["state"] == "AWAITING_REVIEW"]
    checks = {"alex_budget_now": [json.loads(c["value_json"]) for c in alex_budget],
              "flights_still_agreed": bool(agreed_flights), "a_live_stay_proposal": bool(live_stay),
              "no_plan_events": len([e for e in results["events"]["A"] if e["type"] == "NO_FEASIBLE_PLAN"])}
    if live_stay and agreed_flights:
        alex_flight = picks(after, rooms["A"], agreed_flights[-1]).get("Alex", {}).get("total_cents", 0)
        stay = live_stay[-1]["terms"]["selected"][0]["total_cents"]
        checks.update(alex_flight=alex_flight, stay_total=stay, alex_total=alex_flight + stay / 4,
                      within_900=alex_flight + stay / 4 <= 90000)
    verdicts["QA-02"] = {"pass": checks["alex_budget_now"] == [90000] and (checks.get("within_900") or checks["no_plan_events"] > 0),
                         "checks": checks}

    # QA-03: Chris can't leave until Nov 25: no plan, said plainly, never fabricated (room B).
    b = rooms["B"]
    chris_rules = [json.loads(r["rule_json"]) for r in after["participant_rule"] if r["room_id"] == b
                   and who(names(after, b), r["owner"]) == "Chris"]
    live_b = [p for p in proposals(after, b) if p["state"] == "AWAITING_REVIEW"]
    no_plan = [e for e in results["events"]["B"] if e["type"] == "NO_FEASIBLE_PLAN"]
    note = [m["body"] for m in results["conversations"]["B"]["Chris"] if m["body"].startswith("No plan fits yet")]
    checks = {"chris_rules": chris_rules, "live_proposals_after": len(live_b), "no_plan_events": len(no_plan),
              "room_text": [json.loads(e["safe_payload_json"]).get("text") for e in no_plan][-1:],
              "chris_note": note[-1:]}
    verdicts["QA-03"] = {"pass": any(r.get("value") == "2026-11-25" for r in chris_rules) and not live_b and bool(no_plan)
                         and not any("Chris" in (t or "") for t in checks["room_text"]), "checks": checks}

    # QA-04: Bella's nonstop is weighed, not required, and she's asked first (room C).
    c = rooms["C"]
    flight_c = [p for p in proposals(after, c, "flight")]
    bella_qs = [x for x in results["log"] if x.get("who") == "Bella" and x.get("kind") and x.get("room") == "C"]
    route = next((r for q in results["research"] if str(q.get("room_id")) == str(c) for r in q.get("routes") or ()
                  if r["owner"] == "Bella"), {})
    checks = {"bella_search_filter": route.get("max_stops"), "bella_pick": picks(after, c, flight_c[-1]).get("Bella") if flight_c else None,
              "bella_questions": bella_qs}
    verdicts["QA-04"] = {"pass": route.get("max_stops") is None and any(q["kind"] == "CONCESSION_CONSULTATION" for q in bella_qs),
                         "checks": checks}

    # QA-05: what's saved matches what's true now (room A).
    by_decision = {}
    for p in a_props:
        by_decision.setdefault(p["decision_id"], []).append(p["state"])
    stale_consents = [x for x in after["consent"] if any(x["proposal_id"] == p["id"] and p["state"] == "SUPERSEDED" for p in a_props)]
    entries = [e for e in after["compromise_entry"] if e["room_id"] == rooms["A"]]
    checks = {"states_by_decision": by_decision,
              "one_live_version_each": all(s.count("AWAITING_REVIEW") <= 1 for s in by_decision.values()),
              "superseded_approvals_stale": all(x["response"] in ("STALE", "PENDING") for x in stale_consents),
              "ledger_only_for_agreed": all(e["state"] != "ACTIVE" or any(p["id"] == e["proposal_id"] and p["state"] == "AGREED"
                                                                           for p in a_props) for e in entries)}
    verdicts["QA-05"] = {"pass": all(checks[k] for k in ("one_live_version_each", "superseded_approvals_stale", "ledger_only_for_agreed")),
                         "checks": checks}
    return {"database": results["database"], "minutes": results["minutes"], "settled": results["settled"],
            "claude_calls": results["claude_calls"], "verdicts": verdicts}


if __name__ == "__main__":
    run = Path(sys.argv[1])
    scored = score(run)
    (run / "score.json").write_text(json.dumps(scored, indent=1, default=str))
    print(json.dumps({k: v["pass"] for k, v in scored["verdicts"].items()}, indent=1))
