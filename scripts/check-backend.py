"""Read-only backend setup check. Run with worker/.venv/bin/python."""
import json
from pathlib import Path
import shlex
import sys
import urllib.error

from dotenv import dotenv_values
from tandem_worker.adapters.spacetime import SpacetimeBackend
from tandem_worker.config import load_settings
from tandem_worker.contracts import ContractError


def main():
    env_file = Path(__file__).resolve().parents[1] / '.env.local'
    settings = load_settings(env_file)
    settings.require_backend()
    backend = SpacetimeBackend(settings.stdb_url, settings.stdb_database,
        settings.stdb_worker_token.get_secret_value())
    print(f'Backend: {settings.stdb_url}/{settings.stdb_database}', flush=True)
    # This procedure only reads. Worker authorization runs before the lookup;
    # job 0 is a deliberately absent job, so NOT_FOUND confirms registration.
    try:
        backend.http.procedure(backend.token, 'check_lease', job_id=0, attempt=0)
    except ContractError as exc:
        if exc.code == 'NOT_AUTHORIZED':
            print('Token accepted, but this identity is not registered as a worker.')
            identity = dotenv_values(env_file, interpolate=False).get('STDB_WORKER_IDENTITY')
            if identity:
                print('The database owner must run:')
                print(shlex.join(['spacetime', 'call', '--server', settings.stdb_url,
                    '--no-config', settings.stdb_database, 'add_worker',
                    json.dumps({'__identity__': '0x' + identity.removeprefix('0x')}),
                    json.dumps('skylar-worker')]))
            return 1
        if exc.code != 'NOT_FOUND':
            raise
    jobs = backend.pending_jobs()
    print(f'OK: authenticated, registered, and queue readable ({len(jobs)} pending PLAN jobs).')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ContractError, RuntimeError, urllib.error.URLError) as exc:
        print(f'Backend check failed: {exc}', file=sys.stderr)
        sys.exit(1)
