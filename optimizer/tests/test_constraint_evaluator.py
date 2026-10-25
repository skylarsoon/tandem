import unittest
from optimizer.constraint_evaluator import (
    evaluate_candidate,
    evaluate_candidates,
    ConstraintValidationError,
)

CONSTRAINTS = [
    {'id': 'c1', 'owner_id': 'bella', 'kind': 'hard', 'attribute': 'price_per_person', 'operator': 'lte', 'value': 100},
    {'id': 'c2', 'owner_id': 'alex', 'kind': 'soft', 'attribute': 'pool', 'operator': 'eq', 'value': True, 'importance': 2},
    {'id': 'c3', 'owner_id': 'chris', 'kind': 'soft', 'attribute': 'distance_km', 'operator': 'lte', 'value': 2, 'importance': 4, 'tolerance': {'limit': 5}},
    {'id': 'c4', 'owner_id': 'dana', 'kind': 'soft', 'attribute': 'breakfast', 'operator': 'eq', 'value': True, 'importance': 2},
]
HOTELS = [
    {'id': 'hotel_a', 'attributes': {'price_per_person': 90, 'distance_km': 4, 'pool': True, 'breakfast': False}},
    {'id': 'hotel_b', 'attributes': {'price_per_person': 110, 'distance_km': 1, 'pool': False, 'breakfast': True}},
]

class Tests(unittest.TestCase):
    def test_hotel_a(self):
        result = evaluate_candidate(HOTELS[0], CONSTRAINTS)
        self.assertEqual(result.hard_feasibility, 'FEASIBLE')
        self.assertEqual(result.violated_soft_preferences, ['c3', 'c4'])
    def test_hotel_b(self):
        result = evaluate_candidate(HOTELS[1], CONSTRAINTS)
        self.assertEqual(result.hard_feasibility, 'INFEASIBLE')
        self.assertEqual(result.violated_hard_constraints, ['c1'])
    def test_unknown_not_false(self):
        result = evaluate_candidate({'id':'h', 'attributes': {'pool': None}}, CONSTRAINTS)
        self.assertEqual(result.hard_feasibility, 'UNKNOWN')
        self.assertEqual([e.result for e in result.evaluations if e.constraint_id=='c2'], ['UNKNOWN'])
    def test_invalid_weight(self):
        bad = dict(CONSTRAINTS[1], importance=6)
        with self.assertRaises(ConstraintValidationError):
            evaluate_candidate(HOTELS[0], [bad])
    def test_type_mismatch(self):
        badhotel = {'id':'h', 'attributes': {'price_per_person':'90'}}
        with self.assertRaises(ConstraintValidationError):
            evaluate_candidate(badhotel, [CONSTRAINTS[0]])
    def test_all(self):
        self.assertEqual(len(evaluate_candidates(HOTELS, CONSTRAINTS)),2)

    #test method
    def test_all_soft_violated_still_feasible(self):
        candidate = {
            "id": "hotel_test",
            "attributes": {
                "price_per_person": 90,
                "pool": False,
                "breakfast": False
            }
        }

        constraints = [
            {
                "id": "hard_budget",
                "owner_id": "bella",
                "kind": "hard",
                "attribute": "price_per_person",
                "operator": "lte",
                "value": 100
            },
            {
                "id": "soft_pool",
                "owner_id": "alex",
                "kind": "soft",
                "attribute": "pool",
                "operator": "eq",
                "value": True,
                "importance": 3
            },
            {
                "id": "soft_breakfast",
                "owner_id": "dana",
                "kind": "soft",
                "attribute": "breakfast",
                "operator": "eq",
                "value": True,
                "importance": 2
            }
        ]

        result = evaluate_candidate(candidate, constraints)

        self.assertEqual(result.hard_feasibility, "FEASIBLE")
        self.assertEqual(
            set(result.violated_soft_preferences),
            {"soft_pool", "soft_breakfast"}
    )


if __name__ == '__main__': unittest.main()
