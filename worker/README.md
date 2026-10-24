# Tandem Python worker

Runnable LangGraph coordinator and personal-agent workflow, with an independent
offline harness. Tested on Python 3.13.2. The package declares Python 3.11+;
other interpreters have not been verified in this repository.

## Setup and quick check

```bash
cd worker
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test,live]'
.venv/bin/python -m pytest -q
.venv/bin/tandem-worker scenario targeted-budget --mode fixture
.venv/bin/tandem-worker scenario agent-objection --mode fixture
.venv/bin/tandem-worker scenario clarification-restart --mode fixture
```

The default tests prohibit network access. No provider keys, frontend, dispatcher,
or deployed SpacetimeDB are needed. `requirements.lock` records the exact tested
environment (including development and optional Claude dependencies); use
`pip install -r requirements.lock` before editable installation to reproduce it
on a compatible interpreter/platform.

Each scenario returns JSON containing the outcome, safe message/thread history,
proposal terms, resource-call counts, evidence mode and consent count. A successful
run produces `AWAITING_REVIEW`, **not agreement**. Fixtures never create human
approvals or claim live prices. The fixture demo covers flights only; it does not
silently treat accommodation or other trip costs as zero.

## Implemented flow

1. Claim a durable job with a lease and fencing token; load a consistent snapshot.
2. Require confirmed participation settings before visible representation.
3. Discover/reuse sourced candidates, normalize boundary types, run real CP-SAT.
4. Deliver addressed typed messages through a bounded dialogue loop. Each owner
   gets a separate private decision context and a room-safe voice context.
5. Route replies to targeted research or private consultation, re-solve and obtain
   new assessments for changed drafts.
6. Independently validate costs, coverage, evidence freshness, baggage and arrival
   overlap; publish immutable terms awaiting human review.

The coordinator is a checkpointed `StateGraph`. The personal graph is instantiated
per execution with ephemeral private context; its safe decisions/references return
to the coordinator. Clients, attempt tokens and private reasons are not checkpointed
in coordinator state. Hosted LangSmith tracing is disabled in this package.

Default limits: one active coordinator per `Worker.reconcile()` loop, 60-second
lease, 15-second heartbeat, three rounds, six turns/thread, 24 turns/round,
100 model calls and four research batches per generation. Turn and call counters
are durable across attempts. The sequential personal-turn scheduler is below the
two-model-call concurrency ceiling. These are call-count limits, not a measured
dollar-spend accounting system. A shared SQLite file is not a multi-worker
deployment strategy.

## Check wording in Python, enforce state in the backend

The worker decides the typed act with its owner's private context. It separately
projects permitted facts and asks the voice model to select/order canonical fact
sentences and an allowed connector. Python checks complete segments and references.
Unsupported wording is regenerated once, then replaced with a deterministic
template. Private caps/reasons never enter the voice context. This v1 grammar
deliberately constrains expression; it does not claim to prove arbitrary prose
safe through an LLM or keyword filter.

`submit_agent_message` includes worker-validation metadata bound to the act,
utterance, payload and evidence/claim refs. The backend checks identity, versions,
audience, thread membership, budgets and that digest. It has **no LLM calls and no
semantic wording checks**. The local contract suite deliberately demonstrates
that valid worker metadata can accompany nonsensical text: prevention belongs in
the worker. Neither a digest nor a worker flag defeats a compromised service.

Participation settings are enforced in both the worker's tool policy and the local
backend's structured checks. New settings require confirmation; proposed defaults
are Speak up off, Ask me first on, Give me time on. Disclosure/consultation grants
are action-specific, change the authority revision, and never create final consent.
Declining disclosure preserves the blocker. Review-control projection suppresses
reminders with Give me time and always allows deciding later.

## Independently exercise pause and resume

Use a fresh state directory. These commands explicitly simulate owners in the
**local fixture backend**; they are not production authentication endpoints.

```bash
.venv/bin/tandem-worker seed clarification-restart --state-dir /tmp/tandem-demo
# Copy job_id from the result:
.venv/bin/tandem-worker run-one JOB_ID --scenario clarification-restart --state-dir /tmp/tandem-demo
.venv/bin/tandem-worker questions --owner p1 --state-dir /tmp/tandem-demo
# Copy the pending question ID:
.venv/bin/tandem-worker answer QUESTION_ID --owner p1 --response yes --state-dir /tmp/tandem-demo
.venv/bin/tandem-worker reconcile --scenario clarification-restart --state-dir /tmp/tandem-demo
```

The waiting job releases its lease. On a new process, the worker opens the same
SQLite checkpoint, claims a new attempt, loads authoritative answers and resumes
the interrupt. Early answers and crashes before parking are reconciled. Constraint
or permission changes supersede the generation instead of restoring obsolete
authority from a checkpoint. Publication ends the job; final consent lives outside
the graph. `scenario clarification-restart` supplies its synthetic answer
automatically for a quick demonstration; the recovery tests use two OS processes.

## Backend and solver handoff

