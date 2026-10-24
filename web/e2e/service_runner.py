"""Explicit localhost-only service test runner; no external providers."""
import importlib.util
from pathlib import Path

from tandem_worker.adapters.spacetime import SpacetimeBackend
from tandem_worker.budget import ProviderBudget
from tandem_worker.contracts import ContractError, digest
from tandem_worker.live import bind_room, bind_session, validate_app_room
from tandem_worker.runner import Worker

spec = importlib.util.spec_from_file_location('browser_fixtures', Path(__file__).with_name('worker.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def run(config, settings, directory, *, room_id):
    bind_room(directory, room_id)
    bind_session(directory, config, settings, "run", __import__("time").time())
    bound = config.model_copy(update={'room_id': str(room_id)})
    scope = digest([bound.model_dump(mode='json'), settings.claude_model, settings.apify_actor_build])
    budget = ProviderBudget(directory / 'providers.sqlite', scope, model_calls=config.limits.model_calls, actor_runs=config.limits.actor_runs)
    class Model(fixtures.Model):
        def decide(self, context):
            budget.reserve('claude')
            return super().decide(context)
        def voice(self, context):
            budget.reserve('claude')
            return super().voice(context)
    class Research(fixtures.Research):
        def search(self, request):
            budget.reserve('apify')
            return super().search(request)
    backend = SpacetimeBackend(settings.stdb_url, settings.stdb_database, settings.stdb_worker_token.get_secret_value(),
                              room_id=room_id, owner_name=lambda m: m['display_name'])
    def before(claim):
        context = backend._context(claim)
        validate_app_room(config, context)
        backend.notify_owner(claim, 'p1', 'PRIVATE_NOTE_P1: Your private planning note.', 'browser-private-note')
    worker = Worker(backend, Model('clarify'), Research('clarify'), directory / 'checkpoints.sqlite', before_run=before)
    jobs = backend.pending_jobs()
    result = worker.run_one(str(jobs[-1]['id'])) if jobs else {'outcome': 'NO_ELIGIBLE_WORK'}
    return {'result': result}
