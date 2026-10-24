# Demo runbook: showing SpacetimeDB to judges

For: whoever drives the 3-minute demo, plus Anna (screens) and Skylar (worker).
Goal: make the SpacetimeDB track judges *see* that the database is the live coordination layer, not just storage.

---

# Talk track: explaining what we built

A 60-second version for judges, then a technical walkthrough for teammates and for judges who want depth.

## The 60-second version (judges)

*"Tandem is a group trip planner where every person has their own AI agent. The hard part is coordination: four people and four agents writing to shared state at the same time, with private budgets, and only humans allowed to approve.*

*We use SpacetimeDB as the coordination layer, not just storage. All 29 tables are private, and every client reads through per-caller views, so private data is never sent to anyone else's browser. Every write is a reducer, a transaction that enforces the rules: approvals bind to the exact terms of a plan, outdated work is rejected by revision checks, and a crashed AI worker is fenced off by leases. Browsers subscribe to those views, so every screen updates the moment a transaction commits.*

*Our Python AI worker connects through an adapter. We ran its full planning flow against the real database (agents negotiating, re-searching, asking people questions, publishing a plan, four humans approving), and it's covered by 140 automated tests."*

## Technical walkthrough

### Architecture

```
Browsers (Next.js) ── subscribe to per-caller views ──▶ ┌──────────────────────────────┐
         └────────── call reducers (human actions) ───▶ │  SpacetimeDB module (TS)      │
                                                        │  29 private tables            │
Dispatcher (Node) ── subscribes to worker_pending_jobs ─│  31 reducers · 5 procedures   │
         │ POST /wake                                   │  22 views · 2 scheduled tasks │
         ▼                                              └──────────────────────────────┘
Python worker (LangGraph) ── HTTP: procedures + reducers via SpacetimeBackend adapter ──▲
```

- **The module owns all state and all rules.** There is no separate API server.
- **The worker does the slow work** (LLM calls, flight search, CP-SAT solving) outside transactions, then writes results back through reducers that validate them.
- **The dispatcher only wakes the worker.** It subscribes to a view that's empty unless the caller is an allowlisted worker identity, and it never writes.

### Access control

- **Every table is private.** Clients can't query tables directly; they read through views, and each view is computed for `ctx.sender`, e.g. `my_constraints` returns only the caller's rows.
- **Shared views expose only shared fields.** The group chat view has no payload JSON, and approval progress is counts only (`3/4 approved`), never who is pending.
- **Two kinds of identity.** Humans are separate SpacetimeDB identities (one per browser profile in the demo). Workers and the dispatcher are service identities on an allowlist. Human-only reducers reject workers, and worker reducers reject humans.

### One planning cycle, end to end

1. **A person changes their budget.** `set_constraint` runs as one transaction: write the constraint, bump the room's `input_revision`, mark open jobs `SUPERSEDED`, mark open proposals `SUPERSEDED` with their consents `STALE`, and insert a new `PLAN` job. Everyone sees "a participant updated their requirements", never which one or why.
2. **The worker gets woken.** The new job appears in `worker_pending_jobs`; the dispatcher's subscription fires and it POSTs to the worker.
3. **The worker claims the job.** The `claim_job` procedure returns an attempt number and a 60-second lease (renewed every 15 s). Every later worker write checks four things: the caller, the attempt, an unexpired lease, and that the room's revisions still match the job's snapshot. Failures return `LEASE_LOST` or `STALE_SNAPSHOT`.
4. **It reads a consistent snapshot.** `load_execution_context` returns one transaction's view: members, confirmed settings, constraints, grants, threads, candidates. The worker is the trusted processor and scopes private data per agent.
5. **External calls go through a ledger.** `begin_operation` records each LLM call or search before it runs, and `complete_operation` stores the result. A replay returns the stored result, and per-generation budgets (100 model calls, 4 searches) persist across crashes.
6. **Agents negotiate in threads.** `submit_agent_message` takes a schema-v2 message, a typed act plus an utterance, and in one transaction checks:
   - sender and recipient are in the room and in the thread
   - a reply targets a message the sender actually received
   - cited facts exist in the room
   - 6 turns per thread and 24 per round
   - every participant has confirmed their settings
   - no owner's private reason appears in the text
   - the required permission grant exists
   - a SHA-256 of the content matches the worker's validation digest

   Duplicate submissions are no-ops.
