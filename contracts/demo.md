# Demo runbook: showing SpacetimeDB to judges

For: whoever drives the 3-minute demo, plus Anna (screens) and Skylar (worker).
Goal: make the SpacetimeDB track judges *see* that the database is the live coordination layer, not just storage.

---

# Talk track: explaining what we built

Use the **60-second version** with judges, and the **full walkthrough** to bring a teammate up to speed (about 10 minutes). Lines in *italics* are meant to be said out loud; the bullets under them are the details to have ready.

## The 60-second version (judges)

*"Tandem is a group trip planner where every person has their own AI agent. The hard part isn't the AI, it's coordination: four people and four agents changing things at the same time, with private budgets, and nobody allowed to say yes for anyone else.*

*So SpacetimeDB isn't just where we store data, it's the referee. Every rule lives inside the database: only humans can approve, private limits never leave their owner, outdated plans can't collect approvals, and a crashed worker can't overwrite newer work. Every screen subscribes to live views, so when anything changes, all four people see it instantly.*

*Our Python AI worker plugs in through an adapter. We proved it works by running the worker's full planning flow against the real database: agents talk, search, ask people questions, publish a plan, and four humans approve. It's all covered by 140 automated tests."*

## The full walkthrough (teammates)

### 1. The big idea: a shared notebook with a referee

*"Think of SpacetimeDB as one notebook that everyone writes in at the same time: four browsers, the AI worker and a small dispatcher. Nobody writes in it directly. Every change goes through a 'button' with rules attached, and each person only sees the pages they're allowed to see."*

