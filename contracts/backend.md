# Tandem backend contract (SpacetimeDB module)

For: Skylar (worker / LangGraph), Anna (frontend), and anyone calling the backend.
Owner: Ronit. Source of truth: `spacetimedb/src/`. Verified by `spacetimedb/scripts/test.sh` (45 tests).
SpacetimeDB **2.13.0**. Every JSON example in `examples/` was captured from a real run.

---

## 1. How to talk to it

| Who | How |
|---|---|
| Python worker | HTTP. `POST /v1/database/<db>/call/<name>` for reducers/procedures, `POST /v1/database/<db>/sql` to read views. `Authorization: Bearer <token>`. |
| Browser (Next.js) | TypeScript SDK (`spacetimedb` npm), generated bindings, subscribe to views. |
| Dispatcher | TypeScript SDK, subscribes to `worker_pending_jobs` (see §8). |

Connection details:
- **Local:** `spacetime start`, then `spacetime publish --server local tandem-dev` (from repo root).
- **Shared dev:** Maincloud `tandem-dev`. URL and worker registration to follow once it's published.
- **Never run the test suite against the shared database.** It wipes data.

`spacetimedb/tests/stdb_client.py` is a ~140-line client that does all of the encoding below. Use it as the starting point for the worker's `BackendPort` adapter.

### Encoding rules (tested)

1. **Top-level argument names are camelCase** (`jobId`, `requestKey`), exactly as declared in the module. **Fields inside nested objects are snake_case** (`open_thread.topic_ref`, `candidates[].candidate_ref`). **SQL/view columns are snake_case.** `stdb_client` lets you write snake_case everywhere and converts the top level.
2. Options: `null` for none, `{"some": value}` for a value. A bare value is rejected.
3. Identity arguments: `{"__identity__": "0x<64 hex>"}`. The worker never needs to send one.
4. u64/u32: JSON numbers. IDs stay far below 2^53.
5. SQL rows come back as positional arrays with a schema; `stdb_client.sql()` turns them into dicts. In raw rows, options are `[0, value]` (some) or `[1, []]` (none), identities `["0x…"]`, timestamps `[micros]`.

### Results and errors

| Call type | Success | Business failure |
|---|---|---|
| **Reducer** | HTTP 200, empty body. Reducers can't return data. | HTTP **530**, body `CODE: detail` (`examples/error.reducer.json`) |
| **Procedure** (`claim_job`, `load_execution_context`, `publish_proposal`) | HTTP 200, body is a JSON-encoded string: `{"ok": true, ...}` | HTTP 200 with `{"ok": false, "code", "detail"}` (`examples/error.procedure.json`) |
| Bad argument shape | HTTP 400 with a parser message | |
| **Outcome unknown** | HTTP 504 (durability wait timed out) | Retry with the **same request key** |

Error codes: `NOT_AUTHORIZED`, `NOT_A_MEMBER`, `NOT_FOUND`, `INVALID_ARGUMENT`, `INVALID_STATE`, `STALE_SNAPSHOT`, `LEASE_LOST`, `KEY_BODY_MISMATCH`, `QUESTION_SUPERSEDED`, `EVIDENCE_EXPIRED`, `PREFERENCES_UNCONFIRMED`, `THREAD_CLOSED`, `TURN_BUDGET_EXHAUSTED`, `UTTERANCE_PAYLOAD_MISMATCH`, `UNSUPPORTED_SCHEMA`, `PRIVATE_CONTENT_DETECTED` (new, see §5).

---

## 2. Core rules the backend enforces

- **Generation = job id.** Any confirmed change to a room's constraints, membership or participation preferences bumps a revision and supersedes every open PLAN job, its questions and threads, plus proposals awaiting review (their consents become `STALE`). A fresh PLAN job is queued if the room has any constraints.
- **Attempt = fencing token.** Every worker write checks: caller is an allowlisted worker, the job is `CLAIMED` under this exact attempt by this caller, the lease hasn't expired, and (for PLAN jobs) the room's three revisions still equal the job's snapshot. Otherwise `LEASE_LOST` or `STALE_SNAPSHOT`.
- **Request keys make writes idempotent.** Same key and same body returns the original result and does nothing new. Same key with a different body is `KEY_BODY_MISMATCH`. The digest ignores `attempt`, so a re-claimed attempt replaying a logical operation (even under another registered worker identity) gets the original receipt, as the worker plan requires.
- **Only humans approve.** Worker identities are refused on every human operation (C01).
- **Private data never reaches another person's browser.** All tables are private; each view filters by caller (C02).

---

## 3. Worker operations (agreed names)

All take `jobId` + `attempt` and run the checks in §2 unless noted. "Key" = `requestKey` (or `messageKey`) idempotency.

