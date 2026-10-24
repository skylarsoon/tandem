# Tandem backend contract (SpacetimeDB module)

For: Skylar (worker / LangGraph), Anna (frontend), and anyone calling the backend.
Owner: Ronit. Source of truth: `spacetimedb/src/`. SpacetimeDB **2.13.0**. Demo guide and talk track: `demo.md`.

Verified by:
- `spacetimedb/scripts/test.sh`: 63 tests of the module's rules
- `scripts/backend-integration.sh`: 17 tests running the worker against the real module through `SpacetimeBackend`

Every JSON example in `examples/` was captured from a real run. Request examples use snake_case argument names, as you pass them to the Python clients (which convert the top level to camelCase on the wire).

---

## 1. How to talk to it

| Who | How |
|---|---|
| Python worker | `worker/src/tandem_worker/adapters/spacetime.py` (`SpacetimeBackend`, implements `BackendPort`) over HTTP: `POST /v1/database/<db>/call/<name>` for reducers/procedures, `POST /v1/database/<db>/sql` for views. `Authorization: Bearer <token>`. |
| Browser (Next.js) | TypeScript SDK (`spacetimedb` npm), generated bindings, subscribe to views. |
| Dispatcher | TypeScript SDK, subscribes to `worker_pending_jobs` (§8). |

Connection details:
- **Shared (everyone uses this):** Maincloud, URL `https://maincloud.spacetimedb.com`, database `tandem-dev`. Dashboard: https://spacetimedb.com/tandem-dev.
  - The browser connects with `withUri("https://maincloud.spacetimedb.com").withDatabaseName("tandem-dev")`.
  - The worker and dispatcher accounts are registered. Their tokens are in Ronit's git-ignored `worker/.env` and `dispatcher/.env`; ask him for them privately, never in git or chat.
- **Republishing** (owner only): `spacetime publish --server maincloud --module-path spacetimedb tandem-dev`. A breaking schema change needs `--break-clients` or a data wipe, so tell the team first.
- **Local, for development:** `spacetime start`, then `spacetime publish --server local tandem-dev` from the repo root.
- **Never run the test suites against the shared database.** The test scripts start their own throwaway local server.

### Encoding rules (tested)

1. **Top-level argument names are camelCase** (`jobId`, `requestKey`), exactly as declared. **Fields inside nested objects are snake_case** (`open_thread.topic_ref`, `candidates[].candidate_ref`). **View columns are snake_case.** Both clients (`StdbHttp` in the adapter, `stdb_client.py` in the tests) take snake_case and convert the top level.
2. Options: `null` for none, `{"some": value}` for a value. A bare value is rejected.
3. Identity arguments: `{"__identity__": "0x<64 hex>"}`. The worker never needs to send one.
4. u64/u32 are JSON numbers; ids stay far below 2^53.
5. SQL rows come back as positional arrays plus a schema; the clients decode them to dicts. In raw rows, options are `[0, value]` / `[1, []]`, identities `["0x…"]`, timestamps `[micros]`.

### Results and errors

| Call type | Success | Business failure |
|---|---|---|
| **Reducer** | HTTP 200, empty body | HTTP **530**, body `CODE: detail` (`examples/error.reducer.json`) |
| **Procedure** (`claim_job`, `load_execution_context`, `publish_proposal`, `check_lease`, `begin_operation`) | HTTP 200, a JSON-encoded string `{"ok": true, ...}` | HTTP 200 with `{"ok": false, "code", "detail"}` (`examples/error.procedure.json`) |
| Bad argument shape | HTTP 400 with a parser message | |
| **Outcome unknown** | HTTP 504 (durability wait timed out) | Retry with the **same request key** |

Error codes:
- **Access and lookup:** `NOT_AUTHORIZED`, `NOT_A_MEMBER`, `NOT_FOUND`
- **Bad input or state:** `INVALID_ARGUMENT`, `INVALID_STATE`
- **Jobs and fencing:** `JOB_NOT_ELIGIBLE`, `LEASE_LOST`, `STALE_SNAPSHOT`, `KEY_BODY_MISMATCH`, `CALL_BUDGET_EXHAUSTED`
- **Questions and permissions:** `QUESTION_SUPERSEDED`, `PREFERENCES_UNCONFIRMED`, `CONSULTATION_REQUIRED`
- **Messages:** `THREAD_CLOSED`, `TURN_BUDGET_EXHAUSTED`, `INVALID_REPLY`, `INVALID_EVIDENCE_REF`, `UTTERANCE_PAYLOAD_MISMATCH`, `INVALID_VALIDATION_METADATA`, `PAYLOAD_TOO_LARGE`, `UNSUPPORTED_SCHEMA`, `PRIVATE_CONTENT_DETECTED`
- **Publication:** `INVALID_PROPOSAL`, `INVALID_COVERAGE`

