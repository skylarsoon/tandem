# Design: conversational trip onboarding (trip brief + per-traveller intake) and realistic test personas

## Context

Today each traveller's private limits are entered as typed `set_constraint` calls, the same as a form. Nothing asks for missing ones: a missing budget silently becomes "no limit" (`NO_CONFIRMED_CAP`, worker/src/tandem_worker/adapters/spacetime.py:40). Route data (who flies from where, and when) comes from a live config file, not from people. Ronit wants onboarding that feels different from a form:

1. The organizer describes the trip in their own words.
2. Each traveller's agent opens with one basic, trip-aware question, then asks a few adaptive follow-ups.
3. It's short, but it covers enough that the agent doesn't have to nudge people later.
4. It never assumes anything on someone's behalf.

Test travellers should feel like real people with real private reasons.

The architecture doc already describes this: Claude does "conversational onboarding, tentative extraction of constraints", turns language into a *proposed* typed change the owner confirms, and "asks rather than silently chooses".

Decisions Ronit made:
- **Gate:** searches start as soon as routes and dates are known. Nothing is proposed until every traveller has confirmed their intake.
- **Origin quick-confirm:** as soon as someone says where they fly from, the agent asks "Flying from SFO on Feb 12, right?" with one tap to confirm. That makes the origin a confirmed constraint early, so searches can start without assuming anything. Everything else is confirmed once, in the final summary.
- **New limits:** add typed limits for arrival time (no red-eyes or late arrivals: earliest and latest local arrival) and maximum stops. Flight search and the solver both respect them. "Home by Sunday" (return flights) stays a private note, and the agent says plainly that it can't plan that yet.
- **Agent settings** (speak up / ask first / give time) come from the same intake chat, are confirmed in the final summary, and carry over to later trips.
- **Scope:** the backend, the worker's intake engine and test personas. Anna builds the screens from the contract. The intake graph is a new, separate graph in Skylar's worker area. Search-early also needs small changes to his coordinator (`load` and `discover`, see below), so agree them with him first.
- **Personas:** both scripted transcripts (deterministic tests) and Claude-played personas (realistic live runs).

## What the experience is

**Organizer:** they write a free-text trip description, for example "Four of us from SF, LA, Seattle and NYC, Honolulu Feb 12–16, chill beach trip, want a place together and one good group dinner." Their agent turns it into a **trip brief** (destination and airport, dates, group size, what to plan: flights, stay, transfer, plus a short vibe) and lists anything unclear. The organizer confirms it, and it becomes the room's shared context and settings.

**Each traveller (organizer included):** their agent opens privately with one basic question grounded in the brief. For example: *"Hi Dave! Honolulu, Feb 12–16 with Alice, Bob and Carol. Where would you fly from, and is there anything about this trip that's a must-have or a deal-breaker for you?"*

After each answer:
- The agent extracts only what was actually said.
- It works out which needed topics are still open.
- It asks the next question, at most two topics per question, worded around what they already said.

There are usually 2–3 questions, with a hard cap of 5. When everything needed is covered, the agent sends **one summary**: each item with the person's own words next to it, plus the proposed agent settings. The person confirms or edits each item; those are the existing constraint patches and preference proposals.

**No assumptions, enforced in code, not just in the prompt:**
1. Every extracted item cites the exact message and a quote from it. A number has to appear in that quote, or the item is dropped.
2. Anything ambiguous becomes a question, never a default. Example: "$800": is that flight only or the whole trip? A firm cap or flexible?
3. Topics nobody mentioned stay *unknown*. "No limit" is recorded only if the person says so.
4. Settings are proposed only from what the person said. Otherwise the agent asks one light question, for example: *"If the plan costs you more than your cheapest option, should I check with you first?"*

**Only the essentials up front; nudge later when it matters.** The intake asks only about what planning can't start without:
1. Where they fly from, and whether the brief's dates work for them.
2. Money: how much, flight only or whole trip, firm or flexible and by how much. A clear "money's not an issue" also counts.
3. How the agent should act: one light question, unless they've already shown it.