| Operation | Type | Arguments | Behavior |
|---|---|---|---|
| `claim_job` | procedure | `jobId?`, `leaseSeconds` (2–300), `requestKey` | Claims that job (or the oldest pending if `null`). Returns `job_id`, `generation`, `room_id`, `kind`, `attempt`, revisions, `lease_expires_at_micros`, `graph_thread_ref`. Competing claim → `INVALID_STATE`. A lapsed lease is reclaimable. |
| `load_execution_context` | procedure | `jobId`, `attempt` | One consistent snapshot (`examples/load_execution_context.response.json`): room, `mediator_agent_id`, members with confirmed preferences, revision and derived `tool_policy`, constraints (**including private rationale**; worker is the trusted processor and must scope it per agent), questions with answers, current grants, threads, this job's messages, candidates, open proposals. |
| `renew_lease` | reducer | `jobId`, `attempt`, `leaseSeconds` | Extends the current attempt only. Heartbeat every ~15 s with a 60 s lease. |
| `submit_agent_message` | reducer | see §5 | Durable message + thread advance + turn charge, atomically. Key: `messageKey`. |
| `record_message_processed` | reducer | `jobId`, `attempt`, `messageId`, `recipientAgentId`, `replyIds[]` | Consumer receipt. Repeat with same replies is a no-op; different replies → `KEY_BODY_MISMATCH`; wrong recipient → `NOT_AUTHORIZED`. |
| `ask_owner` | reducer | `jobId`, `attempt`, `requestKey`, `memberId`, `kind`, `actionJson`, `prompt`, `answerSchemaJson`, `threadId?` | Private versioned question (v1). Kinds: `MISSING_FACT`, `PARTICIPATION_CONFIRMATION`, `OBJECTION_DISCLOSURE`, `CONCESSION_CONSULTATION`, `BROADER_EXPLORATION`. If `threadId` is given, that thread closes with `WAITING_FOR_OWNER`. The worker finds the question id in context via `request_key`. |
| `park_job` | reducer | `jobId`, `attempt`, `requestKey`, `graphThreadRef`, `questionIds[]` | `WAITING_FOR_INPUT`, lease released, `graph_thread_ref` stored. If every listed question is already answered (early-answer race), the job goes straight back to `PENDING`. |
| `schedule_resume` | (built in) | | No call needed. `answer_question` and `park_job` make the waiting job `PENDING` once all its questions are answered. Resume with a new `claim_job`; `graph_thread_ref` comes back in the claim. |
| `publish_proposal` | procedure | `jobId`, `attempt`, `requestKey`, `stage`, `termsJson`, `sharedSummaryJson`, `validationJson`, `evidenceRefs[]`, `affectedMemberIds[]` (empty = all active), `impacts[{member_id, impact_json}]` | One transaction: supersedes the stage's previous proposal, writes the immutable proposal, computes `terms_digest = sha256(termsJson bytes)` (**send canonical JSON**), private impacts, `PENDING` consent rows, milestone `IN_REVIEW`, job `SUCCEEDED`. Returns `proposal_id`, `version`, `terms_digest`. Retrying the same key returns the same proposal. Stages: `UNDERSTAND`, `GOALS_DATES`, `TRAVEL`, `STAY_EXPERIENCES`, `COMPLETE_PLAN`. |
| `finish_job` | reducer | `jobId`, `attempt`, `requestKey`, `outcome`, `outcomeJson` | Terminal `FINISHED` (e.g. `NO_FEASIBLE_CANDIDATE`, `PAUSED_NO_PROGRESS`). |
| `fail_job` | reducer | `jobId`, `attempt`, `requestKey`, `retryable`, `code`, `detail` | Retryable → `PENDING`; otherwise `FAILED`. Neither can touch a `SUCCEEDED` job. |

Extra worker writes (same checks):

| Operation | Arguments | Purpose |
|---|---|---|
| `record_candidates` | `requestKey`, `candidates[{candidate_ref, candidate_set_version, category, mode, source_url, observed_at, facts_json, missing_fields}]` | Upsert sourced options (`mode`: `APIFY_OBSERVED`, `PROVIDER_VERIFIED`, `CACHED_WITH_TIMESTAMP`, `SANDBOX`, `FIXTURE`). Makes them citable as claim/evidence refs. |
| `reply_to_owner` | `requestKey`, `memberId`, `body` | Agent → owner message in the private My Agent chat. |
| `propose_constraint_patch` | `requestKey`, `memberId`, `patchJson`, `sourceMessageId?` | Tentative extraction for the owner to confirm. Patch shape: `{"key", "value", "unit", "hardness", "authorized_range"?, "private_rationale"?}`. |
| `propose_participation_preferences` | `requestKey`, `memberId`, `speakUpForMe`, `askMeFirst`, `giveMeTime` | Suggested settings; stay unconfirmed, grant nothing. Refused once confirmed. |
| `emit_event` | `requestKey`, `type`, `memberId?`, `payloadJson` | Live Negotiation progress (`INITIAL_SEARCH_STARTED`, …). No `memberId` = room-wide (private-text checked); with it = that owner only. |

