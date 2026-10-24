# LangGraph worker implementation and independent test plan

Based on [architecture.md](architecture.md), version 3, especially §§5–9, 13–16, and 19–21. Updated 24 October 2026. The older `cal_hacks_13_0_coordination_architecture.md` is historical reference; `architecture.md` is authoritative for this implementation.

**Status:** Implementation specification with team-agreed backend interfaces. A runnable offline worker, persistent local backend, minimal real flight solver and executable tests now exist under [`worker/`](worker/README.md). The README records implemented behavior and remaining integration gates; this document remains the target specification, not a claim that all backend/provider/product gates have passed. Numeric limits are configurable starting defaults, not measured performance guarantees.

This revision adds confirmed participation preferences, schema-v2 threaded dialogue, separate private decision and room-voice contexts, utterance validation, and the associated independent tests. It preserves the agreed backend operations and recovery guarantees.

## 1. What you are implementing

Build a bounded planning program that invokes personal agents, research, and deterministic computation, then submits a proposal for human review. LangGraph is the program's control flow and recovery mechanism.

| Layer | Owns | Must not decide |
| --- | --- | --- |
| Worker runner | Claiming, heartbeats, cancellation, recovery, execution scheduling | Human consent |
| Coordinator graph | Research → solve → addressed dialogue → resolve blockers → validate → publish | Authorization or changing confirmed constraints |
| Personal-agent graph | One owner's scoped decisions, enforced participation policy, and permitted utterances | Other owners' private context or human approval |
| SpacetimeDB | Structured authorization/state checks, durable messages/jobs, immutable proposals, consent | Natural-language wording judgments, LLM calls, or slow solver execution inside reducers |
| Solver and validator | Encoded feasibility and declared ranking policy | Psychological consensus or missing external facts |

One graph definition can serve every personal agent. Each invocation receives a different authorized context and agent identity. A separate server or model per participant is unnecessary.

**First vertical slice:** Four confirmed participant briefs and participation settings + labeled candidate fixtures → real addressed threaded dialogue with deterministic voice fixtures → independently checked draft → immutable proposal awaiting human review. Run this entirely from a Python CLI with a fake backend. Then add restart/pause behavior, the real backend and solver, Claude decision/voice calls, and Apify behind the same interfaces.

Start with confirmed inputs. Conversational extraction is a separate entry workflow: private message → proposed typed patch → private confirmation card → END. Only the owner's authenticated confirmation updates constraints and creates a planning job. Do not mix tentative model extraction into the planning snapshot.

## 2. Agreed team interfaces and extensions

Skylar owns orchestration and protocol; Ronit owns solver/validation. The team has agreed on the operations below; preserve their names and responsibilities. Assign a named owner for the SpacetimeDB module and authorization before integration. Extend the existing contracts with example JSON payloads and typed errors for the new policies and dialogue.

| Interface                  | Required input                                                      | Required output / behavior                                                                          |
| -------------------------- | ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `claim_job`                | Job ID, worker identity, request key                                | Atomically returns attempt token, expiry, execution generation, snapshot ref; competing claim fails |
| `load_execution_context`   | Job and attempt                                                     | Consistent snapshot, confirmed participation preferences/revision, tool policy, permitted conversation-thread refs, current authorization |
| `renew_lease`              | Job, token                                                          | Extends current attempt only; expired/replaced attempts fail                                        |
| `submit_agent_message`     | Schema-v2 envelope, worker-checked utterance and validation metadata, token, stable key, thread metadata when opening a thread | Structured policy/audience checks and worker-validation metadata checks; durable message, thread advance, budget charge and delivery state atomically; duplicate returns original receipt |
| `record_message_processed` | Message, recipient, token, reply refs                               | Consumer receipt; retry cannot create a second logical reply                                        |
| `ask_owner`                | Owner, question kind/version, exact proposed action, snapshot ref, key | Private question ref; supports missing facts, objection disclosure, concession consultation and broader exploration |
| `answer_question`          | Authenticated owner, question/version, answer                       | Authoritative answer ref scoped to that question/action; classify input or authority changes; never creates final consent |
| `park_job`                 | Attempt, question refs, graph thread/checkpoint ref                 | `WAITING_FOR_INPUT`, lease released; must handle an answer arriving early                           |
| `schedule_resume`          | Valid answer ref, waiting generation                                | Makes eligible work once; resumes old generation only if still valid                                |
| `publish_proposal`         | Attempt, snapshot fingerprint, terms, validation/evidence refs, key | Atomic immutable proposal + human-review records + successful job result                            |
| `finish_job` / `fail_job`  | Attempt, typed outcome, key                                         | Terminal or retryable state; cannot overwrite successful publication                                |

These application operations include the team's agreed extensions to architecture §14.3; they are not SDK methods or a requirement for separate HTTP endpoints. The Python backend adapter translates them to authenticated reducers and authorized reads. Publication atomically commits the immutable proposal, human-review records and successful job result. If the underlying module uses separate transactions, the adapter must reconcile the durable proposal receipt before completing the job, without publishing a second proposal.

