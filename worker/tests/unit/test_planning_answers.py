from tandem_worker.adapters.spacetime import planning_facts


def test_answers_use_latest_attempt_and_only_allowlisted_public_search_fields():
    events=[{'id':'1','at':10,'type':'RESEARCH_ACTIVITY','payload':{'job_ref':'old','category':'FLIGHT','search_ref':'old','phase':'COMPLETED','result_count':9}},
            {'id':'2','at':20,'type':'RESEARCH_ACTIVITY','payload':{'job_ref':'new','category':'STAY','search_ref':'hotel','phase':'RUNNING',
              'search_context':{'locations':['San Diego'],'secret':'NEVER_DISCLOSE','hard_cap':12345}}}]
    facts=planning_facts(events,50)
    assert len(facts)==1
    text=' '.join(facts.values())
    assert 'STAY' in text and '30 seconds ago' in text and 'last reported' in text
    assert 'NEVER_DISCLOSE' not in text and '12345' not in text and '9 usable' not in text


def test_no_events_does_not_invent_search_activity():
    assert 'No execution updates' in planning_facts([],10)['status']


def test_fresh_queued_retry_does_not_report_previous_failure_as_current():
    events=[{'id':'1','at':10,'type':'WORKFLOW_PROGRESS','payload':{'job_ref':'8','stage':'STOPPED','phase':'FAILED'}}]
    facts=planning_facts(events,100,{'id':'9','status':'PENDING'})
    assert 'queued' in facts['planning_job']
    assert 'FAILED' not in ' '.join(facts.values())
    assert 'workflow' not in facts


def test_current_claimed_job_uses_only_its_own_observations():
    events=[{'id':'1','at':10,'type':'WORKFLOW_PROGRESS','payload':{'job_ref':'8','stage':'STOPPED','phase':'FAILED'}},
            {'id':'2','at':100,'type':'WORKFLOW_PROGRESS','payload':{'job_ref':'9','stage':'RESEARCH','phase':'RUNNING'}}]
    facts=planning_facts(events,225,{'id':'9','status':'CLAIMED'})
    assert 'claimed by the worker' in facts['planning_job']
    assert 'RESEARCH / RUNNING, 2 minutes ago' in facts['workflow']
    assert 'FAILED' not in ' '.join(facts.values())


def test_private_constraints_have_readable_units_and_no_private_rationale():
    from tandem_worker.adapters.spacetime import own_fact
    text=own_fact({'key':'trip_budget_total','value':65000,'unit':'USD_cents','hardness':'HARD','private_rationale':'PRIVATE_CANARY'})
    assert '$650.00' in text and 'whole-trip maximum' in text
    assert '65000' not in text and 'PRIVATE_CANARY' not in text