Job statuses: `PENDING` → `CLAIMED` → `WAITING_FOR_INPUT` / `SUCCEEDED` / `FINISHED` / `FAILED`, or `SUPERSEDED` / `CANCELLED` at any point. Kinds: `PLAN` (snapshot-bound) and `INTERPRET` (owner messages, review follow-ups; not superseded by revisions).

---

## 4. Human operations (browser)

| Operation | Arguments | Notes |
|---|---|---|
| `create_room` | `goal`, `displayName` | Creates the room, mediator agent, your membership and personal agent, five milestones, and an invite code. |
| `join_room` | `inviteCode`, `displayName` | Bumps membership revision. |
| `upsert_profile` | `displayName`, `avatarRef`, `selfDescription`, `priorityRanking[]` | |
| `confirm_participation_preferences` | `speakUpForMe`, `askMeFirst`, `giveMeTime` | Defaults before confirmation: false / true / true, **unconfirmed**. Confirming or editing bumps the permission revision in all your rooms (new generation). |
| `set_constraint` | `roomId`, `key`, `valueJson`, `unit`, `hardness` (`HARD`/`SOFT`), `authorizedRangeJson?`, `privateRationale?` | Form entry = confirmed. New generation. Room sees only "A participant updated their requirements". |
| `confirm_constraint_patch` / `reject_constraint_patch` | `patchId` | Confirming applies it like `set_constraint`. |
| `submit_private_message` | `roomId`, `body` | Message to your own agent; queues an `INTERPRET` job. |
| `answer_question` | `questionId`, `version`, `answerJson` | See §6. Wrong owner → `NOT_FOUND`; not open or wrong version → `QUESTION_SUPERSEDED`. |
| `respond_to_proposal` | `proposalId`, `version`, `termsDigest`, `response`, `note?` | `ACCEPT`, `NOT_COMFORTABLE`, `ASK_MY_AGENT`, `SUGGEST_ALTERNATIVE`, `DECIDE_LATER`. Rejected with `STALE_SNAPSHOT` if version, digest or room revisions changed. Agreement is recomputed in the same transaction: **every affected member must have ACCEPTED these exact terms**. Non-accept responses message your agent (INTERPRET job). |
| `withdraw_consent` | `proposalId`, `note?` | Reopens an AGREED proposal and its milestone. |

---

## 5. `submit_agent_message` (schema v2)

Full request: `examples/submit_agent_message.request.json`.

Arguments: `jobId`, `attempt`, `messageKey`, `schemaVersion` (must be **2**), `roundId`, exactly one of `threadId` / `openThread{topic_ref, participant_agent_ids, turn_budget?, successor_of?}`, `senderAgentId`, `recipientAgentId`, `type`, `replyTo?`, `draftRef?`, `proposalId?`, `proposalVersion?`, `payloadJson`, `utterance`, `claimRefs[]`, `evidenceRefs[]`, `audience` (`ROOM` / `OWNER_PRIVATE` / `INTERNAL`), `audienceMemberId?`, `sanitizationStatus` (`VALIDATED` / `TEMPLATE_FALLBACK`), `grantRef?`.

Checks, in one transaction (nothing is written if any fail):
1. `schemaVersion != 2` → `UNSUPPORTED_SCHEMA`.
2. Sender and recipient agents must be in the job's room; a personal agent's owner must still be an active member. No broadcast: one explicit recipient, not the sender.
3. Thread: `openThread` must list both sender and recipient, all in the room; budget ≤ 6 (default 6). An existing thread must belong to this job and round, be open, and include both agents. Older-generation thread → `THREAD_CLOSED`. Closed by budget → `TURN_BUDGET_EXHAUSTED`.
4. Round budget: 24 turns per `(job, roundId)`. Opening more threads doesn't bypass it.
5. **ROOM audience:**
   - non-empty utterance and a valid `sanitizationStatus`
   - sender's owner has **confirmed** preferences, else `PREFERENCES_UNCONFIRMED`
   - an `OBJECT` from an owner without `speak_up_for_me` needs `grantRef` pointing at a granted, current `OBJECTION_DISCLOSURE` grant, else `NOT_AUTHORIZED`
   - utterance must not contain any owner's private rationale text (case-insensitive, 6+ chars), else `PRIVATE_CONTENT_DETECTED`
   - every claim/evidence ref must resolve to a recorded candidate ref, `msg:<id>` or `proposal:<id>` in this room, or `draft:<ref>`, else `UTTERANCE_PAYLOAD_MISMATCH`