Create and advance conversation threads through `submit_agent_message`; no additional public thread-management operation is required. The Python worker checks wording before submission. SpacetimeDB has no LLM capability in this design and does not judge whether a sentence is truthful, private, contradictory or consistent with its typed act. It derives the effective audience and permitted structured fields, checks current authority/revisions, and accepts worker-validation metadata only from the authorized service identity. Persist that metadata and block messages lacking a deliverable validation status. A pass flag cannot bypass the backend's independent structured checks. The backend computes or verifies the canonical proposal terms digest from the actual terms.

Bind worker-validation metadata to the exact submitted message: include validator version, checked snapshot/permission revision, sanitization status, and a digest covering the utterance, typed payload and claim/evidence refs. The backend can recompute the digest and check versions without interpreting the text. This records which content the trusted worker checked; it does not prove semantic correctness or protect against a compromised authorized worker. Wording safety remains the Python worker's responsibility.

The Python backend adapter returns typed command receipts and reads committed state. A transport acknowledgement alone is not proof of a successful application mutation. Do not assume every reducer returns an arbitrary JSON object directly; verify the selected SpacetimeDB transport and receipt projection during integration.

Use safe errors such as `STALE_SNAPSHOT`, `LEASE_LOST`, `NOT_AUTHORIZED`, `QUESTION_SUPERSEDED`, `KEY_BODY_MISMATCH`, `EVIDENCE_EXPIRED`, `PREFERENCES_UNCONFIRMED`, `THREAD_CLOSED`, and `TURN_BUDGET_EXHAUSTED` for structured contract failures. `UTTERANCE_PAYLOAD_MISMATCH` is a worker-side wording-validation error, not a semantic judgment returned by SpacetimeDB. These are expected control-flow outcomes, not prompts asking Claude to fix authorization.

## 3. Graph design

Use a `StateGraph` with named deterministic nodes and a few model-backed nodes. A model does not need to choose the next node when a typed status already determines the route. State, nodes, conditional edges, and fan-out are standard LangGraph mechanisms. [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)

```text
START → load_snapshot → check_required_inputs
  missing → persist_questions → await_owner → recheck_current_state
  ready   → initial_discovery_or_reuse → normalize → solve

solve:
  candidate draft → dispatch_evaluations → advance_agent_dialogue → collect_replies → route_blockers
  no candidate   → route_blockers using solver evidence
  invalid model → fail explicitly
  unknown       → bounded retry or pause as unresolved

route_blockers:
  substantive follow-up → advance_agent_dialogue → collect_replies → route_blockers
  permitted new search → targeted_research → merge → solve
  owner needed        → persist_questions → await_owner → recheck_current_state
  all eligible        → independent_validation → audience_projections → publish → END
  exhausted/no progress/insufficient evidence → persist_pause_outcome → END
```

Every route also checks cancellation, current authorization, and remaining budget. Validate even when all agents report `ELIGIBLE`. A deterministic hard violation cannot be outvoted by agent messages. A private objection with no shareable explanation still blocks readiness without revealing the reason.

### Node contracts

| Node | Action and output | Model? |
| --- | --- | --- |
| `load_snapshot` | Verify attempt; load immutable inputs, active members, confirmed participation settings, policy and grants | No |
| `check_required_inputs` | Schema/domain checks; return precise missing-field requests | Usually no |
| `initial_discovery_or_reuse` | Run allowed research requests or reuse still-valid evidence; record purpose and mode | No |
| `normalize` | Validate provenance, cost basis, timezone, missing fields; quarantine malformed records | No |
| `solve` | Call Ronit's typed solver; store result/status and draft reference | No |
| `dispatch_evaluations` | Open participant-scoped evaluation threads and persist separately addressed `PROPOSE` messages | No |
| `advance_agent_dialogue` | Deliver the next authorized turn through the hub; invoke the recipient graph; enforce thread/round budgets | Recipient graph uses models |
| Personal `evaluate` | Produce a typed act from owner-private context, enforce policy, then independently phrase and check an authorized utterance | Yes, separate decision/voice calls |
| `collect_replies` | Track latest effective assessments for the current draft plus unresolved thread blockers; deduplicate and enforce deadline | No |
| `route_blockers` | Map permitted objections to search, clarification, or stop; optionally draft bounded search suggestions | Optional |
| `targeted_research` | Validate search against confirmed exploration permissions and budget; record blocker reference | No |
| `independent_validation` | Recheck selected facts, costs, membership coverage, hard constraints and freshness | No |
| `audience_projections` | Construct typed private/shared outputs; generate explanations only from permitted facts | Optional |
| `publish` | Submit through version/fencing checks; return immutable proposal ref | No |

Initial discovery precedes initial optimization. Reuse evidence only when its scope, authorization and freshness still match. An infeasible initial candidate set may have no complete draft to evaluate. Use its solver blocker report for targeted search; never fabricate a proposal merely to send `PROPOSE`. A solver `UNKNOWN` result must not become `INFEASIBLE`. Every re-solve produces a new draft reference and requires assessments against that draft; eligibility for old terms cannot carry forward automatically.

