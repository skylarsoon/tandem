// Plain explanations of each part of the Tandem module and the SpacetimeDB idea
// behind it. Which tables each reducer/view touches is derived from the source
// (module-map.ts); this file only says what things are for and why it matters.

export const CONCEPTS: Record<string, { title: string; text: string }> = {
  table: {
    title: "Tables",
    text: "Rows live in tables inside the database, in memory, with a log on disk. All 32 Tandem tables are private: no client can read or subscribe to them directly. Only reducers (writes) and views (reads) touch them.",
  },
  reducer: {
    title: "Reducers = transactions",
    text: "A reducer is a function that runs inside the database as one transaction. Everything it writes commits together or not at all, and every row it writes carries the same timestamp. That's why the Replay can group rows into calls. A reducer can throw an error (\"CODE: detail\"), and then nothing it did is kept.",
  },
  procedure: {
    title: "Procedures",
    text: "Like reducers, but they can return a value (e.g. claim_job returns the lease). Inside, ctx.withTx(...) runs a transaction that may be retried, so it must be safe to run twice. Ours return JSON {ok, code} instead of throwing.",
  },
  view: {
    title: "Views = privacy",
    text: "A view is a read-only query computed for the person asking (ctx.sender). my_private_messages returns only your rows; room_messages only rooms you're in. Browsers subscribe to views, so private data is never even sent to the wrong person. Views can only read through indexes.",
  },
  subscription: {
    title: "Subscriptions",
    text: "Clients subscribe to views over a WebSocket. When a transaction commits, SpacetimeDB pushes the changed rows to every subscriber whose view result changed. That's how every screen updates instantly without polling.",
  },
  identity: {
    title: "Identity",
    text: "Every caller has an Identity (a 256-bit id, shown as 0x…) tied to their token. Reducers read ctx.sender to know who is calling; that's how 'only the owner can confirm their own limits' is enforced.",
  },
  revision: {
    title: "Revisions",
    text: "Each room has input, membership and permission revisions. Any change to what people want bumps one, cancels (supersedes) the planning job working on the old inputs, and queues a new one. Old work can't sneak in late: every worker write is checked against the revisions it started with.",
  },
  lease: {
    title: "Jobs, leases and fencing",
    text: "The AI worker claims a job and gets a lease (it expires unless renewed) and an attempt number. Every worker write must quote both. If a worker crashes, a scheduled reducer expires the lease, another worker claims the job with a higher attempt, and anything the old one tries later is rejected (LEASE_LOST).",
  },
  receipt: {
    title: "Receipts (exactly-once)",
    text: "Worker calls carry a request key. The first call stores a receipt with the result; a retry with the same key returns the same result instead of doing the work twice. A different request under the same key is refused (KEY_BODY_MISMATCH).",
  },
  schedule: {
    title: "Scheduled reducers",
    text: "Inserting a row into a schedule table (lease_timer, review_reminder) asks SpacetimeDB to call a reducer at that time. It's a timer that survives restarts, because it's just data.",
  },
};