6. **OWNER_PRIVATE:** a personal agent may only speak to its own owner; the mediator must name `audienceMemberId`. The effective audience owner is derived server-side.
7. Charges one turn to the thread and the round; the thread closes at its budget (`TURN_BUDGET`), and all of the round's open threads close at 24 (`ROUND_BUDGET`). Replays don't charge.

**What the backend can't check:** whether the wording is true or faithful to the typed act. That stays in the worker's room-voice validation; the backend only stores the status the worker reports.

---

## 6. Questions and grants

| Kind | Answer shape | Effect |
|---|---|---|
| `PARTICIPATION_CONFIRMATION` | `{"speak_up_for_me", "ask_me_first", "give_me_time"}` booleans | Confirms preferences → new generation |
| `MISSING_FACT` + `{"constraint": {patch}}` | constraint patch | Confirms that constraint → new generation |
| `MISSING_FACT` otherwise | any object | Stored; resumes the waiting job |
| `OBJECTION_DISCLOSURE`, `CONCESSION_CONSULTATION`, `BROADER_EXPLORATION` | `{"granted": bool}` | Writes an `action_grant` bound to that question, `sha256(canonical actionJson)`, and the current revisions, then resumes. **Never creates consent.** Grants appear in context only while revisions match. |

---

## 7. Views (what each caller can read)

All views are per-caller and update live.

| View | Contents |
|---|---|
| `my_rooms`, `room_members` (`is_me`, `agent_id`), `room_agents` | Rooms you're in and who's there. No identities of others. |
| `my_profile`, `my_participation_preferences`, `my_constraints`, `my_constraint_patches`, `my_private_messages`, `my_questions` | Yours only |
| `room_messages` | Live Negotiation chat: ROOM-audience utterances with agent ids, thread, type, refs. No payload JSON. |
| `my_agent_messages` | OWNER_PRIVATE utterances addressed to you (My Agent). |
| `room_threads`, `room_events` (room-wide + your private events), `room_candidates`, `room_milestones` | Shared progress |
| `room_proposals` | Terms, digest, summary, state, `reviewer_count`, `is_reviewer`. No validation JSON. |
| `my_proposal_impacts`, `my_consents` (`decide_later_available`) | Your allocation and your own response |
| `room_consent_counts` | `required` / `accepted` / `declined` / `pending` per proposal. **Counts only, never who** (team decision). |
| `worker_pending_jobs` | `PENDING` jobs; empty unless the caller is an allowlisted worker. |

---

## 8. Dispatcher

`dispatcher/`: `npm install`, then `STDB_URL=… STDB_DB=… STDB_TOKEN=… WORKER_WAKE_URL=… npm start`.

- Subscribes to `worker_pending_jobs`.
- POSTs `{"job_id", "room_id", "kind"}` to `WORKER_WAKE_URL` for every pending job: at startup, on reconnect, on new jobs, and when an expired lease makes a job pending again.
- Never claims or writes. The worker should still poll and scan on startup (W05).
- First run without `STDB_TOKEN` prints a new identity and the `add_worker` command to register it.

---

## 9. Setup

- **Register service identities.** The database owner (whoever published) runs `spacetime call <db> add_worker '{"__identity__":"0x…"}' '"worker"'` for the worker's and the dispatcher's identities. Mint them with `POST /v1/identity` and keep the tokens as secrets; they don't expire.
- **Run the backend tests:** `spacetimedb/scripts/test.sh`. It starts an in-memory server on port 3100 (override with `STDB_TEST_PORT`), publishes, runs pytest, and stops.

---

## 10. Open points to settle (Skylar + Ronit)

1. **Wording validation stays in the worker.** The backend enforces status, refs, audience, preference gates and a private-text substring check (§5). OK?
2. **Resume vs new generation:** proposed in §6. In particular, a granted disclosure/consultation/exploration *resumes* the same generation; preference confirmations and constraint answers start a new one.
3. **Wake mechanism:** dispatcher POSTs to `WORKER_WAKE_URL`; worker also polls `worker_pending_jobs`. OK, or do you prefer polling only?
4. **Server-side gates:** only "unconfirmed preferences can't speak in the room" and "objection needs speak-up or a disclosure grant" are enforced here. *Ask me first* and *give me time* are worker policy (context gives `tool_policy`).
5. **New error code:** `PRIVATE_CONTENT_DETECTED`.
6. **Argument casing:** camelCase top-level vs snake_case nested is a SpacetimeDB quirk. `stdb_client` hides it; reuse it in the adapter.
