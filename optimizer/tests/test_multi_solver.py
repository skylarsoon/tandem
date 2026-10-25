"""Run at repository root: python -m unittest optimizer.tests.test_multi_solver -v"""
import copy
import unittest
from optimizer.multi_solver import optimize_multi
from optimizer.solver import ObjectiveWeights
from optimizer.constraint_evaluator import ConstraintValidationError


def sample():
    return {
        'schema_version': '1.0', 'session_id': 'hawaii', 'task_id': 'transport',
        'task_type': 'multi_selection', 'revision': 3,
        'participants': [{'id': 'alex'}, {'id': 'bella'}],
        'candidates': [
            {'id': 'a_early', 'attributes': {'price_cents': 10000, 'arrival_epoch': 1000}},
            {'id': 'a_late', 'attributes': {'price_cents': 11000, 'arrival_epoch': 5000}},
            {'id': 'b_early', 'attributes': {'price_cents': 10000, 'arrival_epoch': 1100}},
            {'id': 'b_late', 'attributes': {'price_cents': 12000, 'arrival_epoch': 5100}},
        ],
        'selection_groups': [
            {'id': 'alex', 'candidate_ids': ['a_early', 'a_late'], 'required_count': 1},
            {'id': 'bella', 'candidate_ids': ['b_early', 'b_late'], 'required_count': 1},
        ],
        'aggregate_rules': [
            {'attribute': 'arrival_spread_seconds', 'operation': 'spread',
             'source_attribute': 'arrival_epoch', 'selection_group_ids': ['alex', 'bella']}
        ],
        'constraints': [
            {'id': 'together', 'kind': 'hard', 'attribute': 'arrival_spread_seconds',
             'operator': 'lte', 'value': 200},
        ],
        'compromise_history': {'alex': 0.0, 'bella': 0.0},
        'locked_candidate_ids': [], 'max_results': 3,
    }


class MultiSelectionTests(unittest.TestCase):
    def test_compatible_combinations_only(self):
        r = optimize_multi(sample())
        self.assertEqual(r['status'], 'FEASIBLE')
        self.assertEqual(r['combinations_examined'], 4)
        self.assertEqual(len(r['proposals']), 2)
        self.assertEqual(r['proposals'][0]['candidate_ids'], ['a_early', 'b_early'])
        self.assertEqual(r['proposals'][1]['candidate_ids'], ['a_late', 'b_late'])

    def test_uses_cost_when_fairness_ties(self):
        r = optimize_multi(sample())
        self.assertLess(r['proposals'][0]['scores']['total_cost'], r['proposals'][1]['scores']['total_cost'])

    def test_lock_enforced(self):
        q = sample()
        q['locked_candidate_ids'] = ['a_late']
        r = optimize_multi(q)
        self.assertEqual(len(r['proposals']), 1)
        self.assertEqual(r['proposals'][0]['candidate_ids'], ['a_late', 'b_late'])

    def test_infeasible_cross_candidate_constraint(self):
        q = sample()
        q['constraints'][0]['value'] = 10
        r = optimize_multi(q)
        self.assertEqual(r['status'], 'INFEASIBLE')
        self.assertFalse(r['proposals'])

    def test_owner_scoped_soft_and_history(self):
        q = sample()
        q['constraints'] += [
            {'id': 'alex_late', 'kind': 'soft', 'owner_id': 'alex',
             'selection_group_id': 'alex', 'attribute': 'arrival_epoch',
             'operator': 'gte', 'value': 4000, 'importance': 5},
            {'id': 'bella_early', 'kind': 'soft', 'owner_id': 'bella',
             'selection_group_id': 'bella', 'attribute': 'arrival_epoch',
             'operator': 'lte', 'value': 2000, 'importance': 5},
        ]
        q['compromise_history'] = {'alex': 1.0, 'bella': 0.0}
        r = optimize_multi(q, weights=ObjectiveWeights(alpha=5, beta=3, gamma=0))
        # Historical fairness protects Alex, so his preferred late flight wins.
        self.assertEqual(r['proposals'][0]['candidate_ids'], ['a_late', 'b_late'])
        self.assertEqual(r['proposals'][0]['per_person'][0]['historical_compromise_burden'], 1.0)

    def test_missing_soft_data_excludes_incomplete_bundle(self):
        q = sample()
        q['candidates'][0]['attributes'].pop('arrival_epoch')
        # Unknown aggregate excludes a bundle, but another complete bundle remains.
        r = optimize_multi(q)
        self.assertEqual(r['status'], 'FEASIBLE')
        self.assertEqual(r['proposals'][0]['candidate_ids'], ['a_late', 'b_late'])

    def test_all_missing_hard_data_needs_data(self):
        q = sample()
        for c in q['candidates']:
            c['attributes'].pop('arrival_epoch')
        self.assertEqual(optimize_multi(q)['status'], 'NEEDS_DATA')

    def test_rejects_more_than_limit(self):
        with self.assertRaises(ConstraintValidationError):
            optimize_multi(sample(), max_combinations=3)

    def test_rejects_duplicate_membership(self):
        q = sample()
        q['selection_groups'][1]['candidate_ids'].append('a_early')
        with self.assertRaises(ConstraintValidationError):
            optimize_multi(q)

    def test_rejects_unscoped_constraint_not_aggregate(self):
        q = sample()
        q['constraints'].append({'id': 'bad', 'kind': 'hard', 'attribute': 'arrival_epoch',
                                 'operator': 'lte', 'value': 200})
        with self.assertRaises(ConstraintValidationError):
            optimize_multi(q)

    def test_does_not_mutate_request(self):
        q = sample()
        before = copy.deepcopy(q)
        optimize_multi(q)
        self.assertEqual(q, before)

    def test_count_distinct_prefers_the_same_flight(self):
        """Dana would like Alex's flight: a soft count_distinct <= 1 over their two picks,
        on a string flight id. The shared flight wins though it costs a little more."""
        q = sample()
        q['participants'] = [{'id': 'alex'}, {'id': 'dana'}]
        q['candidates'] = [
            {'id': 'a_ua', 'attributes': {'price_cents': 10000, 'flight': 'UA1158'}},
            {'id': 'd_ua', 'attributes': {'price_cents': 10500, 'flight': 'UA1158'}},
            {'id': 'd_as', 'attributes': {'price_cents': 10000, 'flight': 'AS1093'}},
        ]
        q['selection_groups'] = [
            {'id': 'alex', 'candidate_ids': ['a_ua'], 'required_count': 1},
            {'id': 'dana', 'candidate_ids': ['d_ua', 'd_as'], 'required_count': 1},
        ]
        q['aggregate_rules'] = [{'attribute': 'flights', 'operation': 'count_distinct',
                                 'source_attribute': 'flight', 'selection_group_ids': ['alex', 'dana']}]
        q['constraints'] = [{'id': 'together', 'kind': 'soft', 'owner_id': 'dana', 'attribute': 'flights',
                             'operator': 'lte', 'value': 1, 'importance': 5}]
        q['compromise_history'] = {}
        r = optimize_multi(q)
        self.assertEqual(r['status'], 'FEASIBLE')
        self.assertEqual(r['proposals'][0]['candidate_ids'], ['a_ua', 'd_ua'])


if __name__ == '__main__':
    unittest.main()
