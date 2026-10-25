import copy
import json
import unittest

from optimizer.adapter import AdapterError, adapt_execution_context


def fixture():
    return {
        'ok': True,
        'job': {'kind': 'PLAN', 'room_id': 1, 'input_revision': 5, 'membership_revision': 4},
        'room': {'room_id': 1},
        'members': [{'member_id': 1, 'active': True}, {'member_id': 2, 'active': True}],
        'constraints': [
            {'constraint_id': 10, 'member_id': 1, 'key': 'flight_budget_total',
             'value': 50000, 'unit': 'USD_cents', 'hardness': 'HARD'},
            {'constraint_id': 11, 'member_id': 2, 'key': 'flight_budget_total',
             'value': 65000, 'unit': 'USD_cents', 'hardness': 'HARD'},
        ],
        'candidates': [
            {'candidate_ref': 'alice-flight', 'category': 'FLIGHT',
             'input_revision': 5, 'membership_revision': 4,
             'missing_fields': [], 'facts': {'owner_member_id': 1, 'currency': 'USD',
                'total_cents': 48000, 'cost_basis': 'COMPLETE_FLIGHT_TOTAL',
                'baggage_included': True}},
            {'candidate_ref': 'bob-flight', 'category': 'FLIGHT',
             'input_revision': 5, 'membership_revision': 4,
             'missing_fields': [], 'facts': {'owner_member_id': 2, 'currency': 'USD',
                'total_cents': 63000, 'cost_basis': 'COMPLETE_FLIGHT_TOTAL',
                'baggage_included': True}},
        ],
    }


class AdapterTests(unittest.TestCase):
    def adapt(self, ctx=None, **kwargs):
        return adapt_execution_context(ctx or fixture(), category='FLIGHT',
             task_id='alice-flight-task', owner_member_id=1,
             compromise_history={'1': 0.4, '2': 0.2}, **kwargs)

    def test_flight_scopes_owner_and_keeps_cents_and_history(self):
        req = self.adapt().request
        self.assertEqual(req['candidates'][0]['attributes']['price_cents'], 48000)
        self.assertEqual(len(req['candidates']), 1)
        self.assertEqual(req['constraints'], [{'id': '10', 'owner_id': '1',
                         'kind': 'hard', 'attribute': 'price_cents',
                         'operator': 'lte', 'value': 50000}])
        self.assertEqual(req['compromise_history'], {'1': 0.4, '2': 0.2})
        self.assertEqual(req['revision'], 5)
        self.assertEqual(req['task_type'], 'single_selection')

    def test_facts_json_supported(self):
        ctx = fixture()
        c = ctx['candidates'][0]
        c['facts_json'] = json.dumps(c.pop('facts'))
        self.assertEqual(self.adapt(ctx).request['candidates'][0]['id'], 'alice-flight')

    def test_stale_candidate_rejected(self):
        ctx = fixture()
        ctx['candidates'][0]['input_revision'] = 4
        with self.assertRaisesRegex(AdapterError, 'stale'):
            self.adapt(ctx)

    def test_composite_trip_budget_rejected(self):
        ctx = fixture()
        ctx['constraints'].append({'constraint_id': 12, 'member_id': 1,
             'key': 'trip_budget_total', 'value': 90000, 'unit': 'USD_cents',
             'hardness': 'HARD'})
        with self.assertRaisesRegex(AdapterError, 'multi-item'):
            self.adapt(ctx)

    def test_unverified_flight_price_rejected(self):
        ctx = fixture()
        ctx['candidates'][0]['facts']['cost_basis'] = 'OBSERVED_FLIGHT_PRICE'
        with self.assertRaisesRegex(AdapterError, 'complete verified'):
            self.adapt(ctx)

    def test_unmapped_constraint_rejected(self):
        ctx = fixture()
        ctx['constraints'].append({'constraint_id': 13, 'member_id': 1,
             'key': 'own_bedroom', 'value': True, 'hardness': 'HARD'})
        with self.assertRaisesRegex(AdapterError, 'unsupported backend constraint'):
            self.adapt(ctx)

    def test_requires_complete_history(self):
        with self.assertRaisesRegex(AdapterError, 'history'):
            adapt_execution_context(fixture(), category='FLIGHT', task_id='test',
                                    owner_member_id=1, compromise_history={'1': 0.0})

    def test_private_rationale_is_not_exported(self):
        ctx = fixture()
        ctx['constraints'][0]['private_rationale'] = 'saving for rent'
        output = self.adapt(ctx).request
        self.assertNotIn('private_rationale', str(output))
        self.assertNotIn('saving for rent', str(output))

    def test_soft_requires_confirmed_importance(self):
        ctx = fixture()
        ctx['constraints'][0]['hardness'] = 'SOFT'
        with self.assertRaisesRegex(AdapterError, 'importance'):
            self.adapt(ctx)
        req = self.adapt(ctx, importance_by_constraint_id={'10': 4}).request
        self.assertEqual(req['constraints'][0]['importance'], 4)

    def test_locked_candidate_mapping(self):
        req = self.adapt(locked_candidate_refs=['alice-flight']).request
        self.assertEqual(req['locked_candidate_ids'], ['alice-flight'])

    def test_shared_stay_selection_with_generic_constraint(self):
        ctx = fixture()
        ctx['candidates'] = [{
            'candidate_ref': 'stay-a', 'category': 'STAY',
            'input_revision': 5, 'membership_revision': 4, 'missing_fields': [],
            'facts': {'total_cents': 20000, 'currency': 'USD',
                      'cost_basis': 'COMPLETE_STAY_TOTAL', 'rating': 4.4}
        }]
        ctx['constraints'].append({'constraint_id': 22, 'member_id': 2,
            'key': 'stay_min_rating', 'value': 4, 'hardness': 'SOFT'})
        adapted = adapt_execution_context(ctx, category='STAY', task_id='stay-task',
            compromise_history={'1': 0.4, '2': 0.2},
            importance_by_constraint_id={'22': 3})
        self.assertEqual(adapted.request['constraints'][0]['attribute'], 'rating')
        self.assertEqual(adapted.request['candidates'][0]['attributes']['price_cents'], 20000)


if __name__ == '__main__':
    unittest.main()
