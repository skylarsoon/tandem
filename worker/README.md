# Tandem Python worker

Runnable LangGraph coordinator and personal-agent workflow, with an independent
offline harness. Tested on Python 3.13.2. The package declares Python 3.11+;
other interpreters have not been verified in this repository.

## Setup and quick check

```bash
cd worker
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test,claude]'
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

## Optional provider adapters

`ClaudeModel(model=..., api_key=...)` uses separate structured decision and voice
calls. A compatible model must be supplied explicitly (e.g. server `CLAUDE_MODEL`);
there is no hardcoded entitlement or automatic provider switch. The offline CLI
always uses `ScriptedModel`, and live Claude access has not been exercised here.

`ApifyFlightResearch` requires an allowlisted Actor ID, pinned numeric build,
confirmed room/revision-bound route/date/count inputs, an Actor-specific input
builder and item normalizer. These mappings cannot be guessed before selecting and
testing the Actor. Raw private prompts/caps are never sent to the input builder.
Runs/datasets are tracked in a durable `RunLedger`; it resumes known run IDs,
caps polling/collection, quarantines malformed rows and uses `APIFY_OBSERVED`.
An ambiguous start is stopped for reconciliation rather than automatically paying
for another run. A crash between provider acceptance and recording its ID remains
an external-transaction limitation. The poll/run tests use a fake transport;
**no live Actor schema, coverage, pricing or reliability has been validated**.

API behavior follows the official [Actor run](https://docs.apify.com/api/v2/actors-runs-post)
and [dataset items](https://docs.apify.com/api/v2/dataset-items-get) contracts.
No provider calls are made just by importing either adapter. The CLI currently
supports fixture mode only; wire live dependencies explicitly after validation.

## Test groups and remaining integration gates

```bash
.venv/bin/python -m pytest tests/unit tests/workflow -q
.venv/bin/python -m pytest tests/recovery -q
.venv/bin/python -m pytest tests/contract -q
```

Tests cover privacy canaries, preference gates, meaningful recipient replies,
unsupported wording, independent validation, transaction receipts, fencing,
revocation/cancellation, budgets, heartbeat under blocking calls, early answers,
lost effects/responses, two-process checkpoint recovery and provider run reuse.
`tests/contract` currently runs against the local double; it does not establish
SpacetimeDB security. Real backend integration, subscription dispatcher, human
consent/UI checks, full solver policy and live provider validation remain team
integration gates. No placeholder tests claim those gates passed.
