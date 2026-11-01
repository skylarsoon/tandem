from threading import Event
from tandem_worker.serve import JobScheduler


def test_new_user_message_is_processed_while_planning_is_blocked():
    planning, release, replied = Event(), Event(), Event()
    calls = []
    def run(job):
        calls.append(job['id'])
        if job['kind'] == 'PLAN':
            planning.set()
            assert release.wait(5)
        else:
            replied.set()
        return job
    scheduler = JobScheduler(run, parallel=2)
    try:
        plan = {'id':'plan', 'kind':'PLAN'}
        scheduler.scan([plan])
        assert planning.wait(1)
        scheduler.scan([plan, {'id':'reply', 'kind':'INTERPRET'}])
        assert replied.wait(1), 'User conversation was starved by planning'
        assert calls.count('plan') == 1
        assert not release.is_set()
    finally:
        release.set()
        scheduler.close()


def test_only_one_plan_runs_and_no_unbounded_queue_is_created():
    entered, release = Event(), Event()
    calls=[]
    def run(job):
        calls.append(job['id']); entered.set(); release.wait(5); return job
    scheduler=JobScheduler(run)
    try:
        jobs=[{'id':str(i),'kind':'PLAN'} for i in range(20)]
        scheduler.scan(jobs)
        assert entered.wait(1)
        scheduler.scan(jobs)
        assert len(scheduler.active)==1
        assert calls==['0']
    finally:
        release.set(); scheduler.close()