### Confirmed participation preferences

Require explicit owner confirmation before group-visible representation. On the confirmation surface preselect `speak_up_for_me=false`, `ask_me_first=true`, and `give_me_time=true`. These proposed selections grant no authority until confirmed. Previously authorized independent research may continue while settings are unconfirmed. The onboarding model may propose settings for review; it cannot silently activate settings inferred from sparse answers or past behavior.

| Setting | Enforcement in trusted tool execution |
| --- | --- |
| Speak up for me | If enabled, allow a neutral agent-attributed objection without private caps or reasons. If disabled, ask privately before surfacing the objection. Refusal to disclose does not remove the blocker or make the draft eligible. |
| Ask me first | If enabled, privately consult before a group-visible concession or change involving the owner, including an option within their authorized exploration range. If disabled, consult when exploration exceeds that range. |
| Give me time | Suppress reminders and urgency wording; include a decide-later option in approval-card projections. Pending remains pending regardless of elapsed time. |

The settings compose: satisfy every applicable consultation/disclosure gate before a visible action. A confirmed exploration allowance is not permission to disclose its private value. Asking permission to surface a neutral objection must not expose the objection before the answer arrives. While waiting, publish only permitted operational pending status without identifying a holdout.

Participation settings cannot relax hard constraints, authorize payments, or grant final consent. Preference edits increment the permission revision and invalidate affected pending output. Scope any action-specific permission to the exact owner, question, action and snapshot. If an answer changes constraints or authority, start a new generation using current state rather than treating the answer as approval of the old plan.

### Personal-agent execution

Begin with sequential turn scheduling, with a global cap of two concurrent model calls. Each recipient evaluates using its own authorized context. The same personal graph serves every agent; a shared all-owner prompt is forbidden.

Personal flow: receive envelope → verify sender/recipient/thread/revisions → build private decision context → deterministic own-term checks and structured model act → enforce participation policy → project permitted facts → generate room voice → validate utterance → persist reply and processing receipt → return permitted reply reference.

### Two contexts and checked utterances

The private decision context can contain the owner's confirmed constraints, optional reasons and permissions. It produces a typed act. A separate projection builds the room-voice context from only the room-permitted act and facts. Never copy raw private payloads, caps, reasons or private decision history into that context. The mediator prompt receives only permitted replies; the trusted solver receives necessary numeric inputs through a separate computational path.

Use Claude to generate room wording with structured claim references. All wording validation runs in Python before `submit_agent_message`: resolve each referenced factual claim against the permitted payload/evidence and check for unsupported prices, flight facts, concessions, commitments or contradictions. Any LLM-assisted semantic review also runs in the Python worker, never in SpacetimeDB. References alone do not validate the rest of a sentence; check the complete wording. Text never creates an application fact, changes a constraint or grants approval.

For rejected wording, regenerate once from the permitted context with focused validation errors. If the second attempt fails, render a deterministic template using only checked fields. Persist sanitization status as `VALIDATED` or `TEMPLATE_FALLBACK` for deliverable text; `REJECTED` text cannot be delivered. Apply the same checks to fallback values. Never pass private text into regeneration errors. Record rejection codes and references rather than raw rejected wording in shared logs.

Owner-private utterances use a separate owner-authorized projection and appear only in My Agent. Live Negotiation renders committed, authorized room utterances, with agent attribution; it does not expose private decision traces or hidden chain-of-thought. Recheck current permissions immediately before delivery, including after room-voice generation.

### Threaded dialogue and progress

Every substantive turn pairs a defined typed act with an utterance; there is no free-chat act. Agents may address each other directly within an authorized open thread, but all delivery passes through the mediator hub. Its Python path performs wording checks and sanitization; SpacetimeDB performs structured authorization, sequencing and persistence. Do not broadcast to all agents: initial evaluation requests are individually addressed. Conversation recipients and human readers are separately authorized; participating agents do not gain access to private data merely because a thread exists.

The mediator opens threads, may join them, summarizes permitted outcomes and closes them when resolved, waiting for a person, or making no progress. A reply must be able to change research, clarification, validation or proposal selection. Carry its message/thread reference into the downstream operation so tests can establish the causal link.

Use progress fingerprints based on the typed act, relevant new payload values, evidence and resolved blockers, excluding cosmetic wording. Repeated polite disagreement does not reset budgets. Missing agents or unresolved objections prevent publication even if other threads resolve. After a human pause, open a successor conversation thread when needed; do not append new turns to a closed thread.

Reuse a small personal graph through an explicit input/output wrapper. LangGraph supports this mapping when parent and child have different state schemas. The wrapper is a useful boundary, but access checks still belong in application code. [Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)

For the first version, personal evaluations return `NEEDS_OWNER_INPUT` instead of interrupting inside concurrent child graphs. The coordinator creates all required private questions and pauses once. This keeps resume routing manageable.

