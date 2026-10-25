# Tandem conversational planning

The trip page contains two panels: established trip details, revisions and compact
workflow progress on the left; a persistent private/shared conversation on the
right. There are no permanent budget, airport, baggage or accommodation forms.
Questions, suggested changes, participation settings and proposal approval appear
as inline conversation cards when relevant. The profile page remains separate.

## Run

```bash
cd web
npm ci
# Configure NEXT_PUBLIC_STDB_URL and NEXT_PUBLIC_STDB_DB in .env.local.
npm run dev -- --port 3001
```

Create or join a trip in the left panel. The organizer's first message prepares a
shared trip description, explicitly labeled as shared in the composer. Later
messages go privately to your agent, including during active work and after
onboarding. Shared agent utterances are labeled separately. Confirm proposed
changes before they become established details; approve final terms separately.

The frontend only calls existing SpacetimeDB reducers and subscribes to
participant-scoped views. It never starts Python or sends provider credentials.
Use separate browser profiles for separate participants. Anonymous identities are
saved in database-scoped localStorage; clearing storage loses their access.

Run the registered worker and dispatcher separately. Their existing setup lives in
`../worker/README.md` and `../dispatcher/`. Backend support for interpreting
mid-workflow chat and non-flight trips is being developed separately. Sending a
message means it was stored, not that it was interpreted or that replanning ran.
See [BACKEND_HANDOFF.md](BACKEND_HANDOFF.md) for exact APIs, remaining action items,
and end-to-end acceptance scenarios.

## Verify

```bash
npm run typecheck
npm test
TANDEM_NEXT_DIST_DIR=.next-integration npm run build
# Installed Playwright Chromium required; no backend or paid providers:
npm run test:e2e -- e2e/planning.spec.ts
```

The component browser harness bundles the real planning components with explicit
fixture subscriptions/reducer responses. It covers ongoing chat, inline question
payloads, correction confirmation, failure/reconnect handling, room privacy and
mobile layout, and saves desktop/mobile screenshots. It is never imported by the
application. The non-flight fixture verifies frontend flexibility only.

The existing disposable-backend subscription harness can be run from the repo root:

```bash
SPACETIME_BIN=/path/to/spacetime bash scripts/web-integration.sh e2e/room.spec.ts
```

That harness still uses the existing flight-provider fixtures. Required legacy
owner inputs are seeded by a localhost-only test helper, rather than reintroducing
old forms into the production page. It checks real subscriptions, worker pause/
resume, participant isolation and exact-term consent. It does not establish the
teammate-owned conversational backend capabilities.

Manrope and IBM Plex Mono are bundled locally. On mobile, the overview collapses
and the conversation/composer remain available. Room changes clear private data;
disconnects disable actions and clear subscription content while preserving the
current draft. Revision summaries use shared authoritative events, never another
participant's private input.
