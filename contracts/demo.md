# Demo runbook: showing SpacetimeDB to judges

For: whoever drives the 3-minute demo, plus Anna (screens) and Skylar (worker).
Goal: make the SpacetimeDB track judges *see* that the database is the live coordination layer, not just storage.

---

# Talk track

Two parts:
1. **SpacetimeDB basics** — the concepts a teammate needs before any of our code makes sense.
2. **Our implementation** — what we built on top of them, from a 60-second judge version down to the full technical walkthrough.

---

## Part 1 — SpacetimeDB basics

### What SpacetimeDB is

SpacetimeDB is a **database and an application server in one**. You write your backend logic as a **module** (we use TypeScript), then **publish** it *into* the database. Clients (browsers, Node scripts, our Python worker) connect straight to the database. There's no separate API server between them.

The model in one line: **reads are live subscriptions; writes are function calls that run as transactions.**

> **Example.**
> 1. Bob types the invite code and clicks Join. His browser calls the `join_room` reducer.
> 2. The transaction commits: Bob gets a membership row and a personal agent, and the room's `membership_revision` goes up.
> 3. Alice, Carol and Dave are subscribed to `room_members`, so Bob appears on their screens in the same instant.
>
> Nobody wrote an API endpoint or a websocket broadcast for this.

| A typical stack | SpacetimeDB |
|---|---|
| Frontend → REST API server → database | Frontend → database (the logic runs inside it) |
| Poll or set up websockets for updates | Subscribe once; changes are pushed after every commit |
| Auth checks in API middleware | Checks inside each function, using the caller's identity |

### The pieces of a module

**1. Tables.** Defined in code with typed columns, primary keys and indexes. Tables are **private by default** (only module code can read them) unless marked `public`. From our schema:

```ts
const membership = table(
  { name: 'membership',
    indexes: [{ accessor: 'byRoomMember', algorithm: 'btree', columns: ['roomId', 'member'] }] },
  { id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    member: t.identity(),      // the person's SpacetimeDB identity
    displayName: t.string(),
    active: t.bool() /* … */ }
);
```

> **Example rows** in `membership` for our test room:
>
> | id | room_id | member | display_name | role | active |
> |---|---|---|---|---|---|
> | 1 | 1 | `0xc200b8…` | Alice | ORGANIZER | true |
> | 2 | 1 | `0xc200df…` | Bob | PARTICIPANT | true |
>
> `byRoomMember` makes "is this identity a member of room 1?" an index lookup instead of a table scan.

**2. Reducers.** These are the functions clients call to change data. Each call runs as **one transaction**: every write lands, or none do. Reducers can't return data; they either succeed or throw an error. Inside a reducer, `ctx.sender` is the caller's identity and `ctx.db` gives access to every table, private ones included. Ours (trimmed):

```ts
export const joinRoom = spacetimedb.reducer(
  { inviteCode: t.string(), displayName: t.string() },
  (ctx, { inviteCode, displayName }) => {
    // find the room by invite code; fail if already a member
    addMember(ctx, roomId, displayName, 'PARTICIPANT');   // uses ctx.sender
    invalidateRoom(ctx, roomId, { membership: true }, 'member joined');
  }  // all of this commits together, or not at all
);
```

> **Example.**
> - Bob clicks Join a second time. The reducer throws, the client gets `530 INVALID_STATE: already a member of this room`, and nothing changes.
> - If the reducer had already written a row before throwing, that write would be rolled back too.

**3. Procedures.** Like reducers, but they **can return a value** and can make outbound HTTP calls. Database work happens inside `ctx.withTx(...)`, and that block may run more than once, so it must be safe to repeat. We use procedures wherever the caller needs an answer back, like claiming a job and getting a lease:

```ts
export const checkLease = spacetimedb.procedure({ jobId: t.u64(), attempt: t.u32() }, t.string(),
  (ctx, args) => runProcedure(() => ctx.withTx(tx => leaseFields(requireLease(tx, args.jobId, args.attempt)))));
```

> **Example.** A real `claim_job` response from `examples/claim_job.response.json`:
> ```json
> {"ok": true, "job_id": 4, "generation": 4, "room_id": 1, "kind": "PLAN", "attempt": 1,
>  "input_revision": 5, "membership_revision": 4, "permission_revision": 5,
>  "lease_expires_at_micros": "1792839963270410", "graph_thread_ref": null}
> ```
> A reducer couldn't hand back the `attempt` number. That's why claiming is a procedure.

