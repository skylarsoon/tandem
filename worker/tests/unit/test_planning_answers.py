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