If using LangGraph `Send` later, send a task containing agent ID, draft/message refs, conversation-thread ref and expected snapshot, never all participants' briefs. Merge individual replies by immutable message ID: same ID/body is a no-op; same ID/different body is an error. Separately derive the latest effective assessment per `(round_id, draft_ref, agent_id)` from authoritative sequence and reply links. A subsequent legitimate reply is not a duplicate conflict. Avoid concurrent replacement of one state field or unchecked list concatenation. The fan-in verifies the full expected set; missing agents are pending, never eligible by default.

## 4. State and typed protocol

Use Pydantic for external boundaries (model outputs, commands, envelopes, normalized evidence). A `TypedDict` is sufficient for internal graph state if all ingress is validated. Serialize checkpoint state as small data and references; inject clients through runtime dependencies.

```text
CoordinatorState
  schema_version, room_id, job_id, execution_generation
  snapshot_ref, input_revision, membership_revision, permission_revision
  policy_version, candidate_set_ref, candidate_set_version
  round_id, draft_ref, solver_result_ref, validation_ref
  expected_agent_ids, permitted_reply_refs_by_agent, conversation_thread_refs
  blocker_refs, pending_question_refs
  round_count, budget_ref, last_progress_fingerprint
  outcome, published_proposal_ref

PersonalState
  schema_version, owner_id, agent_id, room_id, execution_generation
  snapshot_ref, inbound_message_ref, own_brief_ref, own_terms_ref
  participation_preferences_ref, participation_revision, tool_policy_ref
  conversation_thread_ref, permitted_evidence_refs, assessment_ref
  room_voice_projection_ref, outgoing_message_refs

ParticipationPreferences
  owner, speak_up_for_me, ask_me_first, give_me_time, confirmed_at, revision
  referenced by AgentIdentity.tool_policy

ConversationThread
  room, thread_id, participant_agent_ids, topic_ref, input_revision
  turn_budget, turns_used, state, closure_reason

AgentMessage
  schema_v2_envelope, typed_body, utterance, thread_id
  claim_refs, effective_audience, sanitization_status, processing_state
  worker_validation(validator_version, checked_snapshot, permission_revision, content_digest)
```

Keep raw private chats out of coordinator state and mediator prompts. Resolve numeric constraints only inside trusted solver/validator calls. Owner-scoped checkpoints may contain necessary private material; they need restricted storage, retention, and invalidation too. Thread names do not enforce privacy. Keep credentials, backend/model clients, and mutable lease tokens out of checkpoints; inject the currently claimed token on each attempt so a resumed checkpoint cannot restore an obsolete one.

Proposed assessment body:

```json
{
  "draft_ref": "draft-r12-1",
  "evaluation": "NEEDS_OWNER_INPUT",
  "blockers": [{"code": "UNCONFIRMED_RETURN_DEADLINE", "constraint_ref": "c17"}],
  "permitted_search_request_refs": [],
  "owner_question_ref": "q17-v1",
  "evidence_refs": []
}
```

Use `ELIGIBLE`, `INELIGIBLE`, `NEEDS_OWNER_INPUT`, `INCOMPLETE_EVIDENCE`. Enforce variant-specific required fields. The model may suggest a question body; trusted code creates the question and binds its reference. Reject model-invented evidence refs, references to other owners, and unsupported constraint changes.

Adopt message `schema_version=2`, including conversation `thread_id`, `utterance`, explicit recipient, reply linkage, revisions, evidence refs and stable idempotency key. Retain the architecture's defined message vocabulary. Add a `draft_ref` for evaluations before immutable proposal publication; require final proposal ID/version for human review. Store sender-bound identity, effective audience, server sequence, claim refs, sanitization status and processing receipts. Models cannot assign effective audience or authenticate `OWNER_RESPONSE`; that message carries an actual backend answer reference.

Where architecture §15 contains both old and expanded `AgentMessage` sketches, use the expanded definition with utterance, thread and sanitization fields. Reject unsupported schemas for active processing without mutation. Retain historical messages under their original interpretation; do not silently reinterpret schema-v1 messages as schema v2.

Distinguish identifiers throughout: the envelope's `thread_id` identifies a conversation; `graph_thread_ref` identifies a LangGraph checkpoint thread. A conversation may close while a graph waits for human input, and one graph generation may process several conversation threads. Never use either identifier as a substitute for authorization.

## 5. Worker lifecycle and recovery

Worker loop: receive wake-up or scan → claim → load current context → execute/resume → publish, park, or finish. The TypeScript dispatcher only wakes it; the durable queue is sufficient to run with reconciliation polling while that dispatcher is unfinished.

Starting defaults: one active coordinator job, two concurrent model calls, three substantive negotiation rounds, six turns per conversation thread, 24 total turns per round, one targeted search batch per round, one malformed-output retry, one room-voice regeneration, and two retries for transient provider failures. Configure time/cost/candidate limits as well. Lease: 60 seconds, heartbeat every 15 seconds. These are engineering starting points, not measured performance claims.