**4. Views.** Read-only functions that compute rows **for whoever is asking** (`ctx.sender`). Views must be public, but they can read private tables, so a view is how you expose *some* private data to *the right* person. Views can only read through indexes, and clients subscribe to them like tables:

```ts
export const myConstraints = spacetimedb.view({ name: 'my_constraints', public: true },
  t.array(participantConstraint.rowType),
  ctx => [...ctx.db.participantConstraint.byOwner.filter(ctx.sender)]);   // only your rows
```

> **Example.** The same query, `SELECT * FROM my_constraints`, run by two people (real output):
> ```
> Alice → [{"key": "flight_budget_total", "value_json": "50000", "hardness": "HARD", "private_rationale": "saving for rent"}]
> Bob   → [{"key": "flight_budget_total", "value_json": "65000", "hardness": "HARD", "private_rationale": null}]
> ```
> Querying the table directly (`SELECT * FROM participant_constraint`) is refused: *"no such table … it may be marked private."*

**5. Scheduled reducers.** You insert a row with a time into a schedule table, and the database calls the reducer at that time. Clients can't call these directly. We use them for lease expiry and reminders:

```ts
export const expireLease = spacetimedb.reducer({ onSchedule: leaseTimer }, { arg: leaseTimer.rowType },
  (ctx, { arg }) => { /* if the lease really expired, put the job back to PENDING */ });
```

> **Example timeline.**
> 1. **12:00:00** The worker claims job 4 with a 60-second lease. The same transaction inserts a `lease_timer` row for 12:01:00.
> 2. **12:00:20** The worker crashes, so no more renewals arrive.
> 3. **12:01:00** `expire_lease` fires and sees the lease really expired. Job 4 goes back to `PENDING`, the dispatcher's subscription sees it reappear, and another worker claims it as attempt 2.

**6. Lifecycle hooks.** `init` runs once on first publish; `clientConnected` and `clientDisconnected` run as clients come and go (we use them for presence).

> **Example.** Alice opens the app in two tabs, so `presence.connections` is 2. She closes one; it drops to 1 and she still shows online. She closes the other: 0, offline, and `last_seen` is set.

**7. Event tables** are transient rows that are broadcast on commit and never stored. Clients only receive them if the table is public, and views can't read them, so they can't be filtered per caller.

### Identity

Every client connects with a token that maps to an **Identity** (a 256-bit id). The module sees it as `ctx.sender` and makes every permission decision from it. A token can be minted with `POST /v1/identity`, issued automatically on first connection, or come from an OIDC login provider. Service accounts (our worker and dispatcher) are just identities the module keeps on an allowlist.

> **Example.**
> ```
> $ curl -X POST http://127.0.0.1:3000/v1/identity
> {"identity": "c200c91036f0350a…", "token": "eyJ0eXAiOiJKV1QiLCJhbGci…"}
> ```
> The worker keeps that token as a secret, and the owner allowlists the identity with `add_worker`. When that worker calls `respond_to_proposal`, the module sees a worker identity and refuses: `NOT_AUTHORIZED: worker identities cannot perform human actions`.

### Subscriptions: how screens stay live

1. A client subscribes with SQL, e.g. `SELECT * FROM room_messages`.
2. It receives an **initial snapshot**, then an update after **every committed transaction** that changes its results, applied atomically and in commit order.
3. The SDK keeps a local cache and fires `onInsert` / `onUpdate` / `onDelete`, which is what React re-renders from.

The client never polls, and the UI never shows a half-applied transaction.

> **Example** (TypeScript, as in our dispatcher):
> ```ts
> conn.subscriptionBuilder()
>   .onApplied(() => console.log('initial snapshot loaded'))
>   .subscribe(['SELECT * FROM room_consent_counts']);
> conn.db.roomConsentCounts.onUpdate((_ctx, before, after) => render(after));
> ```
> When Carol approves, every subscriber's row changes from `{"accepted": 1, "pending": 3}` to `{"accepted": 2, "pending": 2}` in the same update. That's the "2 of 4 approved" counter.