export const TABLES: Record<string, string> = {
  config: "Single row: who owns the database (the identity that published it) and settings like the reminder delay.",
  worker: "Allow-list of service identities (the AI worker, the dispatcher). Worker-only reducers check the caller is on this list.",
  room: "One row per trip: goal, organizer, status, invite code, shared settings (destination, dates, what to plan, arrival window) and the three revisions.",
  membership: "Who is in which room, their display name and role. Every privacy check starts here: 'is the caller a member of this room?'.",
  agent: "Each person's AI agent plus one mediator per room. Agent messages are sent between these ids.",
  profile: "A person's profile across all rooms (name, avatar, short self-description).",
  participation_preferences: "How a person's agent may act for them: speak up, ask first, give time. Unconfirmed until they confirm. Changing them restarts planning in all their rooms.",
  participant_constraint: "A person's confirmed private limits for one trip: budget, airport, bag, own room… with the private reason. Only they (and the worker) can read it.",
  constraint_patch: "A change an agent proposed, quoting the person's exact words. It does nothing until the person confirms it, which writes participant_constraint.",
  private_message: "The private chat between a person and their own agent. Nobody else's browser can read it.",
  trip_brief: "The organizer's description of the trip and the structured brief (destination, airport, dates, what to plan) their agent proposed and they confirmed.",
  intake: "Each person's onboarding: asking → summary → confirmed, which essentials are covered, and how many questions were asked (max 5, enforced here).",
  job_subject: "Which person an onboarding job is for, so the worker loads only that person's conversation.",
  question: "A private question to one person (confirm settings, may I object, may I accept paying more, a just-in-time question). Holds the exact action it would allow and the answer.",
  action_grant: "A permission someone gave by answering yes, scoped to one exact action and room revision. It never counts as approving a plan.",
  job: "The work queue: PLAN (search, solve, negotiate, publish), INTERPRET (read a private message), INTAKE (onboarding). Holds status, attempt, lease, revisions and outcome.",
  lease_timer: "Schedule table: one row per active lease. When it comes due, expire_lease runs and frees the job if the worker stopped renewing.",
  command_receipt: "Idempotency receipts for worker calls: request key → result, so retries never apply twice.",
  operation_record: "The worker's ledger of paid or slow operations (model calls, searches) per job, so a restarted worker replays instead of paying again.",
  job_budget: "Turn and call budgets per job and round, so agents can't talk forever.",
  negotiation_round: "Rounds of agent discussion within a planning job.",
  conversation_thread: "A thread between specific agents, with a turn budget. Closed when superseded or out of turns.",
  agent_message: "Every message an agent sent: type (PROPOSE, OBJECT…), the words, the facts it cites, and the audience (room, one owner, or internal).",
  agent_status: "What each agent is doing right now (thinking, searching, waiting for its owner), for the live avatars.",
  decision_event: "Room events and private notices: someone joined, requirements updated, no plan fits (with neutral text), reminders. Room-wide or for one person.",
  candidate: "Every flight, stay, transfer and idea found by search, with source, observation time and whether its price is complete.",
  proposal: "A published plan: exact terms, a fingerprint (digest) of them, the room-safe summary, and its state (awaiting review, agreed, superseded).",
  participant_impact: "Each person's private line for a plan: what they pay and why (e.g. $125 more to land with the group).",
  consent: "Each person's response to one exact plan version and fingerprint. The plan is agreed only when every affected person accepted those exact terms.",
  proposal_burden: "What each person would give up per decision (0 to 1) if a plan is agreed, as the optimizer scored it at publish. Frozen; nothing counts yet.",
  compromise_entry: "The fairness ledger: compromise people actually approved, per decision. Sum of ACTIVE rows = what someone has given up so far (H_i); a newer agreed decision supersedes, a withdrawn approval revokes.",
  milestone: "Progress stages for the room's journey view (understand, dates, travel, stay, complete).",
  presence: "Who is connected right now; maintained by the connect/disconnect lifecycle reducers.",
  review_reminder: "Schedule table: reminders to review a plan, only for people who opted in and didn't ask for time.",
};

