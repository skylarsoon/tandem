
import unittest

from tandem_worker.contracts import Snapshot, Candidate, ContractError
from tandem_worker.optimizer_bridge import TandemOptimizerBridge


def fixture():
    members = ["1", "2", "3", "4"]

    snapshot = Snapshot(
        room_id="1",
        input_revision=5,
        membership_revision=4,
        members=tuple(members),
        arrival_overlap_seconds=7200,
    )

    candidates = []
    rows = []
    constraints = []

    for member in members:
        for index, (price, arrival) in enumerate([
            (20000, 100000),
            (30000, 100300),
        ]):
            cid = f"flight_{member}_{index}"

            candidates.append(Candidate(
                id=cid,
                category="FLIGHT",
                owner=member,
                total_cents=price,
                arrival_epoch=arrival,
                observed_at=1000,
                valid_until=2000,
                source_url="https://example.com/flight",
                run_ref="test_run",
                mode="FIXTURE",
                cost_basis="COMPLETE_FLIGHT_TOTAL",
                baggage_included=True,
            ))

            rows.append({
                "candidate_ref": cid,
                "category": "FLIGHT",
                "input_revision": 5,
                "membership_revision": 4,
                "missing_fields": [],
                "facts": {
                    "owner_member_id": int(member),
                    "total_cents": price,
                    "currency": "USD",
                    "cost_basis": "COMPLETE_FLIGHT_TOTAL",
                    "arrival_epoch": arrival,
                    "baggage_included": True,
                    "observed_at": 1000,
                    "valid_until": 2000,
                },
            })

        constraints.append({
            "constraint_id": int(member),
            "member_id": int(member),
            "key": "flight_budget_total",
            "value": 35000,
            "unit": "USD_cents",
            "hardness": "HARD",
        })

    context = {
        "ok": True,
        "job": {
            "kind": "PLAN",
            "room_id": 1,
            "input_revision": 5,
            "membership_revision": 4,
        },
        "room": {
            "room_id": 1,
            "settings": {"arrival_overlap_seconds": 7200},
        },
        "members": [
            {"member_id": int(m), "active": True}
            for m in members
        ],
        "candidates": rows,
        "constraints": constraints,
    }

    history = {m: 0.0 for m in members}

    return snapshot, candidates, context, history


class OptimizerBridgeTests(unittest.TestCase):

    def test_selects_one_flight_per_participant(self):
        snapshot, candidates, context, history = fixture()

        bridge = TandemOptimizerBridge(
            context_loader=lambda snapshot: context,
            history_loader=lambda snapshot: history,
            now=1500,
        )

        result = bridge.solve(snapshot, [], candidates)

        self.assertEqual(result.status, "OPTIMAL")
        self.assertEqual(len(result.selected), 4)
        self.assertEqual(len(set(result.selected)), 4)
        self.assertEqual(len(result.impacts), 4)
        self.assertIsNotNone(result.group)
        self.assertEqual(
            result.policy_version,
            "cumulative_fairness_v1",
        )

    def test_rejects_stale_revision(self):
        snapshot, candidates, context, history = fixture()
        context["job"]["input_revision"] = 3

        bridge = TandemOptimizerBridge(
            context_loader=lambda snapshot: context,
            history_loader=lambda snapshot: history,
        )

        with self.assertRaises(ContractError):
            bridge.solve(snapshot, [], candidates)


if __name__ == "__main__":
    unittest.main()