Everything else is **captured if they mention it, never asked by default**. That covers rooming, checked bag, arrival times, stops, rating and anything else. The opening question ("anything that's a must-have or deal-breaker?") invites it.

When a real plan depends on something nobody covered, the agent **nudges just in time**, using the existing private MISSING_FACT question with its own specific wording. For example:
- *"The best-value place has 2 bedrooms for 4 of you. OK to share a room?"*
- *"Your cheapest fare has no checked bag. Do you need one?"*
- *"This flight lands at 1:10 am. Is that OK?"*

The answer becomes a constraint, through the module's MISSING_FACT answer with `{"constraint": …}` (questions.ts:116), and is never assumed. Each topic gets at most one nudge per trip.

Private reasons ("saving for rent") are stored as `private_rationale` and are never shown to the room.

## Backend (SpacetimeDB module): additive only, no existing table changes

New tables in `spacetimedb/src/schema.ts`:
- **`trip_brief`**: `roomId` PK, `description`, `briefJson` (structured draft or confirmed brief), `state` DRAFT|PROPOSED|CONFIRMED, `revision`, timestamps.
- **`intake`**: id, `roomId`, `owner`, `state` NOT_STARTED|ASKING|SUMMARY|CONFIRMED, `coverageJson` (per topic: OPEN|ANSWERED|UNCLEAR|SKIPPED), `questionCount`, timestamps. Indexes byRoom and byOwner.
- **`job_subject`**: `jobId` PK, `memberId`. It says whose conversation an INTERPRET/INTAKE job belongs to, without altering the `job` table.

Reducers, reusing the existing guards and effects (`requireLease`, `withReceipt`, `messageOwnAgent`, `upsertConstraint`, `invalidateRoom`, `createJob`):
- **`set_trip_description(roomId, description)`**: organizer only. Creates or updates `trip_brief` as DRAFT and queues an INTERPRET job for the organizer (via `job_subject`).
- **`propose_trip_brief(job, attempt, key, briefJson)`**: worker. Sets PROPOSED.
- **`confirm_trip_brief(roomId, revision)`**: organizer. Sets CONFIRMED. Writes `components`, `arrival_overlap_seconds` and destination/dates into room settings via the same validation as `setRoomSettings` (rooms.ts:181). Starts an intake (and an INTAKE job) for every active member.
- **`join_room`**: when the brief is already confirmed, also starts that member's intake.
- **`update_intake(job, attempt, key, memberId, coverageJson, state)`**: worker. ASKING or SUMMARY only.
- **`confirm_origin(roomId, patchId)`**: owner. Confirms the inline origin patch only. Uses the existing `upsertConstraint`; one restart.
- **`confirm_intake(roomId, acceptedPatchIds, preferences?)`**: owner. In one transaction it:
  - applies the accepted PENDING patches;
  - confirms the proposed agent settings;
  - rejects the rest;
  - requires the money topic to be covered;
  - sets CONFIRMED;
  - restarts planning once.
- Existing reducers are reused unchanged: `submit_private_message`, `reply_to_owner`, `propose_constraint_patch`, `confirm/reject_constraint_patch`, `propose_participation_preferences`, `confirm_participation_preferences`.
- **New patch shape:** `propose_constraint_patch` gains optional `source_quote` inside `patchJson`. The module checks it is an exact substring of the linked source message.
- **`buildContext`** (jobs.ts:139) adds, for INTERPRET/INTAKE jobs and only for the subject member: their private messages, patches, intake row, and the trip brief.
- **Publish gate:** `publish_proposal` fails with `INTAKE_INCOMPLETE` unless every active member's intake is CONFIRMED. Research before that point is allowed.

New views:
- `room_trip_brief`: members only.
- `my_intake`: the caller's own intake.
- `room_intake_progress`: neutral counts such as "3 of 4 ready". Never who, never contents.