export const ENTRIES: Record<string, string> = {
  init: "Runs once when the module is first published: records the database owner in config.",
  on_connect: "Runs when a browser connects with the SDK: marks the person present.",
  on_disconnect: "Runs when a connection closes: marks them away once their last tab closes.",
  add_worker: "Owner only. Adds a service identity (the AI worker or dispatcher) to the allow-list.",
  create_room: "A person creates a trip: room row, their membership and agent, the mediator, milestones, and an invite code. One transaction.",
  join_room: "Join by invite code. Bumps the membership revision (cancelling planning on the old member list) and, if the trip brief is confirmed, starts this person's onboarding.",
  upsert_profile: "Save your profile.",
  confirm_participation_preferences: "Confirm how your agent may act for you. Restarts planning in every room you're in, because what your agent may do changed.",
  set_room_settings: "Organizer changes shared rules (arrival window, what to plan). Bumps the input revision: old plans become stale.",
  set_constraint: "Directly save one of your private limits (the form path). Bumps the input revision, cancels running planning and queues a new job.",
  confirm_constraint_patch: "Accept a change your agent proposed; it becomes a limit.",
  reject_constraint_patch: "Decline a change your agent proposed.",
  submit_private_message: "You write to your own agent: a private_message row and an INTERPRET job, together.",
  set_trip_description: "Organizer writes the trip in their own words; queues their agent to propose a brief.",
  confirm_trip_brief: "Organizer confirms the brief: it becomes the room's settings and everyone's onboarding starts, all in one transaction.",
  confirm_origin: "The one-tap 'Flying from SFO, right?': saves just your airport so searches can start early.",
  confirm_intake: "Confirm your onboarding summary: every accepted item becomes a limit, settings are confirmed, and the room replans once. If anything required is missing, the whole thing is refused and nothing changes.",
  propose_trip_brief: "Worker: the organizer's agent proposes the structured brief.",
  update_intake: "Worker: records which essentials are covered and counts the question asked. Refuses a 6th question.",
  withdraw_constraint_patches: "Worker: withdraws its pending proposals for someone before proposing corrected ones.",
  ask_owner: "Worker: asks one person a private question and closes the thread it came from until they answer.",
  answer_question: "You answer a question. Depending on the kind: a permission (grant), a just-in-time answer (saved as your limit, the waiting job resumes), or settings.",
  claim_job: "Worker (procedure): takes a pending job, gets a lease and a new attempt number, and a timer is scheduled to expire the lease.",
  load_execution_context: "Worker (procedure): one consistent snapshot of everything a job may see, filtered to what that job is allowed to know.",
  check_lease: "Worker (procedure): is my lease still valid?",
  renew_lease: "Worker heartbeat: extends the lease while it's working.",
  expire_lease: "Scheduled: runs when a lease timer comes due. If the worker stopped renewing, the job goes back in the queue and the old attempt is fenced out.",
  park_job: "Worker: the job waits for people's answers and gives up its lease. If the answers are already in, it goes straight back to the queue.",
  finish_job: "Worker: marks a job done with an outcome (e.g. WAITING_FOR_INTAKE, NO_FEASIBLE_CANDIDATE).",
  fail_job: "Worker: marks a job failed with an error code.",
  close_threads: "Worker: closes this job's agent threads (e.g. before a new research round).",
  begin_operation: "Worker (procedure): opens an entry in the operation ledger before a model call or search, so a crash can be resumed without paying twice.",
  complete_operation: "Worker: stores the operation's result in the ledger.",
  record_candidates: "Worker: stores search results for the room, stamped with the revisions they were found under.",
  submit_agent_message: "Worker: an agent sends a message. Checks routing, thread and turn budgets, cited facts, privacy (no private reason text), and permissions, all in one transaction.",
  record_message_processed: "Worker: marks an agent message as handled and links the replies.",
  reply_to_owner: "Worker: an agent writes privately to its owner (questions, notes like 'no plan fits yet: …').",
  propose_constraint_patch: "Worker: an agent proposes a limit for someone, quoting their exact words; the database checks the quote is really in their message.",
  propose_participation_preferences: "Worker: an agent suggests settings from what someone said; they stay unconfirmed until the person confirms.",
  emit_event: "Worker: a room event or a private notice (e.g. NO_FEASIBLE_PLAN). Refused if it contains anyone's private reason.",
  set_agent_activity: "Worker: what an agent is doing right now (thinking, searching…), for live avatars.",
  publish_proposal: "Worker (procedure): publishes a plan in one transaction (terms, fingerprint, private impacts, pending consents, events), after re-checking every hard limit itself. It won't publish until everyone is onboarded.",
  respond_to_proposal: "You respond to a plan (accept, not comfortable, decide later). Bound to the exact version and fingerprint; agreement is recomputed in the same transaction.",
  withdraw_consent: "Take back your acceptance; the plan reopens.",
  set_reminder_delay: "Owner: how long before review reminders fire.",
  send_review_reminder: "Scheduled: sends one review reminder to someone who opted in, only if they still haven't responded.",
};

export const VIEW_AUDIENCE: Record<string, string> = {
  my_: "Only your own rows (filtered by your identity).",
  room_: "Rows of rooms you're a member of.",
  worker_: "Only for allow-listed workers; empty for everyone else.",
};

export function audienceOf(view: string) {
  const prefix = Object.keys(VIEW_AUDIENCE).find(p => view.startsWith(p));
  return prefix ? VIEW_AUDIENCE[prefix] : "Computed for the caller.";
}