`ports.py` retains the agreed twelve application operations; the auxiliary methods
are scoped reads, evidence writes, operation receipts and thread transitions that
the backend adapter must map to its authorized projections/result transactions.
Print the principal boundary schemas with:

```bash
.venv/bin/tandem-worker contracts
```

`LocalBackend` persists a single synthetic room in SQLite. It is an enforcing test
double, **not the real SpacetimeDB adapter or an authentication implementation**.
Its owner arguments simulate already-authenticated identities. Do not deploy it as
an HTTP service. Production must derive owners/service roles from authentication,
implement scoped projections and consent reducers, and run the same invariants
against the actual module. No deployed module, generated bindings or credentials
exist here, so real-backend approval/read guarantees are not verified.

`FlightSolver` is a real minimal CP-SAT adapter using explicit `flight_cost_v1`:
choose one known-price flight per person, enforce caps and arrival overlap, minimize
total cost. `FlightValidator` independently checks the selection. This is **not**
the proposed minimax fairness policy or a full trip solver. Replace these ports
with Ronit's richer implementation when its policy/schema is ready. Unsupported
currencies/cost bases fail boundary validation; unknown critical data blocks final
validation. Observed listings are not reservations or guaranteed inventory.

## Configure real Claude and Apify

Run the following from `worker/`. Copy the examples without replacing an existing
configuration, then edit `.env` privately and set your desired future flight date
in `live.json`:

```bash
cp -n .env.example .env
cp -n live.example.json live.json
```

Fill `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` and `APIFY_TOKEN` in `.env`. Supply an exact
model ID available to your account; the worker never chooses a model automatically.
Keep `APIFY_ACTOR_BUILD=0.0.141`. Process environment values take precedence over
`.env`, with interpolation disabled. Credentials, `live.json` and `live-state/`
are ignored by Git. The example participant is a synthetic local-test owner;
its confirmation flags do not authenticate a real person or grant final consent.

`CLAUDE_API_KEY` and `APIFY_API_KEY` are also accepted. The worker reads `.env.local`
in the current directory when `.env` is absent. For a shared file in the repository
root while running from `worker/`, add `--env-file ../.env.local` to the live
commands below. Process variables take precedence across both key spellings.

```bash
# Local validation only; no network or charges. Missing variables return exit 2.
.venv/bin/tandem-worker providers check --config live.json

# Read-only credential, model access, pinned build and input-schema checks.
# No Claude generation and no Actor starts.
.venv/bin/tandem-worker providers check --config live.json --online

# Bounded billable provider test: one person, one Actor run, up to four messages.
.venv/bin/tandem-worker providers smoke --config live.json --state-dir ./live-state/smoke

# The complete LangGraph workflow, using real providers and LOCAL test state.
.venv/bin/tandem-worker run --providers live --config live.json --state-dir ./live-state/run
```

The smoke command tests provider access, normalization, a structured Claude decision,
and checked room wording. It reports `status: OK` when both provider paths work,
`mode: LIVE_PROVIDERS`, `backend: LOCAL_TEST`, token/call usage and Actor/run/dataset
references. It does not force an objection or create approval. It also reports
constraint/evidence checks; a successful API test does not assert feasibility.
Real graph execution may pause for owner input, a resource limit or missing evidence.
There is no automatic fixture fallback in live mode.

Successful smoke results are cached in their state directory until evidence becomes
stale. Repeating that command does not generate more paid calls. Changing the config
or command kind in the same directory is rejected; use a new directory deliberately
for a new test. Keep the existing directory to recover an interrupted run. The
local budgets include retries and persist across client/process recreation; they
are attempt limits, not a dollar estimate for Claude.

Default live limits are one Actor run with `maxTotalChargeUsd=1` where supported,
ten collected results, four Claude message attempts, 512 output tokens/message,
30-second request timeouts, a two-minute Actor polling deadline and 15-minute local
evidence freshness. Actor charge caps do not establish a universal total-cost
guarantee for every provider charge category. SDK retries are disabled; transient
Claude retries, corrections and voice regeneration share the same message budget.

### Provider details and teammate handoff

`ClaudeModel` uses the SDK's structured JSON parsing with Pydantic `Decision` and
`VoiceDraft`. Tests exercise the installed SDK over a mock HTTP transport, including
refusal and truncated non-JSON responses. Decision and room-voice contexts remain
separate. Operational metrics contain IDs, token counts, latency and safe outcomes,
never raw private prompts. No model generation occurs during metadata preflight.