New constraint keys the worker understands:
- `origin_airport` (IATA): HARD. Quick-confirmed inline.
- `arrival_window`: `{earliest, latest}`, local times at the destination on the brief's arrival date. Covers "no red-eyes" and "not after 10 pm".
- `max_stops`: an integer.
- `no_budget_limit`: true. An explicit "money doesn't matter"; it's the only way the money topic counts as covered without a budget.
- `trip_note`: free-text private notes the planner can't use yet (e.g. "home by Sunday"). Shown back as "noted, not used in planning yet".
- Existing keys: `flight_budget_total`, `trip_budget_total`, `baggage_required`, `own_bedroom`, `stay_min_rating`.
- A SOFT budget keeps `authorized_range` for **both** budget keys. `read_brief` currently drops a SOFT `trip_budget_total` (spacetime.py:244), so fix that. The flexible amount comes from the conversation, never invented.

**One restart per person, not one per item.** Confirming each patch restarts the room's planning revision (effects.ts:99). So:
- Only the origin is confirmed inline.
- Everything in the summary is applied in one transaction by `confirm_intake(roomId, acceptedPatchIds)`.
- A rejected item goes back to chat for editing. It isn't confirmed piecemeal.
- `confirm_intake` requires the money topic to be covered: a confirmed budget or `no_budget_limit`.
- Agent settings confirmed in the summary use the existing preference confirm path in the same call.

**Gate scope:** `INTAKE_INCOMPLETE` and `WAITING_FOR_INTAKE` apply only to rooms that have a trip brief. Rooms built directly with `set_constraint` behave exactly as today: seeds, existing tests, `live-room`. Direct `set_constraint` stays allowed, and the intake treats an existing constraint as an ANSWERED topic.

**Brief changes after confirmation:** re-proposing the brief reopens only the affected topics. Changed dates reopen `arrival_window` and the dates question; a changed destination reopens the origin quick-confirm. Confirmed answers to other topics are kept.

**Messages mid-summary:** a new message during SUMMARY returns the intake to ASKING. A new worker reducer, `withdraw_constraint_patches(job, attempt, memberId)`, marks that person's PENDING patches REJECTED so stale ones can't block `confirm_intake`.

**Routing INTERPRET jobs:** the intake graph handles a job only when its subject's intake is ASKING or SUMMARY. Other INTERPRET jobs, such as proposal responses and chat after intake, are finished as `NOT_HANDLED`, as the owner-chat worker isn't built yet. Their subject is found through `private_message.jobId`, which already exists; `job_subject` is needed only for INTAKE and brief jobs.

## Worker: intake engine (new graph)

**New file `worker/src/tandem_worker/graphs/intake.py`.** Per INTERPRET or INTAKE job, one pass:
1. `load`: trip brief, transcript, coverage, existing patches.
2. `extract`: Claude call (`ModelPort.extract`) returns a typed `IntakeExtraction`: items, each with a `{key, value, hardness, authorized_range?, private_rationale?, source_message_id, source_quote}`, plus a list of `unclear` entries with reasons.
3. `verify` (deterministic, new `intake_policy.py`):
   - Drop items whose quote isn't in the cited message, or whose numbers don't appear in the quote.
   - Map items to topics.
   - Mark UNCLEAR anything the extractor flagged.
4. `next`:
   - If essential topics are open or unclear and `questionCount` is under 5: a Claude call (`ModelPort.ask`) writes ONE question covering at most the top 2 open topics. It is checked to be a question, under 300 characters, private, with no invented facts. Send it with `reply_to_owner`, then `update_intake(ASKING)`.
   - Otherwise: `propose_constraint_patch` for each verified item, `propose_participation_preferences` if stated, one summary message, and `update_intake(SUMMARY)`.
5. A separate trip-brief path for the organizer's description: extract → `propose_trip_brief`.

**Contracts** in `contracts.py`: `TripBrief`, `IntakeItem`, `IntakeExtraction`, `IntakeQuestion`, plus `Topic` enums.

**Ports** in `ports.py`, implemented in `backend.py` (LocalBackend) and `adapters/spacetime.py`: `load_intake`, `propose_patch`, `update_intake`, `propose_trip_brief`.