- **Tables** are the notebook pages. We have 29, and **all of them are private**.
- **Reducers** are the buttons: 31 of them. Each one is a transaction, so it either fully happens or doesn't happen at all.
- **Procedures** are the 5 buttons that also return an answer (claim a job, load the job's details, publish a plan, check a lease, start an operation).
- **Views** are each person's personal window: 22 of them, each computed for *whoever is asking*.
- **Subscriptions:** browsers subscribe to views, so updates arrive the moment a change commits. No refreshing, no polling.

### 2. Privacy is enforced by the database, not the screen

*"Alice's budget and her reason ('saving for rent') are stored in a private table. Bob's browser never receives them. It isn't hidden on screen, it's never sent."*

- Every view filters by the caller, e.g. `my_constraints` returns only *your* constraints, and `room_messages` only the group chat of rooms you're in.
- Shared progress shows **counts only** ("3 of 4 approved"), never who's still pending, so nobody gets singled out.
- If an agent's group message accidentally contains someone's private reason word for word, the database blocks it (`PRIVATE_CONTENT_DETECTED`).
- **Why no event tables for live agent activity?** They're public and can't be filtered per person, so every room could watch every other room. We used a small status table behind a per-room view instead.

### 3. Only humans can say yes

*"The AI suggests; people decide. The worker's account is simply not allowed to press Approve, and approval only counts for the exact plan you saw."*

- Each published plan gets a **fingerprint** (a SHA-256 hash of its exact terms). Approving requires sending that fingerprint back.
- If anything changed since (someone's budget, room membership, settings), the approval is rejected: `STALE_SNAPSHOT`.
- Agreement is recalculated inside the same transaction as every response: **all affected people** must have accepted those exact terms. Three of four is not agreement.
- Anyone can withdraw later, and the plan reopens.

### 4. How the AI worker gets its jobs

*"When something changes, the database writes a to-do note: a job. A tiny program called the dispatcher watches for jobs and rings the worker's doorbell. The worker then claims the job and does the thinking."*

- **The dispatcher** is a TypeScript program subscribed to `worker_pending_jobs`, a view that's empty for everyone except allowlisted worker accounts. It only wakes the worker; it never claims or writes.
- **Claiming** gives the worker a **lease** (60 seconds, renewed every 15) and an **attempt number**.
- **Every worker write is checked:** right worker, right attempt, lease still valid, and nothing changed in the room since the job started. Otherwise `LEASE_LOST` or `STALE_SNAPSHOT`.
- **If the worker crashes,** a scheduled function inside the database notices the lease ran out and puts the job back in the queue. Another worker picks it up. If the old one wakes up, its writes are rejected.

### 5. Nothing happens twice

*"Networks fail. If the worker retries something, the database recognizes it and returns the original result instead of doing it again."*

- Every worker action carries a **request key**. Same key and same request returns the stored result; same key with a different request is rejected (`KEY_BODY_MISMATCH`).
- A replacement worker replaying the crashed worker's action gets the original result too.
- **The spending ledger:** every Claude call and flight search is recorded *before* it runs. After a crash, finished calls are reused instead of paid for twice, and per-run budgets (100 model calls, 4 searches) survive crashes.

### 6. Agents really talk to each other

*"Agents hold threaded conversations. Every message has two parts: a typed action the system acts on, and a sentence people read. The sentence never has authority; only the typed part does."*

- **Message types:** `PROPOSE`, `EVALUATE`, `OBJECT`, `COUNTERPROPOSE`, `CLARIFY`, and a few control types. There's no free chat.
- **Threads:** agents can reply to each other directly, but always through the database, which checks every message:
  - sender and recipient are in the thread
  - no broadcasting
  - a reply answers a message the sender actually received
  - every cited fact really exists in the room
- **Turn limits:** 6 turns per thread and 24 per round, so agents can't argue forever. Repeated sends are counted once.
- **The wording fingerprint:** the worker fingerprints each message, and the database recomputes it to confirm the text wasn't altered in between. We had to write a JSON encoder in TypeScript that matches Python's output byte for byte, accents and emoji included.

### 7. Your settings change what your agent may do

*"During onboarding you choose how your agent represents you: speak up for me, ask me first, give me time. The database enforces these, not just the AI's instructions."*

- Until you confirm your settings, nothing involving you can appear in the group chat (`PREFERENCES_UNCONFIRMED`).
- With *speak up* off, your agent can't object in front of the group until you approve that specific objection.
- With *ask me first* on, it can't concede on your behalf without asking. Searching beyond your allowed range always needs a yes.
- **Permissions are tied to one exact draft plan.** A yes for one plan doesn't carry over to another (`CONSULTATION_REQUIRED`).
- A yes changes what the agent may do, so planning restarts cleanly. A no keeps the blocker in place without revealing why.
- **Reminders are opt-in** and never go to someone who asked for time. "Decide later" is always available.

### 8. The database double-checks the plan

*"Even if the AI worker had a bug, it couldn't publish a plan that breaks someone's limits. The database re-checks every flight before accepting the plan."*

When a plan is published, the database checks, inside the same transaction:
- **coverage:** exactly one flight per traveller
- **hard limits:** nobody's flight is over their hard budget
- **baggage:** included wherever someone requires it
- **freshness:** prices aren't expired
- **timing:** everyone arrives within the room's window (2 hours by default; the organizer can change it)

Any failure means the plan is rejected (`INVALID_PROPOSAL` / `INVALID_COVERAGE`).

### 9. Live multiplayer touches

*"The room feels alive: you see who's online and what each agent is doing, right now."*

- **Presence** uses SpacetimeDB's connect/disconnect hooks and counts open tabs, so closing one of two tabs doesn't flicker you offline.
- **Agent activity** ("searching…", "writing…") is set by the worker and shown live. It automatically drops to idle if the worker stops.

### 10. The adapter: plugging in Skylar's worker

*"Skylar built the AI worker against a practice backend. We built the real one. The adapter is the translator between them, and we proved the two work together."*

- **What it translates:** names (the worker says "p1" and "mediator", the database uses numbers), question types, thread and message IDs, and SpacetimeDB's encoding quirks.
- **Building it surfaced real mismatches**, which we fixed:
  - whether a yes restarts planning
  - whether reminders are opt-in
  - whether the backend re-checks the plan
  - two different fingerprints for the same snapshot
- **Proof:** we run Skylar's own contract tests *and* his full planning scenarios against the real database: budget retries, an agent's objection triggering a new search, pausing for a question and resuming in a fresh process, a declined permission, and a granted one.

### 11. How we know it works

*"Everything above is tested automatically against a real SpacetimeDB server, started fresh for every run."*

| Suite | Tests | Command |
|---|---|---|
| Backend rules (privacy, consent, leases, threads, permissions, publish checks, presence, reminders, dispatcher, simulator) | 63 | `spacetimedb/scripts/test.sh` |
| Skylar's worker on its own | 60 | `cd worker && .venv/bin/python -m pytest` |
| Worker + real database together | 17 | `scripts/backend-integration.sh` |

### 12. What's honest to say is not done yet

*"The plumbing and the rules are proven. The intelligence is next."*

- **AI and search:** the integration tests use a scripted stand-in for Claude and made-up flights labelled FIXTURE. The real Claude and Apify connectors exist but haven't been run live.
- **Wording:** the database can't judge whether a sentence is true or a reworded leak; that check lives in the worker.
- **Not built yet:** leaving a room, and a real login provider (the demo uses one browser profile per person).

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