### Runtime rules inside reducers

| Can't do | Use instead |
|---|---|
| Network calls | Procedures, or an external worker (us) |
| `Math.random` (throws) or the system clock | `ctx.random` and `ctx.timestamp`; reducers are deterministic |
| Return data to the caller | A procedure, or have the client read a view |
| Return an error object | `throw new SenderError('CODE: detail')`; the transaction rolls back |
| Keep state in module variables | Tables |

### Talking to it from different languages

- **TypeScript (browser and Node):** official SDK plus generated bindings (`spacetime generate`). This is what the frontend and the dispatcher use.
- **Python:** no maintained SDK, so we use the HTTP API:
  - `POST /v1/database/<db>/call/<name>` calls a reducer or procedure.
  - `POST /v1/database/<db>/sql` reads tables and views.
  - Quirks we found:
    - top-level argument names are camelCase, but nested fields and result columns are snake_case
    - options are sent as `{"some": v}`
    - reducer errors come back as HTTP 530 with `CODE: detail`

> **Example** (how the Python worker calls a reducer):
> ```
> POST /v1/database/tandem-dev/call/renew_lease
> Authorization: Bearer <worker token>
> {"jobId": 4, "attempt": 1, "leaseSeconds": 60}
>
> → 200 (empty body) on success
> → 530 "LEASE_LOST: attempt 1 was replaced by attempt 2" if another worker took over
> ```

### Day-to-day commands

| Command | What it does |
|---|---|
| `spacetime start` | Run a local server (`--in-memory` for throwaway test runs) |
| `spacetime publish --server local tandem-dev` | Build the module and deploy it into a database (`--delete-data=always` wipes it) |
| `spacetime generate --lang typescript --out-dir … --module-path spacetimedb` | Generate typed client bindings |
| `spacetime call <db> <reducer> <args…>` / `spacetime sql <db> "<query>"` | Call a reducer, or run SQL, as the publisher |
| `spacetime logs <db>` | Module logs |
| `spacetime login` + `--server maincloud` | Hosted deployment (Maincloud) |

---

## Part 2 — Our implementation

### The 60-second version (judges)

*"Tandem is a group trip planner where every person has their own AI agent. The hard part is coordination: four people and four agents writing to shared state at the same time, with private budgets, and only humans allowed to approve.*

*We use SpacetimeDB as the coordination layer, not just storage. All 29 tables are private, and every client reads through per-caller views, so private data is never sent to anyone else's browser. Every write is a reducer, a transaction that enforces the rules: approvals bind to the exact terms of a plan, outdated work is rejected by revision checks, and a crashed AI worker is fenced off by leases. Browsers subscribe to those views, so every screen updates the moment a transaction commits.*

*Our Python AI worker connects through an adapter. We ran its full planning flow against the real database (agents negotiating, re-searching, asking people questions, publishing a plan, four humans approving), and it's covered by 140 automated tests."*

### The 90-second version (in depth)

*"Most apps treat the database as a filing cabinet: the server makes the decisions and the database just stores the results. In Tandem, SpacetimeDB* is *the server. That matters, because our hardest problem isn't AI, it's coordination: four people and four AI agents changing the same plan at once, with private budgets, and only humans allowed to say yes. All 29 of our tables are private. Every read goes through a view computed for whoever is asking, so Bob's browser is never even sent Alice's budget. Every write goes through a reducer, a small function that runs as one atomic transaction with the rules built in. Change your budget, and in a single transaction the database records it, bumps the room's revision, retires the old plan, marks every approval of it stale, and queues new work for the AI. Approvals are bound to a SHA-256 fingerprint of the exact plan you saw, so an outdated tab can't approve something that's since changed. Our AI worker is a Python service that does the slow thinking outside the database. It's woken by a live subscription the moment work appears, claims the job with a lease, and has every write checked against that lease. If it crashes, a scheduled timer inside the database hands the job to another worker, and anything the dead one tries to write is rejected. Even the AI's final plan is re-checked inside the publish transaction against everyone's hard limits, baggage needs and arrival times. Because every screen subscribes to those views, the moment any of this commits, all four people watch it happen live."*

### Technical walkthrough

#### Architecture