---

## 2. Core rules the backend enforces

- **Generation = job id.** A confirmed change to a room's constraints, settings, membership, participation preferences, or a granted permission bumps a revision (`input`, `membership` or `permission`). That supersedes every open PLAN job with its questions and threads, plus proposals awaiting review (consents become `STALE`), and queues a fresh PLAN job if the room has constraints.
- **Attempt = fencing token.** Every worker write checks the allowlisted caller, `CLAIMED` under this exact attempt by this caller, an unexpired lease, and (for PLAN jobs) all three room revisions equal to the job's snapshot. Otherwise `LEASE_LOST` or `STALE_SNAPSHOT`.
- **Request keys make writes idempotent.** Same key and same body returns the original result; same key with a different body is `KEY_BODY_MISMATCH`. Keys are global across the database (the adapter scopes claim keys by room). The digest ignores `attempt`, so a re-claimed attempt (even under another registered worker identity) replaying a logical operation gets the original receipt.
- **External calls are ledgered.** `begin_operation` / `complete_operation` record model, research and local operations per job, with durable call budgets (§3).
- **Only humans approve.** Worker identities are refused on every human operation (C01).
- **Private data never reaches another person's browser.** All 29 tables are private; each view filters by caller (C02).

---

## 3. Worker operations (agreed names)

All take `jobId` + `attempt` and run the checks in §2 unless noted. "Key" = `requestKey` (or `messageKey`).

| Operation | Type | Arguments | Behavior |
|---|---|---|---|
| `claim_job` | procedure | `jobId?`, `leaseSeconds` (2–300), `requestKey` | Claims that job (or the oldest pending if `null`). Returns `job_id`, `generation`, `room_id`, `kind`, `attempt`, revisions, `lease_expires_at_micros`, `graph_thread_ref`. Not claimable → `JOB_NOT_ELIGIBLE`. A lapsed lease is reclaimable. |
| `load_execution_context` | procedure | `jobId`, `attempt` | One consistent snapshot (`examples/load_execution_context.response.json`). See the list below the table. |
| `check_lease` | procedure | `jobId`, `attempt` | Side-effect-free fence check; returns the lease fields or the failure code. |
| `renew_lease` | reducer | `jobId`, `attempt`, `leaseSeconds` | Extends the current attempt only. Heartbeat every ~15 s with a 60 s lease. |
| `begin_operation` | procedure | `jobId`, `attempt`, `opKey`, `requestDigest`, `kind` (`model`/`research`/`local`), `limit` | Returns `{"status":"DONE","result_json"}` if completed before, else reserves and returns `RESERVED`. Model/research reservations count against `limit` per job (`CALL_BUDGET_EXHAUSTED`). An unfinished earlier reservation reserves (and counts) again. Different digest for the same key → `KEY_BODY_MISMATCH`. |
| `complete_operation` | reducer | `jobId`, `attempt`, `opKey`, `resultJson` | Stores the result. Repeating with the same result is a no-op. |
| `submit_agent_message` | reducer | see §5 | Durable message + thread advance + turn charge, atomically. Key: `messageKey`. |
| `record_message_processed` | reducer | `jobId`, `attempt`, `messageId`, `recipientAgentId`, `replyIds[]` | Consumer receipt. Same replies again is a no-op; different replies → `KEY_BODY_MISMATCH`; wrong recipient → `NOT_AUTHORIZED`. |
| `close_threads` | reducer | `jobId`, `attempt`, `reason` | Closes the job's open threads (e.g. `RESEARCH_REQUIRED`, `NO_PROGRESS`). |
| `ask_owner` | reducer | `jobId`, `attempt`, `requestKey`, `memberId`, `kind`, `actionJson` (`{"action_ref": "<draft>"}`), `prompt`, `answerSchemaJson`, `threadId?` | Private versioned question (v1). Kinds: `MISSING_FACT`, `PARTICIPATION_CONFIRMATION`, `OBJECTION_DISCLOSURE`, `CONCESSION_CONSULTATION`, `BROADER_EXPLORATION`. `threadId` closes that thread with `WAITING_FOR_OWNER`. Context lists the question under its `request_key`. |
| `park_job` | reducer | `jobId`, `attempt`, `requestKey`, `graphThreadRef`, `questionIds[]` | `WAITING_FOR_INPUT`, lease released, `graph_thread_ref` stored, open threads closed (`NEEDS_OWNER_INPUT`). If every listed question is already answered, the job goes straight back to `PENDING`. |
| `schedule_resume` | (built in) | | No call needed: `answer_question` and `park_job` requeue the job once all its questions are answered. Resume with a new `claim_job`; `graph_thread_ref` comes back in the claim. |
| `publish_proposal` | procedure | `jobId`, `attempt`, `requestKey`, `stage`, `termsJson`, `sharedSummaryJson`, `validationJson`, `evidenceRefs[]`, `affectedMemberIds[]` (empty = all active), `impacts[{member_id, impact_json}]`, `burdens[{member_id, task_id, burden_micros}]` (may be empty), `selectedCandidateRefs[]` | See "Publication" below. Returns `proposal_id`, `version`, `terms_digest`; retrying the same key returns the same proposal. Stages: `UNDERSTAND`, `GOALS_DATES`, `TRAVEL`, `STAY_EXPERIENCES`, `COMPLETE_PLAN`. |
| `finish_job` | reducer | `jobId`, `attempt`, `requestKey`, `outcome`, `outcomeJson` | Terminal `FINISHED` (e.g. `NO_FEASIBLE_CANDIDATE`, `OWNER_DECLINED`); closes threads. |
| `fail_job` | reducer | `jobId`, `attempt`, `requestKey`, `retryable`, `code`, `detail` | Retryable → `PENDING`, otherwise `FAILED`; closes threads. Can't touch a `SUCCEEDED` job. |