7. **Owners are asked privately.** `ask_owner` creates a versioned question, and `park_job` releases the lease. If the answer already arrived, parking puts the job straight back in the queue. A granted permission bumps `permission_revision` and starts a new generation; a refusal resumes the same one.
8. **The plan is published.** `publish_proposal` re-validates the selected flights server-side: one per member, under hard caps, baggage where required, unexpired prices, arrivals within the room's window. It then stores immutable terms with a SHA-256 digest and creates `PENDING` consent rows, all in one transaction.
9. **People decide.** `respond_to_proposal` must send the exact version and digest, and the room's revisions must be unchanged. Agreement is recomputed in the same transaction and requires every affected member's `ACCEPTED`.

### Guarantees and how they're enforced

| Guarantee | Mechanism |
|---|---|
| Only humans approve | Human-only reducers; consent rows bound to version + terms digest |
| Nobody approves an outdated plan | Revisions on input, membership and permissions; stale consents marked `STALE` |
| A crashed worker can't corrupt state | Leases + attempt fencing; a scheduled reducer requeues expired jobs |
| Retries don't duplicate effects | Request-key receipts (attempt excluded from the digest); operation ledger |
| Private data stays private | Private tables, per-caller views, private-text check on room messages |
| Agents act within their owner's wishes | Confirmed settings gate room speech; grants bound to owner, kind and draft |
| A buggy worker can't publish a bad plan | Server-side flight validation inside `publish_proposal` |
| Agents can't argue forever | Thread and round turn budgets, charged once per message |

### Live features

- **Presence:** `clientConnected` / `clientDisconnected` hooks count open connections per identity, exposed through `room_presence`.
- **Agent activity:** set by the worker under its lease (`set_agent_activity`). The view reports `IDLE` once the job stops running, so a crash never leaves an agent "thinking". We avoided event tables here because they can't be filtered per caller.
- **Review reminders:** a scheduled reducer sends one private nudge, only to people who opted in and didn't ask for time.

### The worker adapter

`SpacetimeBackend` implements the worker's `BackendPort` over HTTP:
- It maps the worker's names ("p1", "mediator") to the module's numeric ids, its question kinds to ours, and its thread and message ids to ours.
- It handles SpacetimeDB's encoding: camelCase top-level arguments, snake_case nested fields, and `{"some": …}` options.

Integrating surfaced real mismatches between the worker's test double and the real backend. We aligned on:
- grant semantics: a yes starts a new generation
- opt-in reminders
- server-side publish validation
- a shared snapshot fingerprint
- a Python-compatible JSON encoder in the module, so both sides compute the same digest

### Verification

| Suite | Tests | Command |
|---|---|---|
| Module rules against a fresh in-memory SpacetimeDB | 63 | `spacetimedb/scripts/test.sh` |
| Worker on its own (fakes) | 60 | `cd worker && .venv/bin/python -m pytest` |
| Worker + real module: the worker's contract tests and full planning runs | 17 | `scripts/backend-integration.sh` |

### Current limits

- **AI and search are still stand-ins.** Integration runs use a scripted model and fixture flights. The Claude and Apify connectors exist but haven't been run live.
- **The module can't judge semantics.** It checks wording mechanically (cited facts, exact private-text matches); judging meaning stays in the worker.
- **Not built yet:** leaving a room, and a real auth provider.

## Likely questions and answers

**"Why put the rules in the database instead of the backend server?"**
There's no separate backend server: SpacetimeDB *is* the server. Rules that run inside the transaction can't be skipped or raced. For example, an approval and a budget change can't both "win".

**"Why is the AI outside SpacetimeDB?"**
AI calls and the solver take seconds and need Python libraries. Database transactions should be short. So the worker does the slow thinking, and the database owns the truth and the rules.

**"What if two people change things at the same time?"**
Transactions run one at a time. Each change bumps a revision, and anything computed against an older revision is rejected and redone.

**"Can the AI approve for me if I'm away?"**
No. The worker account is refused on every human action, and silence never counts as yes.

**"How do you prevent duplicate charges from AI or search calls after a crash?"**
The spending ledger records each call before it runs and stores the result after. A replay reuses the result. The one gap we can't close (a crash before the provider confirms) is documented, not hidden.

**"What would you do with more time?"**
Run with live Claude and Apify, add real login, let people leave rooms, and only reopen the parts of a plan a change actually affects.

---

## What to have on screen

Four browser windows side by side, one per person (separate browser profiles = separate identities). Each shows:
- **Live Negotiation** chat → `room_messages`
- **Agent avatars** with live state ("searching…", "writing…") → `room_agent_activity`
- **Green "online" dots** → `room_presence`
- **"2 of 4 approved"** counter → `room_consent_counts`
- **Journey milestones** → `room_milestones`
- **My Agent** (private) panel → `my_agent_messages`, `my_questions`, `my_proposal_impacts`