**Claude adapter** (`adapters/claude.py`): `extract`, `ask` and `brief` structured calls with their instructions. The no-assumption rule is stated in the prompt and enforced again in `verify`.

**Runner** (`runner.py`): route INTERPRET/INTAKE jobs to the intake graph. `pending_jobs` currently keeps only PLAN (spacetime.py:210); extend it to all kinds.

**Read side:** `read_brief` maps `origin_airport` and `date_fit`. Missing required intake makes the coordinator stop before solving with outcome `WAITING_FOR_INTAKE`, instead of using `NO_CONFIRMED_CAP`.

## Search early from people's answers, not config

This changes Skylar's coordinator. Tell him before merging.

- **Searches from room data.** Flight searches come from the confirmed brief (destination airport and dates) plus each member's confirmed `origin_airport`, `max_stops` and `arrival_window`. They replace `LiveConfig.searches` for app rooms; the config path stays for the CLI and smoke tests.
- **Stay, activity and dinner searches** come from the brief.
- **Party size** is the active member count when the job runs.
- **The transfer search runs after flights are solved,** with pickup at the latest arrival (rounded up to 30 minutes) and drop-off at the stay's address or area. The brief has neither.
- **Coordinator `load`:** in rooms with a brief, don't park on unconfirmed agent settings. Those are confirmed through intake, and the WAITING_FOR_INTAKE stop covers them.
- **Coordinator `discover`:** runs when the brief is confirmed and **every** active member's origin is confirmed. That is minutes after the last person joins, thanks to the quick-confirm. Then it stops with `WAITING_FOR_INTAKE` until all intakes are confirmed.
- **What early search buys (honest limit).**
  - It validates every route and gives a price preview.
  - Prices expire after 15 minutes (config.py:28), so the proposal always uses a fresh search.
  - If more than 15 minutes have passed, `discover` refreshes before solving.
- **Apify reuse:** run-ledger keys are built from the actor input and room, not the revision. A job superseded by someone's confirmation then has its in-flight runs collected by the next job instead of paying again.
- **Solver/search respect for the new limits:**
  - `max_stops` goes into the memo23 input; its existing `maxStops` filter covers it.
  - `arrival_window` becomes an eligibility filter in `TripSolver` and `TripValidator`.
  - The module's publish check verifies the arrival window too.
  - Conflicts gain `ARRIVAL_TIME` with the earliest and latest fares found. The owner gets a private note; owners holding a window are asked to relax it, following the `relax_ref` pattern in solver.py.

## Just-in-time nudges (personal agent, during planning)

Deterministic triggers in `graphs/personal.py`'s `decide` fire when the owner never answered the topic and the current plan depends on it:
- the stay sleeps people two to a room;
- the selected fare has no checked bag;
- the arrival falls between 23:00 and 06:00 local time;
- the flight has more than one stop.

Each sends one MISSING_FACT question. The question is specific and generated from the plan's own facts, and it carries `answer_schema_json` for a yes/no or short choice. The answer travels as `{"constraint": {...}}`: questions.ts:116 already writes it with `upsertConstraint`, which starts a new generation. The worker's `_answer` mapping (spacetime.py:321) is extended to send that shape.

A topic is nudged at most once: the question id is derived from owner + topic + trip. Once answered, it's a normal constraint for the solver.

## Running intake for real users

There is no worker loop today: `run_live` handles one job and exits (live.py:291). Add `tandem-worker serve`, which polls `pending_jobs` every few seconds for all job kinds and routes PLAN to the coordinator and INTERPRET/INTAKE to the intake graph. It can later be woken by the dispatcher's existing `WORKER_WAKE_URL` POST. Without it, Anna's screens would never get replies.

## Test personas (realistic private contexts)

**`worker/tests/personas/*.json`:** 6 travellers. Each has:
- a private backstory;
- a speaking style (terse, rambling, vague, emoji, defers to the group);
- facts they only reveal when asked;
- ground-truth constraints.