Count each newly committed message once against the thread and round turn budgets; duplicate delivery, operation replay and voice regeneration do not create additional conversation turns. Regeneration still consumes model-call/spend budget. Reserve turn capacity transactionally when committing the message. At a limit, close the affected thread and pause unresolved work; do not bypass the round limit by opening another thread. A permitted stop transition remains possible without creating another conversation turn.

Run heartbeats independently of graph steps. Put blocking solver work in a bounded executor/subprocess so it cannot block lease renewal; apply a solver time limit. On failed renewal, stop new side effects and treat publication as forbidden. The backend still checks fencing because a cancellation cannot stop every in-flight request.

Distinguish business waiting from technical failure:

| Condition | Result |
| --- | --- |
| Proposal committed | Job succeeded; proposal awaits humans |
| Specific answer required | `WAITING_FOR_INPUT`, persisted question/checkpoint, no active lease |
| No useful progress or search budget exhausted | Paused/no-option outcome with safe explanation; no unbounded automatic retry |
| Provider timeout/rate limit | Bounded retry; eventually typed technical failure or explicitly labeled fallback |
| Superseded revision, permission change, or cancellation | Stop old generation; reconcile/start eligible current work |

### Operation keys and crash windows

Use stable keys derived from room, execution generation, logical step, round, conversation thread, recipient, and canonical request digest. The attempt token is an authorization fence, not part of the logical operation key. Reclaimed attempts must discover the same application operation rather than produce duplicates. Intentionally different work gets a new logical step/digest. Persist the selected utterance/result reference before delivery so recovery reuses committed wording rather than producing a second logical turn.

Persist an operation record before external work. Record provider run/dataset IDs promptly. On replay, look up the operation receipt and reuse its result or poll its existing run. Reserve budgets atomically across retries/attempts, counting ambiguous in-flight calls conservatively; checkpoint counters alone cannot prevent repeated spend after crashes.

| Crash point | Recovery |
| --- | --- |
| Message/proposal committed, checkpoint not saved | Replay stable key; backend returns original receipt |
| Checkpoint saved, job status update lost | Reconcile checkpoint and authoritative job/operation records before running |
| External request accepted, ID not recorded | Duplicate external call may occur; record this limitation and bound spend |
| Old attempt returns after another claim | Token/revision checks reject its writes |

Keep job status in SpacetimeDB; graph outcome is a proposed transition. Checkpoint persistence is not a transaction with application writes.

## 6. Human clarification and resume

Use a coordinator checkpoint thread such as `graph-v1:room:<id>:generation:<id>:coordinator`; use a separate owner scope for private evaluations. This graph schema version is independent of message schema v2. An attempt changes after reclaim; the execution generation/checkpoint thread remains stable for valid continuation. A superseding snapshot gets a new generation/thread. Conversation IDs use a distinct namespace such as `conversation:<id>`.

1. Idempotently create question records with owner, question kind/version, exact proposed action, expected snapshot and allowed answer schema. Kinds cover missing facts, participation confirmation, objection disclosure, concession consultation and broader exploration. Close the affected conversation thread as waiting for owner input.
2. Enter a dedicated `await_owner` node whose principal action is `interrupt({question_refs: ...})`. Its payload contains references, not private question text.
3. Once the interrupt checkpoint is durable, reconcile into `WAITING_FOR_INPUT` and release the lease. Do not count an interrupt as a technical failure.
4. An authenticated backend action stores the answer and makes the appropriate continuation eligible. Use backend answer refs as resume input, not browser-provided assertions of identity.
5. Claim a fresh attempt, reread authorization/current revisions, then resume the same thread with `Command(resume=...)` only if the checkpoint still matches. Recheck and load authoritative answers after the interrupt returns.
6. If an answer confirms a changed constraint, increment the input revision. If it changes preferences or action authority, increment the permission revision. Supersede the old generation and start a fresh snapshot in either case. An answer changing neither may resume the existing generation. Reject obsolete question versions and wrong owners. Consultation or disclosure permission is never final proposal acceptance.

