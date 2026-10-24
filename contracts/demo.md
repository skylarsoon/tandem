# Demo runbook: showing SpacetimeDB to judges

For: whoever drives the 3-minute demo, plus Anna (screens) and Skylar (worker).
Goal: make the SpacetimeDB track judges *see* that the database is the live coordination layer, not just storage.

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

To make reminders fire quickly during a demo: `spacetime call --server local tandem-dev set_reminder_delay 20`.

## Pitch line

*"SpacetimeDB isn't our database, it's our coordination layer. Four people and four AI agents share one live, private-by-default state. The database itself enforces consent, replans when something changes, and keeps every screen in sync."*

## SpacetimeDB features we use (for Q&A)

| Feature | Where |
|---|---|
| Transactional reducers | Every write: constraint changes, publication, consent |
| Procedures with return values | `claim_job`, `load_execution_context`, `publish_proposal` |
| Per-caller views over private tables | 22 views; nobody's browser receives another person's private data |
| Real-time subscriptions | All screens, plus the dispatcher waking the worker |
| Scheduled reducers | Lease expiry (crash recovery) and review reminders |
| Lifecycle hooks | Presence (connect/disconnect, multiple tabs) |
| Identity + allowlist | Humans vs worker/dispatcher service identities |

**Why not event tables for agent activity?** They're public and views can't filter them, so every room's activity would be visible to everyone. A small status table behind a per-room view gives the same instant updates without the privacy leak.