**`load_execution_context` returns:**
- the job and the room, including its `settings`
- `mediator_agent_id`
- members, each with confirmed preferences (including `reminders_opted_in`), their revision, and a derived `tool_policy`
- constraints, **including private rationale**: the worker is the trusted processor and must scope this per agent
- questions with their answers
- grants that are still valid (with `action_ref`)
- the job's threads and messages, sorted by id
- candidates with the revisions they were recorded under
- open proposals
- `compromise_history`: `{"<member_id>": H_i}` for every active member, the sum of their ACTIVE approved compromises in this room (0..n, `0` if none). This is the optimizer's `compromise_history` as is. See §6b.

**Publication.** Everything happens in one transaction:
1. If `selectedCandidateRefs` is non-empty, the trip is re-checked (policy `minimax_v1`). A trip is one `FLIGHT` per active member plus exactly one of each shared part the room's `components` setting lists (`STAY`, `TRANSFER`), whose cost is split equally.
   - **Every candidate's `facts_json`:** `category`, `total_cents`, `observed_at` and `valid_until` (epoch seconds), and `cost_basis` `COMPLETE_FLIGHT_TOTAL` / `COMPLETE_STAY_TOTAL` / `COMPLETE_TRANSFER_TOTAL` with no `missing_critical_fields`. An observed price with unknown taxes or fees can't be published.
   - **Flights** also carry `owner_member_id`, `arrival_epoch` and `baggage_included`. **Shared items** carry `capacity` (guests or seats), which must fit every active member.
   - **Hard limits:** each HARD `flight_budget_total`; each HARD `trip_budget_total` against own flight + equal share (compared as `n × flight + shared ≤ n × cap`, exact in cents); baggage where `baggage_required` is true; arrivals within `arrival_overlap_seconds`; every traveller's preferences confirmed; prices not expired.
   - Room *preferences* that owners can set aside by consent (`own_bedroom`, `stay_min_rating`) are applied by the worker with consultation grants, not here.
   - Failures return `INVALID_PROPOSAL` / `INVALID_COVERAGE` / `INVALID_EVIDENCE_REF`.
2. **What the worker publishes:**
   - `terms_json` = `{"scope": "TRIP" | "FLIGHTS_ONLY", "policy": "minimax_v1", "selected": [candidate…]}`.
   - `shared_summary_json` = `{"scope", "travellers", "text", "group", "suggestions"}`. `text` is a room-safe paragraph (total, arrival window, what landing together costs, how many pay extra, stay, transfer, equal share, room-needs premium, activity/dinner ideas). `group` is the same as numbers. `suggestions` are activity and dinner ideas with *estimated* per-person prices, never part of what people approve.
   - Each `impact_json` is private to its owner: `{owner, candidate, price_cents, flight_cents, share_cents, cheapest_alone_cents, extra_cents, reason: NONE | ARRIVAL_WINDOW | SHARED_CHOICE, text}`.