The interrupted node starts again on resume; keep pre-interrupt work replay-safe and do not wrap the interrupt in a broad exception handler. These behaviors are documented by LangGraph. [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

Handle the **early-answer race**: an owner can answer after question creation but before parking finishes. The park transaction must inspect question state; reconciliation must schedule a ready continuation once the checkpoint exists. A restart scan must also recover an active job whose checkpoint is already interrupted. Never rely on receiving one particular notification.

If several questions are open, resume after all required answers are ready for the initial version. Any answer that supersedes the snapshot invalidates that waiting generation immediately. Independent research while waiting is optional later work.

**Final human consent should not keep this planning graph suspended.** Publishing ends the job and opens backend review records. Approval/decline/withdrawal happens through human-only reducers; a change that needs more computation creates new work.

Use an in-memory saver for isolated tests and a file-backed SQLite saver on a persistent volume for single-worker restart testing. Checkpointers retain thread execution state; an in-memory saver does not survive process restarts. Verify the selected persistent adapter and pin compatible versions before deployment. [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

## 7. Implementation layout and dependency boundaries

```text
worker/
  pyproject.toml
  src/tandem_worker/
    cli.py                    # run-one, reconcile, local scenario harness
    runner.py                 # claim, heartbeat, resume, result reconciliation
    contracts/                # snapshots, messages, commands, candidates, outcomes
    graphs/coordinator.py
    graphs/personal.py
    nodes/                    # small testable operations and routers
    policy/                   # participation gates, context projection, tool scope, utterance/evidence checks
    ports.py                  # Python Protocol interfaces
    adapters/backend.py
    adapters/claude.py
    adapters/apify.py
    adapters/solver.py
    checkpoints.py
  tests/
    fakes/                    # backend, model, research, solver, fake clock
    fixtures/                 # versioned JSON; synthetic identities only
    unit/
    workflow/
    recovery/
    contract/
    live/
```

Inject `BackendPort`, `ModelPort`, `ResearchPort`, `SolverPort`, `ValidatorPort`, `Clock` and checkpointer. Graph nodes never import frontend code or connect directly to providers behind the adapters.

Use Python 3.11+ as a proposed baseline; pin the tested interpreter and dependency lock. Expected packages: `langgraph`, compatible SQLite checkpoint adapter, `pydantic`, a Claude SDK or LangChain integration (choose one), backend HTTP client, `ortools`; development: `pytest`, `pytest-asyncio` if async, and `hypothesis` for invariant exploration. The worker does not require FastAPI.

Keep checkpointer connections open for the graph execution lifetime. Serialize work per thread; never concurrently invoke two attempts on the same thread even in one process. Do not assume a shared SQLite file makes a multi-worker deployment safe.

## 8. Independent test harness

Build the harness before real integrations. It should run with no API keys, network, frontend, dispatcher, or deployed SpacetimeDB.

| Fake | Behaviors it must model |
| --- | --- |
| Backend | Atomic claims, expiring leases/fencing, snapshots, receipts, preference revisions, questions, conversation threads/budgets and private/shared views |
| Model | Separate scripted decision/voice calls with captured inputs; malformed JSON, invalid claims/refs, timeout and injection attempts |
| Research | `INITIAL` vs `TARGETED` datasets, run IDs, missing fields, schema errors, deterministic additions |
| Solver | Scripted status/result for orchestration tests; separate integration suite uses the real solver |
| Validator | Independent validity report; can deliberately reject solver output |
| Clock | Controllable UTC/monotonic time for expiry, backoff and budgets; no long real sleeps |

The fake backend must enforce invariants rather than always returning success. It cannot establish that real SpacetimeDB authorization works: run the same backend contract tests against the deployed module later, with separate identities.

### Canonical four-person scenario

Use synthetic flight-only fixture terms with all modeled required costs included, known currency/timezones, provenance, and explicit `FIXTURE` mode. Include confirmed participation settings and synthetic private canaries for every owner. Define the demo scope so missing lodging costs cannot accidentally be treated as zero.

- P1 hard cap: 50,000 cents; P2/P3/P4: 65,000 cents each. All four require arrivals within two hours.
- Initial candidates contain compatible arrivals, but P1's cheapest complete itinerary is 52,000 cents. The solver returns infeasible for this checked set.
- A permitted targeted search adds P1 option B at 48,000 cents with a compatible arrival. All other participants have complete 45,000-cent options.
- All agents receive separately addressed schema-v2 messages for the resulting draft and return eligible assessments under their own scopes, with checked utterances. Validation passes. Exactly one proposal is published; zero human consent records exist.
- P1 changes their cap to 46,000 cents. Old publication/approval requests fail against the current revision. A new generation may search for option C at 44,000 cents; changed terms require fresh review.

Also create a distinct agent-objection scenario: a draft is numerically feasible, but P2's agent identifies missing evidence for a confirmed baggage requirement. After any required disclosure consultation, its typed objection opens a permitted follow-up with P4's agent about a compatible alternative. P4's actual reply triggers targeted research; the new evidence/option changes the next solve. Record the causal message/thread refs and require fresh assessments for the new draft. This proves recipient replies influence the workflow rather than merely narrate a centralized decision.

Add a participation comparison fixture: owners with identical constraints and different confirmed settings encounter the same blocker and potential concession. Assert different objection routing, consultation timing and reminder eligibility without any difference in hard-constraint enforcement. Also exercise refusal to disclose: the group sees only permitted pending status and the unresolved blocker still prevents publication.

### Required tests and observable assertions

| ID | Scenario | Pass condition |
| --- | --- | --- |
| U01 | Invalid envelope, mismatched room/owner, obsolete revision | Rejected before model/tool invocation |
| U02 | Duplicate key with same/different body | Original receipt / typed conflict respectively |
| U03 | Private-context canary per owner | Canary absent from other prompts, mediator inputs, shared outputs, logs, and coordinator checkpoints |
| U04 | Unsupported tools, fabricated evidence refs, malformed assessments | Bounded correction then safe failure; no unauthorized operation |
| U05 | Missing fare fields, taxes, timezone or cost basis | Unknown/quarantined; never converted to zero or approval-ready fact |
| U06 | Identical repeated blocker/candidates | No-progress route stops within configured budget |
| U07 | Unsupported message schema | Active processing rejects without mutation; history retains its original interpretation |
| P01 | Preferences not explicitly confirmed | No group-visible representation; authorized independent research can continue |
| P02 | Identical constraints, different Speak up settings | Enabled produces a neutral permitted objection; disabled asks privately first |
| P03 | Owner refuses objection disclosure | No group-visible objection or private reason; underlying blocker still prevents publication |
| P04 | Ask me first on/off within authorized range | Enabled consults before a visible concession; disabled permits exploration without granting final acceptance |
| P05 | Exploration exceeds authorized range | Consult regardless of Ask me first setting; no silent expansion or hard-limit change |
| P06 | Give me time on | No reminders or urgency; decide-later available; elapsed time never creates consent |
| P07 | Disclosure or consultation answer accepted | Authorizes only its exact action; no human consent record, spending authority or relaxed constraint |
| P08 | Confirmed preference edit | Permission revision changes and affected pending output/checkpoints are invalidated |
| V01 | Private decision vs room-voice prompt capture | Room voice receives only permitted act/facts; private canaries absent from it, other agents and coordinator state |
| V02 | Unsupported price, flight, concession, commitment or contradictory wording | Reject, regenerate once, then safe deterministic template if still invalid |
| V03 | Fabricated claim refs or valid refs attached to false wording | Whole utterance rejected; refs alone do not establish validity |
| V04 | Owner-private message or rejected utterance | Absent from Live Negotiation/shared logs; only authorized owner projection receives private content |
| V05 | Permission revoked during voice generation | No delivery of stale wording; rebuild from current permitted context |
| T01 | Agent B receives and replies in a thread | B uses its own context; its reply ref appears in downstream research, validation or selected-draft change |
| T02 | Broadcast, outsider, closed thread, stale reply or removed member | Rejected before delivery and cannot influence the current draft |
| T03 | Thread/round limit or polite repetition | Thread closes and unresolved work pauses; new threads cannot bypass round budget |
| T04 | Duplicate/replayed message and voice regeneration | One committed turn charge; model retries count against spend; restart preserves both budgets |
| T05 | New draft after re-solve | All required agents reassess current terms; old eligibility alone cannot satisfy fan-in |
| T06 | Conversation closes while graph is interrupted | Correct graph checkpoint resumes; conversation and graph identifiers are never confused |
| G01 | All eligible with complete fixtures | One valid proposal, all required reply receipts, no human approvals |
| G02 | Initially infeasible; targeted candidate added | Recorded blocker → targeted request → candidate version changes → re-solve succeeds |
| G03 | Personal-agent objection | Reply causally changes research/clarification/selection; unresolved objection blocks publication |
| G04 | Missing owner fact | Private versioned question + interrupt + waiting job; no active lease or invented answer |
| G05 | Valid non-planning answer | Fresh attempt resumes same thread; no repeated research/message effects |
| G06 | Answer changes confirmed input | New revision/generation; old checkpoint never publishes |
| G07 | Missing/late agent reply | Never default eligible; retry or pause within deadline |
| G08 | Validator rejects solver result | No proposal published, even if every agent says eligible |
| G09 | Solver `UNKNOWN`, invalid model, or no candidates | Accurate distinct outcome; no claim of universal infeasibility |
| G10 | Injection in scraped content or agent payload | Content cannot alter tool scope, constraints, consent, or effective audience |
| G11 | Initial discovery and targeted research order | Initial research precedes initial solve; targeted results enter normalization and a new solve |
| W01 | Two runners or duplicate notifications | Only one current claim and one effective proposal |
| W02 | Lease expires, replacement attempt wins | Old attempt's messages/result/publication rejected |
| W03 | Input, membership, permission changes mid-run | Current writes rejected; revoked private context not reused |
| W04 | Cancel while provider call is in flight | Late result cannot publish or restart cancelled work |
| W05 | Lost wake-up / dispatcher unavailable | Startup/reconciliation scan recovers eligible work |
| W06 | Worker blocked in solver/provider | Heartbeats continue or lease loss safely stops writes |
| R01 | Kill after message/proposal commit before checkpoint | Restart reuses receipt; one logical effect |
| R02 | Kill after provider run ID stored | Poll same run; no second Actor submission |
| R03 | Kill before provider ID persisted | Ambiguous operation recorded; bounded duplicate risk, no exactly-once assertion |
| R04 | SQLite pause → terminate process → fresh process | Same waiting question recovered; valid answer resumes successfully |
| R05 | Answer before parking, or crash during parking | Reconciliation schedules continuation once; no stranded waiting job |
| R06 | Retry/reclaim near spend limit | Durable reservations prevent resets/budget bypass |
| C01 | Worker credential attempts human approval | Real backend denies it |
| C02 | P1 reads P2 private view or forges agent sender | Real backend denies before retrieval/write |
| C03 | Stale proposal, wrong digest, missing one acceptance | Real backend never declares agreement |
| C04 | Same shared backend test cases on fake and real adapter | Equivalent typed results and invariant behavior |
| C05 | Real message path with permission edit or duplicate delivery | Server checks current policy/audience; one message, thread advance and turn charge; no unauthorized projection |
| C06 | Backend receives worker-checked message without model access | No backend model call; missing/stale/untrusted metadata or digest mismatch rejected using structured checks; semantic mismatch fixtures are caught in worker tests V02–V03 |

Use a fresh graph/checkpointer per ordinary workflow test. Test node functions directly for local transformations, but use compiled graphs for routing, interrupts and checkpoints: direct node invocation bypasses checkpoint behavior. [LangGraph testing](https://docs.langchain.com/oss/python/langgraph/test)

Property/state-machine tests should generate claim, expire, edit, revoke, answer, retry and publish sequences. Assert after every operation: no obsolete attempt changes current state; no invalid hard constraint reaches a published proposal; no unauthorized output contains a private canary; no worker action creates consent. Human agreement invariants also require the real backend suite.

### Proposed commands once the harness exists

```bash
cd worker
python -m pytest tests/unit tests/workflow -q
python -m pytest tests/recovery -q
python -m pytest tests/contract -m backend -q
python -m pytest tests/live -m live -q
python -m tandem_worker.cli scenario targeted-budget --mode fixture
python -m tandem_worker.cli scenario clarification-restart --mode fixture
```

Register markers and skip external suites unless explicitly configured. `backend` uses a disposable database and synthetic identities; `live` uses real credentials and capped spend. The default test command must fail on accidental network access. CLI flags above are proposed acceptance requirements for the harness, not existing commands.

Each scenario should export an allowlisted event report: job/generation, node transitions, conversation-thread/message/receipt IDs, purposes/run refs, candidate counts, solver/validator statuses, proposal ref, wait reason, turn usage, utterance validation/fallback codes and cost totals. Use IDs and typed codes; omit raw private prompts. Assert semantic outcomes and bounded call counts rather than exact model wording or the ordering of concurrent replies.

Measure decision and room-voice model calls separately, including retries, fallback frequency, compute time, human wait time and total cost. Privacy and consent checks are release gates. Do not interpret fewer rounds or faster acceptance as evidence of better representation; unresolved disagreement and voluntary decline remain valid outcomes.

## 9. Build order and completion gates

1. **Contracts and offline workflow:** Schema-v2 Pydantic models, agreed backend Protocol, transactional fake backend, confirmed participation fixtures, typed threaded dialogue, scripted separate decision/voice responses, deterministic CLI and one independently checked proposal. Gate: G01, U01–U04, U07, V01–V04 and T01–T02 pass offline with zero human approvals.
2. **Policy and recovery:** Participation/consultation gates, heartbeat, operation receipts, cancellation, durable turn/spend budgets, SQLite, coordinator interrupt/resume and startup reconciliation. Gate: P01–P08, V05, T03–T06, G04–G06, W01–W06 and R01–R06 pass, including a fresh-process restart. The distinct agent-reply scenario must causally change the fixture workflow.
3. **Real solver and backend:** Ronit's solver and independent validator adapters, authorized views/subscriptions, question handling, backend canonical digest checks, fenced publication and dispatcher wake-up. Gate: G02, G03, G08, G09, G11 pass with real computation and synthetic datasets; C01–C06 and duplicate/lost-notification tests pass against a disposable module, with no backend model access. A scripted solver or fake backend alone does not satisfy this gate.
4. **Claude and Apify:** Replace scripted decisions and room voice with separate scoped Claude calls and adversarial wording checks; keep deterministic template fallback. Pin and validate one flight Actor, persist run IDs, test initial discovery and a real targeted follow-up. Gate: provider source/mode and purpose tracked, re-search changes usable candidates, privacy/utterance checks hold, and failures remain unknown or explicitly use fixtures. Report decision/voice latency, retries, validation failures, fallback rate and cost separately.

You can complete gates 1–2 independently with fakes and local persistence. Gate 3 needs the solver implementation and deployed backend; it verifies infrastructure guarantees the fake cannot prove. Gate 4 needs tested provider access. Credentials, exact Actor selection, fairness-policy sign-off, backend/dispatcher ownership and deployment retention remain explicit integration dependencies rather than hidden prerequisites for offline tests. The CLI and its flags remain proposed acceptance requirements until implemented.

The initial workflow is ready for a team demo when it can (a) produce a checked proposal from fixtures with no fabricated consent, (b) change its outcome because of a recipient agent's real reply, (c) visibly honor different confirmed participation settings, (d) show only validated room dialogue without private caps/reasons, (e) park and resume after a process restart, and (f) reject an obsolete attempt after an input or permission edit. Human acceptance and UI integration remain separately verified backend/product behaviors. Live discovery and live model claims require gate 4 evidence, not fixture success alone.
