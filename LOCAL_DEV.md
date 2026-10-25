# Running the Maincloud demo locally

The browser connects directly to Maincloud. The dispatcher watches Maincloud's
job queue and wakes the Python worker. The worker runs the planning workflow
using Claude and Apify; it also polls the queue every five seconds.

## Environment files

| File | Purpose |
| --- | --- |
| `.env.local` in the repo root | Private worker AND dispatcher settings. Use this file for provider keys, database token, database URL/name, and wake secret. |
| `web/.env.local` | Public browser settings: `NEXT_PUBLIC_STDB_URL` and `NEXT_PUBLIC_STDB_DB`. |
| `worker/.env.local` | Older separate configuration; unused by the startup commands below. |
| `dispatcher/.env.example`, `worker/.env.example` | Documentation/templates, not active configuration. |

`STDB_WORKER_TOKEN` authenticates to Maincloud. Its identity must be registered
by the account that originally initialized the database. The dispatcher uses the
same token through its `STDB_WORKER_TOKEN` fallback. `WORKER_WAKE_TOKEN` is a
separate shared secret for the dispatcher's requests to the local worker.
`WORKER_WAKE_URL` is `http://127.0.0.1:8080/wake`.

The commands below load the root private file explicitly. Exported shell values
still take precedence; use fresh terminals without old STDB overrides.

## One-time database registration

From the repo root:

```bash
worker/.venv/bin/python scripts/check-backend.py
```

If registration is missing, this prints the exact `spacetime call ... add_worker`
command for the database owner to run, logged into the account that initialized
the database. Send them that command; it contains the public identity only.
Rerun the check afterward. It must report authenticated, registered, and queue
readable. This check makes no job changes and starts no provider runs.

## Start the three local processes

Run each command from the repo root, in its own terminal. Stop older worker or
dispatcher processes first with Ctrl+C. These commands assume the existing
Python virtual environment and installed Node dependencies (Node 20.19+).

Web app:

```bash
npm --prefix web run dev -- --port 3001
```

Python worker for room 15 (live execution can incur provider charges):

```bash
bash scripts/start-worker.sh 15
```

Dispatcher:

```bash
npm --prefix dispatcher run start:local
```

Open <http://localhost:3001> and select room 15. Each active participant must
confirm participation preferences and flight constraints. Their display names,
routes, dates, and trip components must match `worker/live.json`. Confirmed inputs
queue work automatically; owner answers resume it automatically.

The dispatcher should log `connected` and `watching ... pending job(s)`.
The worker should log `service: ready`; an idle worker can remain quiet. Repeated
`WORKER_SCAN_FAILED` is an error. Run the backend check to diagnose connectivity
and authorization first. `curl http://127.0.0.1:8080/health` checks local service
liveness only, not database access or provider readiness.

Keep `worker/live-state/browser-demo` across restarts to preserve checkpoints
and provider limits. Use the same room with that state directory.