All of these are subscriptions: they update the instant the database commits, with no refresh or polling.

## The three moments to show

### 1. Everyone in sync (≈30 s)
A participant changes their budget. In every window at once:
- the old plan greys out ("outdated")
- the agents start working again
- the chat fills in live

**Say:** *"One transaction updated the constraint, invalidated the old plan, cancelled the old approvals, and queued new work. Every screen got that same committed state instantly."*

### 2. The database says no (≈30 s)
1. Leave one window showing the old plan.
2. Change a constraint in another window.
3. Click **Approve** in the stale window. It's rejected (`STALE_SNAPSHOT`).
4. Optionally, in a dev panel, show an agent trying to post in the room for someone who hasn't confirmed their settings: `PREFERENCES_UNCONFIRMED`.

**Say:** *"Consent rules live inside the database, so no outdated tab, no agent and no buggy client can approve on someone's behalf."*

### 3. A worker crashes and another takes over (≈30 s)
Kill the worker mid-negotiation. When its lease expires (8 s in the rehearsal, 60 s by default), the agents drop to **IDLE**, the job goes back in the queue, and another worker claims it and the chat continues. If the old worker comes back, its writes are rejected (`LEASE_LOST`). Use a short lease for the demo so judges aren't left waiting.

**Say:** *"Jobs, leases and fencing are all transactional in SpacetimeDB. The dispatcher is woken by a subscription; nothing is lost and nothing runs twice."*

Rehearse it with the simulator (below): `negotiate --crash-after 1`, then run `negotiate` again.

## Running it before the real worker exists

The simulator plays the worker with clearly labelled FIXTURE flights.

```bash
# 1. Local backend (or use the shared Maincloud database once published)
spacetime start                                   # terminal 1
spacetime publish --server local tandem-dev       # from the repo root

# 2. A worker identity
curl -s -X POST http://127.0.0.1:3000/v1/identity  # save "identity" and "token"
spacetime call --server local tandem-dev add_worker '{"__identity__":"0x<identity>"}' '"sim-worker"'

# 3. Seed a room with simulated people; join it from the app with the invite code
python3 spacetimedb/scripts/simulate.py seed --people 3

# 4. Play the worker: agents talk live, activity changes, a plan is published
STDB_WORKER_TOKEN=<token> python3 spacetimedb/scripts/simulate.py negotiate --room <room_id>

# Rehearse a crash + takeover
STDB_WORKER_TOKEN=<token> python3 spacetimedb/scripts/simulate.py negotiate --room <room_id> --crash-after 1
STDB_WORKER_TOKEN=<token> python3 spacetimedb/scripts/simulate.py negotiate --room <room_id>

# 5. Optional: the dispatcher, so the worker gets woken instantly
cd dispatcher && STDB_URL=http://127.0.0.1:3000 STDB_DB=tandem-dev STDB_TOKEN=<token> npm start
```

To show presence without a browser: `STDB_TOKEN=<a person's token> npx tsx src/tools/hold-connection.ts` (in `dispatcher/`).

To make reminders fire quickly during a demo: `spacetime call --server local tandem-dev set_reminder_delay 20`. Reminders only go to people who opted in and didn't ask for time.

To show Skylar's real worker (scripted AI, fixture flights) planning against the real database: `scripts/backend-integration.sh`.

## Pitch line

*"SpacetimeDB isn't our database, it's our coordination layer. Four people and four AI agents share one live, private-by-default state. The database itself enforces consent, replans when something changes, and keeps every screen in sync."*

## SpacetimeDB features we use (for Q&A)

| Feature | Where |
|---|---|
| Transactional reducers | Every write: constraint changes, publication, consent |
| Procedures with return values | `claim_job`, `load_execution_context`, `publish_proposal`, `check_lease`, `begin_operation` |
| Per-caller views over private tables | 22 views; nobody's browser receives another person's private data |
| Real-time subscriptions | All screens, plus the dispatcher waking the worker |
| Scheduled reducers | Lease expiry (crash recovery) and review reminders |
| Server-side validation in transactions | Consent, permission gates, publish-time flight checks, wording fingerprint |
| Lifecycle hooks | Presence (connect/disconnect, multiple tabs) |
| Identity + allowlist | Humans vs worker/dispatcher service identities |

**Why not event tables for agent activity?** They're public and views can't filter them, so every room's activity would be visible to everyone. A small status table behind a per-room view gives the same instant updates without the privacy leak.