The concrete mapper targets [`memo23/google-flights-scraper`](https://apify.com/memo23/google-flights-scraper),
Actor `ulPbFXCmcjxx3iPnp`, build `0.0.141` (build ID `eByt1QXZwUhbeMtQr`). Before
any paid run, preflight verifies account access, the pinned build and required input
fields. It sends an explicit one-way, one-adult, economy, USD search; it does not
use the Actor's default routes/dates or optional enrichment features. The field
contract is based on the [published input schema](https://apify.com/memo23/google-flights-scraper/input-schema).
**Live smoke verified on 24 October 2026:** `claude-sonnet-5-5`, SFO → LAX on
15 January 2027, ten normalized observed flights, one Actor run and two Claude
message requests. The result was correctly `EXPLORATORY_ONLY` because mandatory
fees remained unverified. No human consent or booking was created. This verifies
that bounded provider path, not all routes or multi-person negotiation behavior.

The normalizer checks route/date, decimal price conversion, source links and local
times with explicit IANA airport timezones. Ambiguous/nonexistent DST times and
malformed rows are quarantined. Only flight rows are accepted. The current
`baggage_required` flag means at least one **checked bag**; absent baggage facts
remain unknown. Candidate IDs distinguish observations and retain Actor/build/run/
dataset provenance. `valid_until` is our local freshness deadline, not a held fare.

Scraped prices use `cost_basis: OBSERVED_FLIGHT_PRICE` and retain unknown mandatory
fees. The solver may explore those prices, but the independent validator and local
publication guard block approval-ready publication with incomplete cost evidence.
With otherwise eligible agents, that yields `EXPLORATORY_ONLY`, not a technical
failure or a claim of verified inventory. A separate source-verification step is
needed to establish complete costs; configuration cannot waive that check.

`targeted_filters` lists approved changes per blocker. Targeted requests carry
explicit owner IDs and a typed blocker, and cannot repeat unchanged criteria or
drop a prior hard search filter. Private caps, raw reasons and transcripts are
excluded from Actor input. Durable run IDs are reused on recovery; an ambiguous
start is stopped rather than automatically paying for another run. A new Actor
start reserves the actual run budget, independently of graph research-batch counts.

`make_providers(config, settings, directory, snapshot=current_snapshot)` returns
`ModelPort`/`ResearchPort` implementations that your teammate can inject into the
existing worker. The optional snapshot binds searches to current room/input/
permission revisions. Call `providers.preflight()` before work. Neither this
factory nor these CLI commands implement a SpacetimeDB adapter or subscription.

No provider calls occur merely on import. Fixture commands keep using scripted
providers and do not load `.env` or the live provider factory.

## Test groups and remaining integration gates

```bash
.venv/bin/python -m pytest tests/unit tests/workflow -q
.venv/bin/python -m pytest tests/recovery -q
.venv/bin/python -m pytest tests/contract -q
```

The default suite skips the billable live test. To explicitly opt in after filling
credentials and reviewing the one-person configuration:

```bash
TANDEM_RUN_LIVE_TESTS=1 TANDEM_LIVE_CONFIG=live.json .venv/bin/python -m pytest tests/live -q
```

Only that marked test enables network access. It creates a fresh run directory,
so invoking it again can incur another bounded provider test.

Tests cover privacy canaries, preference gates, meaningful recipient replies,
unsupported wording, independent validation, transaction receipts, fencing,
revocation/cancellation, budgets, heartbeat under blocking calls, early answers,
lost effects/responses, two-process checkpoint recovery and provider run reuse.
`tests/contract` currently runs against the local double; it does not establish
SpacetimeDB security. Real backend integration, subscription dispatcher, human
consent/UI checks, full solver policy and credentialed live provider validation remain team
integration gates beyond the one-person smoke scenario above. No placeholder
tests claim those broader gates passed.

## Persistent worker service and subscription wake-ups

The production trigger path is SpacetimeDB -> TypeScript dispatcher -> Python
`POST /wake`. The same service scans at startup and polls as a fallback; there is
no separate Next.js worker launcher or controller queue.

```bash
.venv/bin/tandem-worker serve --config live.json --env-file ../.env.local \
  --state-dir ./live-state/browser-demo --room <room-id> --port 8080
```

Add `WORKER_WAKE_TOKEN` privately to the worker environment. Export the same value
for the dispatcher, together with `WORKER_WAKE_URL=http://127.0.0.1:8080/wake` and
its existing STDB_URL / STDB_DB / registered STDB_TOKEN. See dispatcher/.env.example.
The endpoint returns 202 for valid PLAN hints; hints neither claim nor execute a
job directly. Unauthorized requests receive 403, other rooms 404, malformed
requests 400, and unsupported job kinds are ignored. `GET /health` is a small
liveness check, not an execution or approval API.

Defaults: localhost:8080, fallback scan every 5 seconds, a 2-second input settling
window, one synchronous execution, and one service process per state directory
(Unix/macOS file lock). Flags: --host, --port, --poll-interval, --settle-seconds.
Both notification and polling paths re-read the durable queue and use existing
transactional claims, leases, checkpoints and provider budgets. Incoming job IDs
are hints only. Keep the same directory on restart; do not run the one-shot live
command concurrently with this service. Plain owner answers resume eligible work
automatically. Configuration/permission changes start new generations as before.

Starting serve enables paid execution for eligible jobs in the configured room.
Every active member needs confirmed participation settings and a flight-budget
input before provider preflight; the operator config cannot supply missing owner
authority. Future dates, matching owner names/searches and trip components, pinned
Actors and existing durable limits still apply. INTERPRET jobs remain unsupported.