Examples:
- a nurse coming off night shifts (no red-eyes; about $900 for the whole trip, flexible to $1,000 to arrive rested);
- someone between jobs who says "whatever works" but truly can't go above $600 (tests "don't assume");
- a light sleeper with a CPAP who needs their own room but won't say why;
- an organizer who doesn't care about money but hates layovers;
- a surfer with a board bag;
- a parent who has to be back by Sunday afternoon.

**Scripted traveller:** answers from the persona's topic→answer map. Combined with a scripted extractor, this gives deterministic workflow tests.

**Claude traveller:** plays the persona. It answers only what's asked, in character, and never reveals ground truth unprompted. Live simulations run as `tandem-worker intake simulate --personas … --live`.

**Scoring per run:**
- questions asked;
- ground-truth items captured;
- **assumed items, which must be 0**;
- private reasons leaked to the room, which must be 0.

## Verification

1. **Module** (`spacetimedb/tests/test_intake.py`):
   - brief draft → propose → confirm writes settings and starts intakes;
   - views show only one's own intake;
   - `source_quote` must be in the cited message;
   - `confirm_intake` blocks on PENDING patches;
   - publish is blocked by `INTAKE_INCOMPLETE`;
   - `room_intake_progress` stays neutral.
2. **Worker unit tests:**
   - verify/drop rules (missing quote, number not in quote, "about $800" with unknown scope becomes UNCLEAR);
   - topic coverage ordering;
   - question cap of 5;
   - just-in-time nudges: a shared 2-bedroom plan with nobody's rooming answer asks once and records the answer as a constraint; a second identical gap doesn't ask again.
3. **Worker workflow tests:** all 6 scripted personas reach SUMMARY in ≤3 questions (cap 5), with exact ground-truth items, zero assumed items, and reasons never in room output. "Whatever works" yields a clarifying question, not a value.
4. **Integration** (`scripts/backend-integration.sh`): full room on a local SpacetimeDB.
   - description → brief → 4 intakes → confirmations → PLAN with derived searches (fixture research);
   - check that research started before the last confirmation, and that the proposal waited for it.
5. **Live on Maincloud:** 4 Claude personas with live Claude and Apify. Report transcripts, questions per person, capture accuracy, and the resulting trip.
6. **Docs:** `contracts/backend.md` (intake section and new keys), `demo.md`, Devpost challenges. Republish Maincloud; the changes are additive.

## Calls I made (check these)

1. The only inline confirm is the origin; everything else is confirmed once, in the summary.
2. Up-front intake covers only origin and dates, money, and agent settings: at most 2 topics per question, a cap of 5, usually 2–3. Rooming, bag, arrival times and stops are captured if mentioned, otherwise asked just in time, once.
3. Money must be covered explicitly: a budget, or a stated "no limit".
4. Gates apply only to rooms that have a trip brief.
5. The transfer search runs after the flight solve, not early.
6. Early search is a warm-up and preview; proposals always use prices less than 15 minutes old.
7. Non-intake INTERPRET jobs finish as `NOT_HANDLED` until an owner-chat worker exists.
8. Add a minimal `serve` polling loop, in Skylar's area, so real users get replies.

## Order of work

1. Module work, with tests:
   - tables, reducers and views;
   - the origin quick-confirm;
   - batched `confirm_intake` and `withdraw_constraint_patches`;
   - gates for rooms with a brief;
   - the arrival-window check at publish.
2. Worker contracts, ports, the intake graph, just-in-time nudges in the personal agent, `serve`, and scripted personas, with tests.
3. Searches derived from room data:
   - `max_stops` and `arrival_window` respected by search, solver and validator;
   - the coordinator gate and search-early;
   - ledger reuse;
   - the transfer search after the solve.
4. Claude personas, a live simulation on Maincloud, then docs.

Message Skylar about the intake graph, `serve`, and the coordinator changes before merging. Write this design to `docs/superpowers/specs/2026-10-24-conversational-intake-design.md` once approved.
