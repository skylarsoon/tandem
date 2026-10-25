import unittest
from copy import deepcopy

from optimizer.solver import optimize, ObjectiveWeights
from optimizer.constraint_evaluator import ConstraintValidationError


def example():
    return {
        'schema_version': '1.0', 'session_id': 'session_123',
        'task_id': 'task_hotel', 'task_type': 'single_selection', 'revision': 7,
        'participants': [{'id': x} for x in ('alex', 'bella', 'chris', 'dana')],
        'candidates': [
            {'id': 'hotel_a', 'attributes': {
                'price_per_person': 90, 'distance_km': 4, 'pool': True,
                'breakfast': False}},
            {'id': 'hotel_b', 'attributes': {
                'price_per_person': 110, 'distance_km': 1, 'pool': False,
                'breakfast': True}},
        ],
        'constraints': [
            {'id': 'c1', 'owner_id': 'bella', 'kind': 'hard',
             'attribute': 'price_per_person', 'operator': 'lte', 'value': 100},
            {'id': 'c2', 'owner_id': 'alex', 'kind': 'soft',
             'attribute': 'pool', 'operator': 'eq', 'value': True, 'importance': 2},
            {'id': 'c3', 'owner_id': 'chris', 'kind': 'soft',
             'attribute': 'distance_km', 'operator': 'lte', 'value': 2,
             'importance': 4, 'tolerance': {'limit': 5}},
            {'id': 'c4', 'owner_id': 'dana', 'kind': 'soft',
             'attribute': 'breakfast', 'operator': 'eq', 'value': True, 'importance': 2},
        ],
        'compromise_history': {'alex': .1, 'bella': .4, 'chris': .7, 'dana': .1},
        'locked_candidate_ids': [], 'max_results': 3,
    }


class Tests(unittest.TestCase):
    def test_hotel_a_selected(self):
        r = optimize(example())
        self.assertEqual(r['status'], 'FEASIBLE')
        self.assertEqual(r['solver_status'], 'OPTIMAL')
        self.assertEqual(r['proposals'][0]['candidate_ids'], ['hotel_a'])
        self.assertEqual(r['proposals'][0]['violated_soft_preferences'], ['c3', 'c4'])
        self.assertTrue(r['proposals'][0]['requires_approval'])

    def test_hard_constraints_not_traded(self):
        q = example()
        q['candidates'][0]['attributes']['price_per_person'] = 101
        r = optimize(q)
        self.assertEqual(r['status'], 'INFEASIBLE')
        self.assertEqual(r['proposals'], [])
        self.assertEqual(r['suggested_relaxations'], [])

    def test_unknown_hard_not_infeasible(self):
        q = example()
        q['candidates'][0]['attributes'].pop('price_per_person')
        r = optimize(q)
        self.assertEqual(r['status'], 'NEEDS_DATA')

    def test_unknown_soft_not_ranked_as_zero(self):
        q = example()
        q['candidates'][0]['attributes'].pop('pool')
        r = optimize(q)
        self.assertEqual(r['status'], 'NEEDS_DATA')
        self.assertEqual(r['proposals'], [])

    def test_history_changes_selection(self):
        q = example()
        q['constraints'] = [
            {'id': 'p', 'kind': 'soft', 'owner_id': 'alex',
             'attribute': 'pool', 'operator': 'eq', 'value': True, 'importance': 5},
            {'id': 'b', 'kind': 'soft', 'owner_id': 'bella',
             'attribute': 'breakfast', 'operator': 'eq', 'value': True, 'importance': 5},
        ]
        q['compromise_history'] = {'alex': 0, 'bella': 2, 'chris': 0, 'dana': 0}
        r = optimize(q, weights=ObjectiveWeights(alpha=10, beta=0, gamma=0))
        self.assertEqual(r['proposals'][0]['candidate_ids'], ['hotel_b'])
        self.assertEqual(q['compromise_history']['bella'], 2)

    def test_order_multiple_and_max(self):
        q = example()
        q['constraints'] = []
        q['max_results'] = 1
        r = optimize(q)
        self.assertEqual(len(r['proposals']), 1)
        self.assertEqual(r['proposals'][0]['candidate_ids'], ['hotel_a'])
        q['max_results'] = 2
        r = optimize(q)
        self.assertEqual(len(r['proposals']), 2)

    def test_locked_infeasible_requires_review(self):
        q = example()
        q['locked_candidate_ids'] = ['hotel_b']
        r = optimize(q)
        self.assertEqual(r['status'], 'NEEDS_REVIEW')

    def test_locked_feasible(self):
        q = example()
        q['locked_candidate_ids'] = ['hotel_a']
        r = optimize(q)
        self.assertEqual(r['proposals'][0]['candidate_ids'], ['hotel_a'])

    def test_missing_price_not_assumed_free(self):
        q = example()
        q['constraints'] = []
        del q['candidates'][0]['attributes']['price_per_person']
        with self.assertRaises(ConstraintValidationError):
            optimize(q)

    def test_nonpriced_task(self):
        q = example()
        q['constraints'] = []
        for c in q['candidates']:
            del c['attributes']['price_per_person']
        r = optimize(q, cost_attribute=None)
        self.assertEqual(r['status'], 'FEASIBLE')

    def test_request_unchanged(self):
        q = example()
        before = deepcopy(q)
        optimize(q)
        self.assertEqual(q, before)

    def test_bad_mode_or_weights(self):
        q = example()
        q['task_type'] = 'multi_selection'
        with self.assertRaises(ConstraintValidationError):
            optimize(q)
        with self.assertRaises(ValueError):
            ObjectiveWeights(-1, 0, 0)

    def test_cp_sat_if_installed(self):
        try:
            import ortools
        except ImportError:
            self.skipTest('OR-Tools not installed in this environment')
        result = optimize(example(), backend='cp_sat')
        self.assertEqual(result['proposals'][0]['candidate_ids'], ['hotel_a'])
        self.assertEqual(result['solver_status'], 'OPTIMAL')


if __name__ == '__main__':
    unittest.main()