```
Browsers (Next.js) ── subscribe to per-caller views ──▶ ┌──────────────────────────────┐
         └────────── call reducers (human actions) ───▶ │  SpacetimeDB module (TS)      │
                                                        │  34 private tables            │
Dispatcher (Node) ── subscribes to worker_pending_jobs ─│  38 reducers · 5 procedures   │
         │ POST /wake                                   │  26 views · 2 scheduled tasks │
         ▼                                              └──────────────────────────────┘
Python worker (LangGraph) ── HTTP: procedures + reducers via SpacetimeBackend adapter ──▲
```

- **The module owns all state and all rules.** There is no separate API server.
- **The worker does the slow work** (LLM calls, flight search, CP-SAT solving) outside transactions, then writes results back through reducers that validate them.
- **The dispatcher only wakes the worker.** It subscribes to a view that's empty unless the caller is an allowlisted worker identity, and it never writes.

#### Access control

- **Every table is private.** Clients can't query tables directly; they read through views, and each view is computed for `ctx.sender`, e.g. `my_constraints` returns only the caller's rows.
- **Shared views expose only shared fields.** The group chat view has no payload JSON, and approval progress is counts only (`3/4 approved`), never who is pending.
- **Two kinds of identity.** Humans are separate SpacetimeDB identities (one per browser profile in the demo). Workers and the dispatcher are service identities on an allowlist. Human-only reducers reject workers, and worker reducers reject humans.

#### One planning cycle, end to end

Running example: a four-person Hawaii trip. Alice's hard budget is $500 and her private reason is "saving for rent"; the others have $650 each.

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

**What this looks like in the example** (real output from a test run):
- **After Alice and Carol approve,** everyone's `room_consent_counts` shows `{"required": 4, "accepted": 2, "declined": 0, "pending": 2}`. It's a count, with no names.
- **Then Carol changes her budget to $600.** Bob still has the old plan open and clicks Approve: `STALE_SNAPSHOT: proposal 1 was superseded`.
- **Alice's earlier approval** now reads `STALE` in `my_consents`, so she'll review the new plan when it arrives.
- **Bob's feed** only ever said `"A participant updated their requirements"`: no name, no amount, no reason.
- **If the worker tries to approve:** `NOT_AUTHORIZED: worker identities cannot perform human actions`.

#### Guarantees and how they're enforced

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

**Each guarantee, as a concrete failure we test:**
- **Outdated tab:** Bob approves version 1 after Carol's change → `STALE_SNAPSHOT`.
- **Old worker:** a worker whose lease expired posts a message → `LEASE_LOST`; the replacement (attempt 2) succeeds.
- **Retry:** the same message key sent twice → one message and one turn charged. Same key, different text → `KEY_BODY_MISMATCH`.
- **Privacy:** an agent writes *"Alice is SAVING FOR RENT, so this is too expensive"* → `PRIVATE_CONTENT_DETECTED`, and nothing is posted.
- **Settings:** Alice has *speak up* off and her agent objects in the room → `CONSULTATION_REQUIRED`. It can object after she grants permission for that exact draft.
- **Bad plan:** the worker publishes Alice on a $500.01 flight → `INVALID_PROPOSAL`.
- **Endless debate:** the 7th message in a 6-turn thread → `TURN_BUDGET_EXHAUSTED`.

#### Live features

- **Presence:** `clientConnected` / `clientDisconnected` hooks count open connections per identity, exposed through `room_presence`.
- **Agent activity:** set by the worker under its lease (`set_agent_activity`). The view reports `IDLE` once the job stops running, so a crash never leaves an agent "thinking". We avoided event tables here because they can't be filtered per caller.
- **Review reminders:** a scheduled reducer sends one private nudge, only to people who opted in and didn't ask for time.

#### The worker adapter

`SpacetimeBackend` implements the worker's `BackendPort` over HTTP:
- It maps the worker's names ("p1", "mediator") to the module's numeric ids, its question kinds to ours, and its thread and message ids to ours.
- It handles SpacetimeDB's encoding: camelCase top-level arguments, snake_case nested fields, and `{"some": …}` options.

Integrating surfaced real mismatches between the worker's test double and the real backend. We aligned on:
- grant semantics: a yes starts a new generation
- opt-in reminders
- server-side publish validation
- a shared snapshot fingerprint
- a Python-compatible JSON encoder in the module, so both sides compute the same digest