2. The stage's previous proposal is superseded.
3. Immutable terms are stored with `terms_digest = sha256(termsJson bytes)`. **Send canonical JSON.**
4. Burdens are stored with the proposal (see §6b), then private impacts and `PENDING` consents are created, reminders scheduled, and the milestone set to `IN_REVIEW`.
5. Threads close (`RESOLVED`) and the job becomes `SUCCEEDED`.

**Other worker writes** (same checks):

| Operation | Arguments | Purpose |
|---|---|---|
| `record_candidates` | `requestKey`, `candidates[{candidate_ref, candidate_set_version, category, mode, source_url, observed_at, facts_json, missing_fields}]` | `category`: `FLIGHT` (one traveller's), `STAY` / `TRANSFER` (shared), `ACTIVITY` / `DINNER` (ideas, estimated per-person prices). Upsert sourced options, stamped with the job's input and membership revisions. `mode`: `APIFY_OBSERVED`, `PROVIDER_VERIFIED`, `CACHED_WITH_TIMESTAMP`, `SANDBOX`, `FIXTURE`. |
| `reply_to_owner` | `requestKey`, `memberId`, `body` | Agent → owner message in the private My Agent chat. |
| `propose_constraint_patch` | `requestKey`, `memberId`, `patchJson`, `sourceMessageId?` | Tentative extraction for the owner to confirm. Shape: `{"key", "value", "unit", "hardness", "authorized_range"?, "private_rationale"?}`. |
| `propose_participation_preferences` | `requestKey`, `memberId`, `speakUpForMe`, `askMeFirst`, `giveMeTime` | Suggested settings; stay unconfirmed and grant nothing. Refused once confirmed. |
| `emit_event` | `requestKey`, `type`, `memberId?`, `payloadJson` | Live Negotiation progress. No `memberId` = room-wide (private-text checked); with it = that owner only. |
| `set_agent_activity` | `agentId`, `activity`, `threadId?` | Live avatar state: `IDLE`, `THINKING`, `SEARCHING`, `CHECKING`, `WRITING`, `WAITING_FOR_OWNER`. Last write wins; shown as `IDLE` once the job stops running. |

Job statuses: `PENDING` → `CLAIMED` → `WAITING_FOR_INPUT` / `SUCCEEDED` / `FINISHED` / `FAILED`, or `SUPERSEDED` / `CANCELLED`. Kinds: `PLAN` (snapshot-bound) and `INTERPRET` (owner messages and review follow-ups; not superseded by revisions, not run by the planning worker yet).

---

## 4. Human operations (browser)

| Operation | Arguments | Notes |
|---|---|---|
| `create_room` | `goal`, `displayName` | Creates the room (settings `{"arrival_overlap_seconds": 7200}`), the mediator agent, your membership and personal agent, five milestones, and an invite code. |
| `join_room` | `inviteCode`, `displayName` | Bumps the membership revision. |
| `set_room_settings` | `roomId`, `settingsJson` | Organizer only. Shared rules: `arrival_overlap_seconds`, `components` (`["FLIGHT"]` by default; `["FLIGHT","STAY","TRANSFER"]` plans the whole trip) and `people_per_bedroom` (1–8, default 2: how many travellers without an own-room request share a bedroom). New generation. |
| `upsert_profile` | `displayName`, `avatarRef`, `selfDescription`, `priorityRanking[]` | |
| `confirm_participation_preferences` | `speakUpForMe`, `askMeFirst`, `giveMeTime`, `remindersOptedIn` | Defaults before confirmation: off / on / on / off, **unconfirmed**. Confirming or editing bumps the permission revision in all your rooms (new generation). |
| `set_constraint` | `roomId`, `key`, `valueJson`, `unit`, `hardness` (`HARD`/`SOFT`), `authorizedRangeJson?`, `privateRationale?` | Form entry = confirmed. New generation. The room sees only "A participant updated their requirements". Keys the worker understands: `flight_budget_total` (USD cents; SOFT budgets use `authorized_range.max` as the cap), `trip_budget_total` (USD cents, HARD: own flight + equal share of stay and transfer), `baggage_required` (`true`), `own_bedroom` (`true`) and `stay_min_rating` (0–5). |
| `confirm_constraint_patch` / `reject_constraint_patch` | `patchId` | Confirming applies it like `set_constraint`. |
| `submit_private_message` | `roomId`, `body` | Message to your own agent; queues an `INTERPRET` job. |
| `answer_question` | `questionId`, `version`, `answerJson` | See §6. Wrong owner → `NOT_FOUND`; not open or wrong version → `QUESTION_SUPERSEDED`. |
| `respond_to_proposal` | `proposalId`, `version`, `termsDigest`, `response`, `note?` | `ACCEPT`, `NOT_COMFORTABLE`, `ASK_MY_AGENT`, `SUGGEST_ALTERNATIVE`, `DECIDE_LATER`. Rejected with `STALE_SNAPSHOT` if the version, digest or room revisions changed. Agreement is recomputed in the same transaction: **every affected member must have ACCEPTED these exact terms**. Non-accept responses message your agent. |
| `withdraw_consent` | `proposalId`, `note?` | Reopens an AGREED proposal and its milestone. |
| `set_trip_description` | `roomId`, `description` | Organizer only. Their own words about the trip; their agent proposes a structured brief (`room_trip_brief`, state PROPOSED). |
| `confirm_trip_brief` | `roomId`, `revision` | Organizer only. Writes destination, airport, dates and components into the room settings, then starts everyone's onboarding (and anyone who joins later). Re-confirming a changed brief reopens only the affected topics. |
| `confirm_origin` | `roomId`, `patchId` | The one-tap "Flying from SFO, right?". Confirms only an `origin_airport` patch, so searches can start early. |
| `confirm_intake` | `roomId`, `acceptedPatchIds[]`, `speakUpForMe?`, `askMeFirst?`, `giveMeTime?`, `remindersOptedIn?` | Confirms the onboarding summary in one go. Accepted PENDING patches become constraints, the rest are rejected, settings are confirmed, and the room replans once. Needs `origin_airport` and a budget or `no_budget_limit` (`INTAKE_INCOMPLETE`) and confirmed settings (`PREFERENCES_UNCONFIRMED`). |

**Presence** is automatic: an SDK connection marks you online, and closing your last tab marks you offline. HTTP calls don't count.

**Review reminders** are opt-in. After publication, reviewers who confirmed preferences with `remindersOptedIn` and **without** "give me time" get one private `REVIEW_REMINDER` event in `room_events`, and only if they still haven't responded. The delay defaults to 10 minutes; the database owner changes it with `set_reminder_delay(seconds)`. Deciding later is always available.

---

## 5. `submit_agent_message` (schema v2)

Full request: `examples/submit_agent_message.request.json`.

Arguments: `jobId`, `attempt`, `messageKey`, `schemaVersion` (must be **2**), `roundId`, exactly one of `threadId` / `openThread{topic_ref, participant_agent_ids, turn_budget?, successor_of?}`, `senderAgentId`, `recipientAgentId`, `type`, `replyTo?`, `draftRef?`, `proposalId?`, `proposalVersion?`, `payloadJson` (an object; the gates read `draft_ref`, `candidate_refs`, `concession`, `exceeds_exploration`), `utterance`, `claimRefs[]`, `evidenceRefs[]`, `audience` (`ROOM` / `OWNER_PRIVATE` / `INTERNAL`), `audienceMemberId?`, `sanitizationStatus` (`VALIDATED` / `TEMPLATE_FALLBACK`), `validationDigest?`.

Checks, in one transaction (nothing is written if any fail):
1. **Schema and size:** `schemaVersion` must be 2 (`UNSUPPORTED_SCHEMA`); utterance ≤ 4096 characters (`PAYLOAD_TOO_LARGE`).
2. **Validation digest:** if given, it must equal the SHA-256 of Python-canonical `{"type", "utterance", "payload", "claim_refs", "evidence_refs"}`, i.e. `json.dumps(sort_keys=True, separators=(",", ":"))` with ASCII escaping. Otherwise `INVALID_VALIDATION_METADATA`.
3. **Agents:** sender and recipient must be in the job's room, and a personal agent's owner must still be an active member. One explicit recipient, never the sender (`NOT_AUTHORIZED`).
4. **Evidence:** every evidence ref and payload `candidate_refs` entry must be recorded in this room (`INVALID_EVIDENCE_REF`).
5. **Threads:**
   - `openThread` must list both sender and recipient, all in the room (`NOT_AUTHORIZED`), with a budget of at most 6.
   - An existing thread must belong to this job and round, be open, and include both agents.
   - An older-generation thread → `THREAD_CLOSED`; a thread closed by its budget → `TURN_BUDGET_EXHAUSTED`.
   - `replyTo` must be a message to the sender in the same thread (`INVALID_REPLY`).
6. **Round budget:** 24 turns per `(job, roundId)`. Opening more threads doesn't bypass it.
7. **ROOM audience:**
   - a non-empty utterance and a valid `sanitizationStatus`
   - **every** personal-agent participant's owner has confirmed preferences (`PREFERENCES_UNCONFIRMED`)
   - the sender's owner holds each required grant for `payload.draft_ref` (`CONSULTATION_REQUIRED`):
     - `OBJECT` without speak-up → `OBJECTION_DISCLOSURE`
     - `concession` with ask-me-first → `CONCESSION_CONSULTATION`
     - `exceeds_exploration` → `BROADER_EXPLORATION`
   - no owner's private rationale appears in the text (case-insensitive, 6+ characters; `PRIVATE_CONTENT_DETECTED`)
   - each claim ref is `act:<TYPE>` for this message's type, a recorded candidate, `msg:<id>`, `proposal:<id>` or `draft:<ref>`, optionally citing one field as `<ref>#<field>` (e.g. `cand_1#price`, `draft:d1#arrival_window`; the record must resolve) (`UTTERANCE_PAYLOAD_MISMATCH`)
8. **OWNER_PRIVATE:** a personal agent may only speak to its own owner; the mediator must name `audienceMemberId`.
9. **Turn charge:** one turn is charged to the thread and the round. The thread closes at its budget (`TURN_BUDGET`); all of the round's open threads close at 24 (`ROUND_BUDGET`). Replays don't charge.

**What the backend can't check:** whether the wording is true or faithful to the typed act. That's the worker's room-voice validation; the backend enforces everything mechanical.

---

## 6. Questions and grants

| Kind | Answer | Effect |
|---|---|---|
| `PARTICIPATION_CONFIRMATION` | `{"speak_up_for_me", "ask_me_first", "give_me_time", "reminders_opted_in"?}` | Confirms preferences → new generation |
| `PARTICIPATION_CONFIRMATION` | `{"confirmed": false}` | Decline: stays unconfirmed, the waiting job resumes |
| `MISSING_FACT` + `{"constraint": {patch}}` | constraint patch | Confirms that constraint → new generation |
| `MISSING_FACT` otherwise | e.g. `{"answer": true}` | Stored; the waiting job resumes |
| `OBJECTION_DISCLOSURE`, `CONCESSION_CONSULTATION`, `BROADER_EXPLORATION` | `{"granted": bool}` | Stores a grant for (owner, kind, `action_ref`). **Granted:** bumps the permission revision → new generation; the grant stays valid while input and membership revisions are unchanged. **Refused:** the waiting job resumes and the blocker remains. Never creates consent. |

---

## 6a. Onboarding (conversational intake)

Instead of a form, each traveller's agent asks a few questions in their private chat (`my_private_messages`):
1. **The organizer** describes the trip (`set_trip_description`). Their agent proposes a brief; they confirm it (`confirm_trip_brief`).
2. **Everyone's agent** opens with one trip-aware question ("Hi Dave! Honolulu, Feb 12–16 with Alice, Bob and Carol. Where would you fly from, and is there anything that's a must-have or a deal-breaker?"). It then asks about at most two open essentials per message: origin and dates, money, and how the agent should act. Usually 2–4 questions, never more than 5 (enforced).
3. **What the agent heard** becomes `constraint_patches`. Each one quotes the person's exact words (`patch_json.source_quote`, checked against their message), so nothing unsaid is recorded. The origin is confirmed right away with `confirm_origin`; everything else is confirmed once, through the summary and `confirm_intake`.
4. **Everything else** (rooming, a checked bag, landing times, connections) is captured only if they mention it. Otherwise it's asked **just in time**, when a real plan depends on it: a `MISSING_FACT` question whose `action_json.nudge` holds the constraint a yes or a no records. Answer with `{"answer": true, "constraint": nudge.yes}`. The waiting job resumes and re-plans with it.
5. **If someone's own requirement blocks the plan** (own room, minimum rating, arrival times, stops), only they are asked whether to set it aside for this trip. Same nudge shape: a yes records the relaxed value, a no records `kept_<key>` so they're never asked again.
6. **Planning in an onboarded room** searches from each person's confirmed airport and the brief. It waits (`WAITING_FOR_INTAKE`) until everyone is onboarded, and `publish_proposal` refuses with `INTAKE_INCOMPLETE` until then. The airport transfer is searched after flights are chosen, for the group's latest landing.

New constraint keys: `origin_airport` (IATA), `arrival_window` (`{"earliest": "HH:MM", "latest": "HH:MM"}`, local at the destination), `max_stops`, `no_budget_limit`, `trip_note` (private, not used in planning yet), `kept_<key>`.

---

## 6b. Compromise ledger (fairness across decisions)

The optimizer scores, per person and decision, how much they'd give up: a **burden** from 0 to 1. Their running total across approved decisions, **H_i**, goes into the next optimization so the person who gave up most gets priority next. The ledger records H_i from human approval only:

1. **At publish:** the worker sends `burdens[{member_id, task_id, burden_micros}]` with the proposal. `burden_micros` is the burden in millionths (`round(b × 1_000_000)`, 0..1,000,000) so sums are exact. `task_id` names the decision (the optimizer's `task_id`, e.g. `STAY`, `FLIGHT:12`, `DINNER`). Each member must be one of the proposal's reviewers, and each (member, task) pair may appear only once. They are frozen in `proposal_burden`, and **nothing counts yet**.
2. **When every reviewer accepts** (the proposal becomes `AGREED`), the module copies them into `compromise_entry` as `ACTIVE`, in the same transaction. There is one entry per (proposal, member, task), so a retried publish (same `requestKey`) or a repeated approval can't count twice.
3. **A replacement counts only once it's agreed:** when a later proposal with the same `task_id` is agreed, the earlier ACTIVE entries for that task (everyone's) become `SUPERSEDED`. Until then they keep counting, even though publishing the new version marks the old proposal `SUPERSEDED`. Tasks the new proposal doesn't mention are untouched.
4. **A withdrawn approval** (`withdraw_consent`, AGREED → AWAITING_REVIEW) marks that proposal's entries `REVOKED`. If everyone accepts again, the same entries become `ACTIVE` again; nothing is duplicated. Entries it superseded stay superseded.
5. **Who sees it:** the worker gets `compromise_history` in the job context, and each person sees their own entries in `my_compromises`. There is no room-wide view, because burdens are derived from private limits.

The optimizer never writes this table; only agreement does.

---

## 7. Views (what each caller can read)

All 26 views are per-caller and update live.

| View | Contents |
|---|---|
| `my_rooms`, `room_members` (`is_me`, `agent_id`), `room_agents` | Rooms you're in and who's there. No identities of others. |
| `my_profile`, `my_participation_preferences`, `my_constraints`, `my_constraint_patches`, `my_private_messages`, `my_questions` | Yours only |
| `room_messages` | Live Negotiation chat: ROOM-audience utterances with agent ids, thread, type and refs. No payload JSON. |
| `my_agent_messages` | OWNER_PRIVATE utterances addressed to you (My Agent) |
| `room_threads`, `room_events` (room-wide plus your private events), `room_candidates`, `room_milestones` | Shared progress |
| `room_proposals` | Terms, digest, summary, state, `reviewer_count`, `is_reviewer`. No validation JSON. |
| `my_proposal_impacts`, `my_consents` (`decide_later_available` is always true) | Your allocation and your own response |
| `my_compromises` | Your approved compromises: `task_id`, `proposal_id`, `proposal_version`, `stage`, `burden_micros`, `state` (`ACTIVE` / `SUPERSEDED` / `REVOKED`) |
| `room_consent_counts` | `required` / `accepted` / `declined` / `pending` per proposal. **Counts only, never who.** |
| `room_presence` | `membership_id`, `online`, `last_seen` |
| `room_agent_activity` | `agent_id`, `activity`, `thread_id` |
| `worker_pending_jobs` | `PENDING` jobs; empty unless the caller is an allowlisted worker |
| `room_trip_brief` | The organizer's description, the proposed/confirmed brief (`brief_json`: destination, airport, dates, components, vibe), `unclear_json`, state, revision |
| `my_intake` | Your onboarding: state ASKING / SUMMARY / CONFIRMED, coverage of origin, dates, money, settings, and questions asked (max 5) |
| `room_intake_progress` | `ready` / `total`. **Counts only, never who or what they said.** |

---

## 8. Dispatcher

`dispatcher/`: `npm install`, then `STDB_URL=… STDB_DB=… STDB_TOKEN=… WORKER_WAKE_URL=… npm start`.
- Subscribes to `worker_pending_jobs`.
- POSTs `{"job_id", "room_id", "kind"}` for every pending job: at startup, on reconnect, on new jobs, and when an expired lease makes a job pending again.
- Never claims or writes. The worker should still poll and scan on startup (W05).
- First run without `STDB_TOKEN` prints a new identity and the `add_worker` command.

---

## 9. Worker adapter (`SpacetimeBackend`)

`SpacetimeBackend(url, database, worker_token, limits=, room_id=, owner_name=, owner_tokens=)` implements every `BackendPort` method.

**Name and id mapping:**
- **Owners:** default `m<member_id>`. Tests use display names (`p1`…`p4`) via `owner_name`. `"mediator"` maps to `mediator_agent_id`.
- **Questions:** worker kinds map to module kinds (`DISCLOSURE` ↔ `OBJECTION_DISCLOSURE`, …). The worker's question id is sent as `request_key`.
- **Messages:** the worker's `message_id` is the module's `messageKey`; replies and receipts are mapped back to numeric ids from context.
- **Threads:** the worker's thread id is stored as `topic_ref`. The adapter opens the thread on first use and reuses it after that.

**Behavior:**
- `snapshot_ref = digest(Snapshot)`, the same fingerprint `FlightValidator` stamps on its report.
- `operation()` goes through `begin_operation` / `complete_operation` with `Limits.model_calls` / `research_calls`.
- `answer_question()` acts as the owner and needs `owner_tokens`; it exists for fixtures and demos. Real answers come from the app.
- `room_id` limits `eligible_jobs()` to one room and scopes claim keys.
- `notify_owner()` is `reply_to_owner` (an AGENT row in that owner's `my_private_messages`). `room_event()` is `emit_event` with no member (a ROOM row in `room_events`).

**What people see when no plan fits** (`job.outcome = NO_FEASIBLE_CANDIDATE`):
- **Room:** one `NO_FEASIBLE_PLAN` event. `safe_payload_json` has `text`, `blocked_travellers` (counts by cause: `HARD_LIMIT`, `TRIP_LIMIT`, `BAGGAGE_UNAVAILABLE`, `NO_CANDIDATES`), `blocked_requirements`, and for an arrival clash `closest_spread_seconds` / `required_seconds`. It never names anyone or states a limit.
- **Each blocked traveller:** a private note with their own numbers, e.g. "the cheapest trip found for you is $630.00 … above your $600.00 trip limit".
- **Room requirements:** when someone's own-room request or minimum rating is what blocks the plan (or pushes another traveller over their limit), only that person's agent asks them first, as a `CONCESSION_CONSULTATION` with `action_ref` `relax-bedrooms-r<input_revision>` / `relax-rating-r<…>`. A grant re-plans with the requirement set aside; a refusal ends in the private notes above.

---

## 10. Simulator and setup

- **Simulator:** `spacetimedb/scripts/simulate.py` plays the worker with FIXTURE flights so the frontend has live data. `seed` creates a room of simulated people; `negotiate --room N` makes agents talk and publishes a plan (everyone in the room must have confirmed their settings first); `--crash-after 1` rehearses a crash and takeover.
- **Service identities:** the database owner (whoever published) runs `spacetime call <db> add_worker '{"__identity__":"0x…"}' '"worker"'` for the worker's and the dispatcher's identities. Mint them with `POST /v1/identity`; the tokens don't expire, so keep them secret.
- **Tests:**
  - `spacetimedb/scripts/test.sh`: port 3100
  - `scripts/backend-integration.sh`: port 3200, needs `worker/.venv`

  Both start an in-memory server, publish, run pytest and stop. Override the ports with `STDB_TEST_PORT`.

---

## 11. Decisions settled with the worker

1. **Wording:** semantic validation stays in the worker. The module enforces the mechanical checks in §5 and verifies the worker's validation digest.
2. **Grants:** a granted disclosure, consultation or exploration starts a new generation. A refusal or a plain missing-fact answer resumes the same one.
3. **Gates:** unconfirmed preferences, disclosure, consultation and exploration are enforced server-side; reminders and urgency are worker policy plus opt-in reminders.
4. **Publication:** trip selections (flights, shared stay and transfer) are re-validated in the module.
5. **Waking the worker:** the dispatcher POSTs to `WORKER_WAKE_URL`, and the worker also polls `eligible_jobs()`.

**Still open:**
- the INTERPRET-job worker (owner chat and review follow-ups)
- live Claude and Apify runs
- leaving a room
- a real auth provider
