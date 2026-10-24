import copy
import unittest
from optimizer.multi_adapter import adapt_multi_execution_context
from optimizer.adapter import AdapterError
from optimizer.multi_solver import optimize_multi


def fixture():
    members = range(1, 5)
    return {
        'ok': True,
        'job': {'kind':'PLAN','room_id':1,'input_revision':5,'membership_revision':4},
        'room': {'room_id':1,'settings':{'arrival_overlap_seconds':7200}},
        'members': [{'member_id':m,'active':True} for m in members],
        'constraints': [{'constraint_id':m,'member_id':m,'key':'flight_budget_total',
                         'value':50000,'unit':'USD_cents','hardness':'HARD'} for m in members],
        'candidates': [{'candidate_ref':f'f{m}','category':'FLIGHT','input_revision':5,
                        'membership_revision':4,'missing_fields':[],
                        'facts':{'owner_member_id':m,'total_cents':40000,
                                 'currency':'USD','cost_basis':'COMPLETE_FLIGHT_TOTAL',
                                 'arrival_epoch':10000+300*m,'baggage_included':True,
                                 'observed_at':100,'valid_until':2000}} for m in members]
    }


def adapt(ctx, **kwargs):
    return adapt_multi_execution_context(ctx,task_id='flights',
          compromise_history={str(m):0.1*m for m in range(1,5)},**kwargs)


class MultiAdapterTests(unittest.TestCase):
    def test_produces_four_groups_and_fairness_history(self):
        req=adapt(fixture(),now_epoch=1000).request
        self.assertEqual(req['task_type'],'multi_selection')
        self.assertEqual(len(req['selection_groups']),4)
        self.assertEqual(req['selection_groups'][0]['candidate_ids'],['f1'])
        self.assertEqual(req['constraints'][0]['selection_group_id'],'1')
        self.assertEqual(req['compromise_history']['4'],0.4)

    def test_optimizer_accepts_request_end_to_end(self):
        req=adapt(fixture(),now_epoch=1000).request
        result=optimize_multi(req)
        self.assertEqual(result['status'],'FEASIBLE')
        self.assertEqual(result['proposals'][0]['candidate_ids'],['f1','f2','f3','f4'])

    def test_arrival_constraint_excludes_incompatible_option(self):
        ctx=fixture()
        c=copy.deepcopy(ctx['candidates'][3]);c['candidate_ref']='f4late';c['facts']['arrival_epoch']=40000
        ctx['candidates'].append(c)
        result=optimize_multi(adapt(ctx).request)
        self.assertEqual(result['status'],'FEASIBLE')
        self.assertTrue(all('f4late' not in p['candidate_ids'] for p in result['proposals']))

    def test_five_candidate_limit_rejects_six_without_discarding(self):
        ctx=fixture()
        for i in range(5):
            c=copy.deepcopy(ctx['candidates'][0]);c['candidate_ref']=f'f1_{i}'
            ctx['candidates'].append(c)
        with self.assertRaisesRegex(AdapterError,'shortlist limit'):
            adapt(ctx)

    def test_missing_owner_group_rejected(self):
        ctx=fixture();ctx['candidates'].pop()
        with self.assertRaisesRegex(AdapterError,'no flight candidates'):
            adapt(ctx)

    def test_trip_budget_fails_closed(self):
        ctx=fixture();ctx['constraints'].append({'constraint_id':9,'member_id':1,'key':'trip_budget_total',
                                    'value':60000,'unit':'USD_cents','hardness':'HARD'})
        with self.assertRaisesRegex(AdapterError,'trip_budget_total'):
            adapt(ctx)

    def test_unrecognized_hard_constraint_fails_closed(self):
        ctx=fixture();ctx['constraints'].append({'constraint_id':9,'member_id':1,'key':'unrecognized',
                                    'value':True,'hardness':'HARD'})
        with self.assertRaisesRegex(AdapterError,'unsupported'):
            adapt(ctx)

    def test_missing_importance_for_soft_fails_closed(self):
        ctx=fixture();ctx['constraints'][0]['hardness']='SOFT'
        with self.assertRaisesRegex(AdapterError,'importance'):
            adapt(ctx)
        req=adapt(ctx,importance_by_constraint_id={'1':5}).request
        self.assertEqual(req['constraints'][0]['importance'],5)

    def test_private_rationale_never_exported(self):
        ctx=fixture();ctx['constraints'][0]['private_rationale']='very confidential'
        self.assertNotIn('very confidential',str(adapt(ctx).request))

    def test_stale_revisions_rejected(self):
        ctx=fixture();ctx['candidates'][0]['input_revision']=4
        with self.assertRaisesRegex(AdapterError,'stale'):
            adapt(ctx)

    def test_expired_price_rejected(self):
        with self.assertRaisesRegex(AdapterError,'stale'):
            adapt(fixture(),now_epoch=3000)

    def test_locks_carried_forward(self):
        req=adapt(fixture(),locked_candidate_refs=['f2']).request
        self.assertEqual(req['locked_candidate_ids'],['f2'])

    def test_missing_baggage_never_passes_as_true(self):
        ctx=fixture();ctx['candidates'][0]['facts']['baggage_included']=None
        ctx['constraints'].append({'constraint_id':9,'member_id':1,'key':'baggage_required',
                                    'value':True,'hardness':'HARD'})
        result=optimize_multi(adapt(ctx).request)
        self.assertEqual(result['status'],'NEEDS_DATA')


if __name__=='__main__':
    unittest.main()