#### Verification

| Suite | Tests | Command |
|---|---|---|
| Module rules against a fresh in-memory SpacetimeDB | 63 | `spacetimedb/scripts/test.sh` |
| Worker on its own (fakes) | 60 | `cd worker && .venv/bin/python -m pytest` |
| Worker + real module: the worker's contract tests and full planning runs | 17 | `scripts/backend-integration.sh` |

#### Current limits

- **Live runs are short on budget.** Claude and Apify run live against Maincloud (flights, Airbnb stays, airport transfers, activities, dinner), but each run is capped (actor runs, model calls, spend), and Apify allows 5 concurrent runs on our account.
- **Ideas aren't priced exactly.** Activity and dinner prices are per-person estimates, so they're suggestions and never part of what people approve. Car rentals are left out because the source doesn't say whether taxes are included.
- **The module can't judge semantics.** It checks wording mechanically (cited facts, exact private-text matches); judging meaning stays in the worker.
- **Not built yet:** leaving a room, and a real auth provider.

### Likely questions and answers

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

### 4. Friction, handled fairly and privately (≈45 s)
Run the fixture scenarios (no keys needed): `cd worker && .venv/bin/tandem-worker scenario <name>`.
- `group-arrival`: Dave's cheap flight lands 5 h late. His agent privately asks: *"Your flight costs $525: $125 more than your cheapest valid fare, so everyone lands within 2h of each other. May your agent accept this on your behalf?"* He says yes; the room sees a neutral summary, and only Dave sees his $125.
- `concession-declined`: Dave says no. The plan re-solves and the other three each take a later flight for $150 more, the smallest worst cost anyone carries.
- `budget-blocked`: Alice's budget is below every fare. The room sees *"1 traveller: every fare found is above a private limit"*. Only Alice sees *"the cheapest complete fare found for you is $290, above your $250 limit"*.
- `own-room-squeeze`: two own-room requests make the only 3-bedroom stay push Dave $80 over his trip limit. Only the two who asked are consulted; they agree to share and the plan fits everyone.
- `trip-happy`: flights, a shared stay and an airport transfer split equally, plus activity and dinner ideas.

Real run (live Google Flights, Airbnb, Booking taxis/attractions, Google Maps, Claude; Maincloud room 9). Alice was asked to share a room (no 3-bedroom stay found) and said yes; Dave was asked about $125 extra to land with the group and said yes. Result: a $2,860.88 trip, everyone landing 21:07–21:55, a $1,134.89 Waikiki 2-bedroom, a $46.99 people carrier, and $295.47 each in shared costs. Only Dave sees "$125.00 more than the cheapest trip available to you … so everyone lands within 2h of each other."

**Say:** *"The solver minimizes the worst burden on any one person, not just the total. Every 'no' becomes a new plan or an explanation, and nobody is ever named in the room."*

## Running it before the real worker exists

The simulator plays the worker with clearly labelled FIXTURE flights.

```bash
# 1. Backend: the shared Maincloud database is already published (tandem-dev).
#    Use STDB_URL=https://maincloud.spacetimedb.com STDB_DB=tandem-dev below.
#    For a private local copy instead:
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
cd dispatcher && set -a && source .env && set +a && npm start      # .env points at Maincloud

# Live Claude + Apify against the shared database (worker/.env has the keys)
cd worker && .venv/bin/tandem-worker live-room seed --config live.json --state-dir ./live-state
.venv/bin/tandem-worker run --providers live --config live.json --state-dir ./live-state
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
| Per-caller views over private tables | 26 views; nobody's browser receives another person's private data |
| Real-time subscriptions | All screens, plus the dispatcher waking the worker |
| Scheduled reducers | Lease expiry (crash recovery) and review reminders |
| Server-side validation in transactions | Consent, permission gates, publish-time flight checks, wording fingerprint |
| Lifecycle hooks | Presence (connect/disconnect, multiple tabs) |
| Identity + allowlist | Humans vs worker/dispatcher service identities |

**Why not event tables for agent activity?** They're public and views can't filter them, so every room's activity would be visible to everyone. A small status table behind a per-room view gives the same instant updates without the privacy leak.
