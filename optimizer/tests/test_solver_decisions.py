"""Behavioral/regression tests for Tandem's optimizer decision policy.

Keep these tests separate from the solver's basic API/unit tests. They describe
which choices the optimizer should make under competing objectives, so rerun
when tuning fairness weights, adding domains, or changing cost normalization.

Run from the Tandem repository root:
    python -m unittest discover -s optimizer/tests -p 'test_*.py' -v
"""
import unittest
from copy import deepcopy

from optimizer.solver import ObjectiveWeights, optimize


def request(candidates, *, constraints=None, history=None, max_results=2, locks=None):
    """Build a schema-v1 single-selection request without domain-specific code."""
    return {
        'schema_version': '1.0',
        'session_id': 'session_behavior_tests',
        'task_id': 'choose_option',
        'task_type': 'single_selection',
        'revision': 1,
        'participants': [{'id': 'alex'}, {'id': 'bella'}],
        'candidates': deepcopy(candidates),
        'constraints': deepcopy(constraints if constraints is not None else []),
        'compromise_history': dict(history if history is not None else {'alex': 0.0, 'bella': 0.0}),
        'locked_candidate_ids': list(locks if locks is not None else []),
        'max_results': max_results,
    }


def option(name, price, *, pool=False, quiet=False, accessible=True):
    return {
        'id': name,
        'attributes': {
            'price_per_person': price,
            'pool': pool,
            'quiet': quiet,
            'accessible': accessible,
        },
    }


def pool_pref():
    return {'id': 'pool_preference', 'owner_id': 'alex', 'kind': 'soft',
            'attribute': 'pool', 'operator': 'eq', 'value': True, 'importance': 5}


def quiet_pref():
    return {'id': 'quiet_preference', 'owner_id': 'bella', 'kind': 'soft',
            'attribute': 'quiet', 'operator': 'eq', 'value': True, 'importance': 5}


def choice(result):
    return result['proposals'][0]['candidate_ids'][0]


class SolverDecisionTests(unittest.TestCase):
    def test_hard_constraint_always_outweighs_low_cost_and_preferences(self):
        q = request(
            [option('cheap_but_inaccessible', 30, pool=True, quiet=True, accessible=False),
             option('feasible', 100, pool=False, quiet=False)],
            constraints=[
                {'id': 'access', 'owner_id': 'bella', 'kind': 'hard',
                 'attribute': 'accessible', 'operator': 'eq', 'value': True},
                pool_pref(), quiet_pref(),
            ],
        )
        for backend in self.backends():
            with self.subTest(backend=backend):
                result = optimize(q, backend=backend)
                self.assertEqual(result['status'], 'FEASIBLE')
                self.assertEqual(choice(result), 'feasible')
                self.assertTrue(all('cheap_but_inaccessible' not in p['candidate_ids']
                                    for p in result['proposals']))

    def test_lower_maximum_historical_burden_wins_when_other_factors_tie(self):
        # Each option causes exactly one person to compromise, and both cost the same.
        # Alex has already compromised more in previous approved tasks.
        q = request(
            [option('burdens_alex', 100, pool=False, quiet=True),
             option('burdens_bella', 100, pool=True, quiet=False)],
            constraints=[pool_pref(), quiet_pref()],
            history={'alex': 1.0, 'bella': 0.0},
        )
        result = optimize(q, weights=ObjectiveWeights(alpha=5, beta=3, gamma=1))
        self.assertEqual(choice(result), 'burdens_bella')
        self.assertEqual(result['proposals'][0]['scores']['max_projected_burden'], 1.0)

    def test_lower_total_new_burden_wins_when_maximum_ties(self):
        # Both options have projected maximum burden 2.0, but one burdens
        # only Alex, while the other burdens Alex and Bella.
        q = request(
            [option('two_compromises', 100, pool=False, quiet=False),
             option('one_compromise', 100, pool=False, quiet=True)],
            constraints=[pool_pref(), quiet_pref()],
            history={'alex': 1.0, 'bella': 1.0},
        )
        result = optimize(q, weights=ObjectiveWeights(alpha=5, beta=3, gamma=1))
        self.assertEqual(choice(result), 'one_compromise')
        self.assertEqual(result['proposals'][0]['scores']['max_projected_burden'], 2.0)
        self.assertEqual(result['proposals'][0]['scores']['total_new_compromise_burden'], 1.0)

    def test_increasing_cost_weight_changes_winner(self):
        q = request(
            [option('cheap_but_less_preferred', 80, pool=False, quiet=False),
             option('expensive_but_preferred', 120, pool=True, quiet=True)],
            constraints=[pool_pref(), quiet_pref()],
        )
        preference_priority = optimize(q, weights=ObjectiveWeights(alpha=1, beta=1, gamma=0))
        cost_priority = optimize(q, weights=ObjectiveWeights(alpha=1, beta=1, gamma=5))
        self.assertEqual(choice(preference_priority), 'expensive_but_preferred')
        self.assertEqual(choice(cost_priority), 'cheap_but_less_preferred')
        self.assertEqual(cost_priority['proposals'][0]['scores']['normalized_cost'], 0.0)

    def test_equal_fairness_and_satisfaction_prefers_cheaper_option(self):
        q = request([option('expensive', 200), option('cheap', 80)])
        result = optimize(q)  # universal cost preference: gamma=1
        self.assertEqual(choice(result), 'cheap')

    def test_locked_choice_is_kept_and_cost_is_renormalized(self):
        # Characterizes CURRENT behavior: after a lock, only one priced
        # candidate remains; normalized cost therefore becomes 0.0.
        # If normalization moves ahead of locking, update this test deliberately.
        q = request([option('cheap', 80), option('locked', 150), option('expensive', 200)],
                    locks=['locked'])
        result = optimize(q)
        self.assertEqual(choice(result), 'locked')
        self.assertEqual(result['proposals'][0]['scores']['normalized_cost'], 0.0)
        self.assertEqual(q['candidates'][1]['attributes']['price_per_person'], 150)

    def test_direct_and_cp_sat_agree_on_unique_winner_and_ranking(self):
        try:
            import ortools  # noqa: F401
        except ImportError:
            self.skipTest('OR-Tools not installed in current Python environment')
        q = request(
            [option('least_preferred', 140, pool=False, quiet=False),
             option('best', 80, pool=True, quiet=True),
             option('middle', 110, pool=True, quiet=False)],
            constraints=[pool_pref(), quiet_pref()], max_results=3,
        )
        direct = optimize(q, backend='direct')
        cp_sat = optimize(q, backend='cp_sat')
        self.assertEqual(direct['solver_status'], 'OPTIMAL')
        self.assertEqual(cp_sat['solver_status'], 'OPTIMAL')
        self.assertEqual(
            [p['candidate_ids'][0] for p in direct['proposals']],
            [p['candidate_ids'][0] for p in cp_sat['proposals']],
        )

    @staticmethod
    def backends():
        try:
            import ortools  # noqa: F401
        except ImportError:
            return ('direct',)
        return ('direct', 'cp_sat')


if __name__ == '__main__':
    unittest.main()
