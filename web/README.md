# Tandem approval demo

Next.js client with participant-scoped SpacetimeDB subscriptions and a live workflow
graph. The browser changes backend state and displays results; it never launches
Python, manages an execution queue, or receives provider/worker credentials.

## Start the frontend

```bash
cd web
npm ci
cp .env.example .env.local
# Set the two public database connection values.
npm run dev -- --port 3001
```

Use Node 20.19+ and the team's SpacetimeDB 2.13 module. For Maincloud, set the public
URL to `https://maincloud.spacetimedb.com` and select the team's actual database.
No worker process paths or other server-only execution settings belong in this app.
Any older `TANDEM_DEMO_*` / `TANDEM_WORKER_*` bridge settings in web/.env.local are
unused and may be removed. No fixture data is substituted by the frontend.

Use separate browser profiles/private sessions for separate people. Participant
identities are saved in database-scoped localStorage. Clearing storage loses the
anonymous participant's access; this demo has no account recovery.

## Start planning through the intended architecture

1. Create/join a room. Record the room ID. Display names must be unique and exactly
   match owner names in the operator's live JSON configuration.
2. Each person confirms participation preferences and flight constraints. The
   organizer selects shared stay/transfer components matching the config. Routes,
   dates, timezones and approved search filters stay in the operator's JSON.
3. Start the persistent worker from `worker/`:

   ```bash
   .venv/bin/tandem-worker serve --config live.json --env-file ../.env.local \
     --state-dir ./live-state/browser-demo --room <room-id> --port 8080
   ```

   Its private env file must contain the registered worker's `STDB_WORKER_TOKEN`,
   `STDB_URL`, `STDB_DB`, provider keys, exact `CLAUDE_MODEL`, and a private
   `WORKER_WAKE_TOKEN` shared with the dispatcher. Explicit config limits govern
   paid calls. Starting this service enables automatic planning for this room.
   Keep the same state directory across restarts; existing budgets and checkpoints
   are preserved. The worker requires every active person's confirmed preferences
   and flight-budget input before provider preflight.
4. Start the existing TypeScript dispatcher (`npm ci` once in `dispatcher/`), with
   `STDB_URL`, `STDB_DB`, and an allowlisted identity's `STDB_TOKEN` exported.
   Also export `WORKER_WAKE_URL=http://127.0.0.1:8080/wake` and the same private
   `WORKER_WAKE_TOKEN`, then run `npm start` in `dispatcher/`.
5. Confirmed constraints queue a job in SpacetimeDB. Its subscription notifies the
   dispatcher, which POSTs a wake hint. The worker scans the authoritative queue,
   claims a job transactionally, and runs LangGraph. A short input quiet window
   (default two seconds) coalesces sequential form writes; it grants no consent.
6. Answer private questions in My Agent. When all required answers arrive,
   SpacetimeDB makes work eligible and the same mechanism resumes planning.
   There is no Start/Continue browser launcher and no manual rerun after answers.
7. Published proposals start with zero approvals. Each person reviews and accepts
   the exact terms. Agreement updates live; approval reserves/purchases nothing.

If the dispatcher or a notification fails, the worker also scans at startup and
every five seconds. Both paths invoke the same worker and backend claims. Run one
service per state directory. Polling/settling are configurable with
`--poll-interval` and `--settle-seconds`. Bind host/port with `--host` / `--port`.
The default host is localhost; `/wake` requires a bearer secret. It is a service
endpoint, not a frontend endpoint. No endpoint call itself is authority to plan:
the room/job hint is validated and the worker re-reads SpacetimeDB.

If a worker stops on a configuration mismatch, fix the config and reconfirm the
appropriate backend inputs to create a new generation. Never delete the state
folder to reset limits. Existing one-shot `run --providers live ...` remains useful
for diagnostics; do not run it concurrently with the service on the same room.
The service uses Unix/macOS process locking and is not a serverless function.

General `INTERPRET` jobs (free-form private feedback) remain unsupported by this
worker. They are not treated as planning requests. Existing refusal/re-solving,
private questions, consent and withdrawal behavior stays unchanged.

## Graph, fonts and subscriptions

The graph reads versioned `WORKFLOW_PROGRESS` room events, current agent activity,
threaded shared utterances and proposal state. It separates earlier revisions and
current drafts, highlights actual sequential agents, and labels uncorroborated
stages “last reported.” Private-input pauses are generic. Private notes/questions
remain in My Agent. Disconnects stop highlights and retain only shared graph data.

Manrope 400/500/600/700 and IBM Plex Mono 400/500 are local Fontsource assets. Form
controls, navigation and headings use Manrope; labels/details use IBM Plex Mono.
There are no Google Fonts requests. Reduced-motion preferences are respected.

One connection owns a room-list subscription and selected-room subscriptions.
Room-bearing views filter room_id; the backend filters by identity as well.
Controls wait for synchronization, unsubscribe on room changes, and reject stale
versions server-side. Bindings match the dispatcher; regenerate both after backend
contract changes with `npm run generate` and the matching SpacetimeDB CLI.

## Verification

```bash
# In web/
npm run typecheck
npm test
npm run build
npx playwright install chromium
# In dispatcher/ (required for the end-to-end subscription test)
npm ci
# From repository root
SPACETIME_BIN=/path/to/spacetime bash scripts/web-integration.sh
```

The browser harness starts a disposable DB (3301), Next.js (3401), the persistent
Python service (3501), and the real TypeScript dispatcher. Explicit localhost-only
fake providers exercise the real graph/backend with no paid calls. Ports can be
changed with STDB_TEST_PORT, TANDEM_WEB_PORT and WORKER_TEST_PORT. The isolated
`.next-integration` directory allows tests alongside the usual dev server. Use
`TANDEM_NEXT_DIST_DIR=.next-integration npm run build` to build without touching a
running dev server's build directory. Test credentials never come from private env
files, and all test processes are stopped afterward.

Tests cover HTTP wake authentication/validation, missed-wake polling, duplicate
hints, input settling, pause/resume, private data isolation, restarts, refusal,
review and agreement. Paid live-provider verification remains an explicit opt-in
operator run with configured credentials/budgets, not a claim made by these tests.
