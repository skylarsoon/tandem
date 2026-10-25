import unittest
from optimizer.scoring import score_candidate, score_candidates, approved_history_update
from optimizer.constraint_evaluator import ConstraintValidationError

USERS = [{'id': name} for name in ('alex', 'bella', 'chris', 'dana')]
CONSTRAINTS = [
    {'id': 'c1', 'owner_id': 'bella', 'kind': 'hard', 'attribute': 'price_per_person', 'operator': 'lte', 'value': 100},
    {'id': 'c2', 'owner_id': 'alex', 'kind': 'soft', 'attribute': 'pool', 'operator': 'eq', 'value': True, 'importance': 2},
    {'id': 'c3', 'owner_id': 'chris', 'kind': 'soft', 'attribute': 'distance_km', 'operator': 'lte', 'value': 2, 'importance': 4, 'tolerance': {'limit': 5}},
    {'id': 'c4', 'owner_id': 'dana', 'kind': 'soft', 'attribute': 'breakfast', 'operator': 'eq', 'value': True, 'importance': 2},
]
HOTEL_A = {'id': 'hotel_a', 'attributes': {'price_per_person': 90, 'distance_km': 4, 'pool': True, 'breakfast': False}}
HOTEL_B = {'id': 'hotel_b', 'attributes': {'price_per_person': 110, 'distance_km': 1, 'pool': False, 'breakfast': True}}
HISTORY = {'alex': 0.1, 'bella': 0.4, 'chris': 0.7, 'dana': 0.1}

class Tests(unittest.TestCase):
    def test_example_hotel_a(self):
        score = score_candidate(HOTEL_A, CONSTRAINTS, USERS, HISTORY)
        self.assertEqual(score.hard_feasibility, 'FEASIBLE')
        prefs = {p.constraint_id: p for p in score.preferences}
        self.assertEqual(prefs['c2'].satisfaction, 1)
        self.assertAlmostEqual(prefs['c3'].satisfaction, 1/3)
        self.assertAlmostEqual(prefs['c3'].weighted_penalty, 8/3)
        self.assertEqual(prefs['c4'].satisfaction, 0)
        people = {p.participant_id: p for p in score.per_person}
        self.assertAlmostEqual(people['chris'].new_compromise_burden, 2/3)
        self.assertAlmostEqual(people['chris'].projected_total_burden, 0.7 + 2/3)
        self.assertEqual(people['dana'].new_compromise_burden, 1)
        self.assertAlmostEqual(score.max_projected_burden, 0.7 + 2/3)
        self.assertAlmostEqual(score.group_satisfaction, (1 + 1/3 + 0) / 3)

    def test_infeasible_cannot_commit(self):
        score = score_candidate(HOTEL_B, CONSTRAINTS, USERS, HISTORY)
        self.assertEqual(score.hard_feasibility, 'INFEASIBLE')
        with self.assertRaises(ValueError):
            approved_history_update(score)

    def test_missing_attribute_explicit_unknown(self):
        hotel = {'id': 'unknown', 'attributes': {'price_per_person': 90, 'pool': None, 'distance_km': 1, 'breakfast': True}}
        score = score_candidate(hotel, CONSTRAINTS, USERS)
        self.assertFalse(score.all_soft_preferences_known)
        self.assertIsNone(score.preferences[0].satisfaction)
        self.assertIsNone(score.per_person[0].satisfaction)
        self.assertEqual(score.per_person[0].coverage, 0)
        with self.assertRaises(ValueError):
            approved_history_update(score)

    def test_approved_history_is_pure(self):
        score = score_candidate(HOTEL_A, CONSTRAINTS, USERS, HISTORY)
        updated = approved_history_update(score)
        self.assertAlmostEqual(updated['chris'], 0.7 + 2/3)
        self.assertEqual(HISTORY['chris'], 0.7)

    def test_lower_bound_tolerance(self):
        c = [{'id':'x','kind':'soft','owner_id':'alex','attribute':'rating','operator':'gte','value':4.5,'importance':5,'tolerance':{'limit':3.5}}]
        hotel = {'id':'h','attributes':{'rating':4.0}}
        result = score_candidate(hotel,c,USERS)
        self.assertAlmostEqual(result.preferences[0].satisfaction, 0.5)

    def test_binary_and_no_preferences(self):
        c = [{'id':'x','kind':'soft','owner_id':'alex','attribute':'pool','operator':'eq','value':True,'importance':4}]
        hotel = {'id':'h','attributes':{'pool':False}}
        result = score_candidate(hotel,c,USERS)
        self.assertEqual(result.per_person[0].new_compromise_burden,1)
        self.assertIsNone(result.per_person[1].satisfaction)
        self.assertEqual(result.per_person[1].new_compromise_burden,0)

    def test_unregistered_preference_owner(self):
        c = [{'id':'x','kind':'soft','owner_id':'nobody','attribute':'pool','operator':'eq','value':True,'importance':4}]
        with self.assertRaises(ConstraintValidationError):
            score_candidate(HOTEL_A,c,USERS)

    def test_bad_history(self):
        with self.assertRaises(ConstraintValidationError):
            score_candidate(HOTEL_A,CONSTRAINTS,USERS,{'alex':-1})

    def test_multiple_candidates(self):
        self.assertEqual(len(score_candidates([HOTEL_A,HOTEL_B],CONSTRAINTS,USERS)),2)

if __name__ == '__main__':
    unittest.main()
