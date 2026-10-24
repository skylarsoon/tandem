// The guided walkthrough: one SpacetimeDB idea per step. Each step is small on
// purpose: a one-line takeaway, a tiny diagram, two or three sentences. The
// Tandem specifics, real rows and code are folded away until asked for.

export type TourFocus = { kind: "table" | "entry" | "concept"; name: string };
export type TourStep = {
  chapter: string;
  title: string;
  takeaway: string; // the one line to remember
  diagram: string[]; // boxes left to right
  plain: string; // 2-3 sentences, no jargon
  tandem: string; // how Tandem uses it (folded)
  look?: { table: string; columns: string[]; note: string };
  try?: { label: string; action: string; person: string; args?: Record<string, unknown>; expect: string };
  deeper: TourFocus[];
};

export const CHAPTERS = ["Basics", "Writing", "Reading", "Onboarding", "AI worker", "Plans"];

export const TOUR: TourStep[] = [
  {
    chapter: "Basics", title: "A database that is also the server",
    takeaway: "Our rules run inside the database, so there's no separate API server.",
    diagram: ["App / worker", "Module (our rules)", "Tables"],
    plain: "Normally a server sits between apps and the database. SpacetimeDB runs our code (the module) inside the database itself, and apps call it directly.",
    tandem: "Tandem's module is TypeScript in spacetimedb/src, published to Maincloud as tandem-dev. The browser app, the dispatcher and the Python AI worker all talk to it.",
    look: { table: "room", columns: ["id", "goal", "status"], note: "This room's row." },
    deeper: [{ kind: "concept", name: "table" }, { kind: "concept", name: "reducer" }],
  },
  {
    chapter: "Basics", title: "Every table is private",
    takeaway: "No browser can read a table directly.",
    diagram: ["Browser", "✕ table", "✓ view (made for you)"],
    plain: "A table can be public or private. All 34 of ours are private, so people only ever see data through views built for them.",
    tandem: "That's the core privacy guarantee: Bob's browser is never even sent Alice's budget.",
    try: { label: "Ben tries to read the raw limits table", action: "read_private_table", person: "ben", expect: "SpacetimeDB refuses." },
    deeper: [{ kind: "concept", name: "table" }, { kind: "concept", name: "view" }],
  },
  {
    chapter: "Basics", title: "The database knows who is calling",
    takeaway: "Every call carries the caller's identity, and it can't be faked.",
    diagram: ["Token", "Identity 0x…", "ctx.sender in our code"],
    plain: "Each person or service has an identity tied to their token. Our code reads it to decide what that caller may do.",
    tandem: "Only you can confirm your own limits, only the organizer can confirm the trip, and only allow-listed identities can act as the AI worker.",
    look: { table: "membership", columns: ["display_name", "role"], note: "People in this room." },
    deeper: [{ kind: "concept", name: "identity" }, { kind: "entry", name: "add_worker" }],
  },
  {
    chapter: "Writing", title: "Every write is one transaction",
    takeaway: "A reducer's changes all happen together, or none do.",
    diagram: ["Button", "Reducer", "Several tables, all at once"],
    plain: "A reducer is a function that changes data. Everything it writes commits together, at one timestamp.",
    tandem: "Setting a budget, in one transaction: saves the limit, bumps the room's revision, cancels the old planning job, queues a new one, and writes a room event plus a private one.",
    try: { label: "Ben sets a $700 budget", action: "set_budget", person: "ben", args: { dollars: 700, firm: true, reason: "saving for a car" }, expect: "Several rows, one transaction." },
    deeper: [{ kind: "concept", name: "reducer" }, { kind: "entry", name: "set_constraint" }],
  },
  {
    chapter: "Writing", title: "A refusal changes nothing",
    takeaway: "If a rule fails, the whole call is undone.",
    diagram: ["Reducer", "Rule fails", "Nothing written"],
    plain: "A reducer can refuse with an error code. Because it's one transaction, a refusal never leaves half the work done.",
    tandem: "Examples: confirming onboarding without a budget (INTAKE_INCOMPLETE), approving an outdated plan (STALE_SNAPSHOT), a crashed worker writing late (LEASE_LOST).",
    try: { label: "Ben joins a room he's already in", action: "join", person: "ben", expect: "Refused; zero rows written." },
    deeper: [{ kind: "entry", name: "join_room" }],
  },
  {
    chapter: "Writing", title: "Revisions: old work can't sneak in",
    takeaway: "Any change bumps a counter, and work based on the old count is dropped.",
    diagram: ["Change", "Revision +1", "Old job cancelled · new job queued"],
    plain: "Each room counts its changes. Work started before a change is cancelled, and late results from it are rejected.",
    tandem: "Three counters per room: input, members, permissions. A change also marks the published plan as outdated and its approvals as stale.",
    look: { table: "job", columns: ["id", "kind", "status"], note: "This room's jobs; SUPERSEDED ones were cancelled by a change." },
    try: { label: "Ava changes the arrival window", action: "arrival_window", person: "ava", args: { hours: 1 }, expect: "Revision goes up; planning restarts." },
    deeper: [{ kind: "concept", name: "revision" }],
  },
  {
    chapter: "Reading", title: "Views: everyone gets their own answer",
    takeaway: "The same view returns different rows for different people.",
    diagram: ["my_constraints", "Ava → Ava's limits", "Ben → Ben's limits"],
    plain: "A view is a read-only query computed for whoever asks. It only returns what that person is allowed to see.",
    tandem: "my_… views return your own rows; room_… views only rooms you're in. In the Sandbox, switch between ava, ben and cleo to see it.",
    deeper: [{ kind: "concept", name: "view" }, { kind: "entry", name: "my_constraints" }],
  },
  {
    chapter: "Reading", title: "Screens update themselves",
    takeaway: "When a write commits, everyone affected gets the change pushed.",
    diagram: ["Write commits", "SpacetimeDB", "Push to subscribed screens"],
    plain: "Apps subscribe to views. After every transaction, SpacetimeDB sends each subscriber just the rows that changed for them.",
    tandem: "Agent messages appear for everyone in the room at once. The dispatcher subscribes too: it wakes the AI worker the instant a job appears.",
    deeper: [{ kind: "concept", name: "subscription" }, { kind: "entry", name: "worker_pending_jobs" }],
  },
  {
    chapter: "Onboarding", title: "From the organizer's words to room rules",
    takeaway: "Words become rules only after the organizer confirms.",
    diagram: ["Organizer's words", "Agent's proposed brief", "Confirmed → room settings"],
    plain: "The organizer describes the trip, their agent proposes a structured version, and the organizer confirms it.",
    tandem: "Confirming the brief writes destination, dates and what to plan into the room, and starts everyone's onboarding, in one transaction.",
    look: { table: "trip_brief", columns: ["state", "description"], note: "This room's brief." },
    try: { label: "Ava describes the trip", action: "describe_trip", person: "ava", args: { text: "Three of us, Honolulu Feb 12 to 16 2027, a beach house together and an airport ride." }, expect: "A draft brief and a job for Ava's agent." },
    deeper: [{ kind: "entry", name: "confirm_trip_brief" }],
  },
  {
    chapter: "Onboarding", title: "Agents propose, people confirm",
    takeaway: "An agent can't set your limits; it can only propose, quoting you.",
    diagram: ["You say it", "Agent proposes (with your quote)", "You confirm → limit"],
    plain: "Proposals must quote your exact words, and the database checks the quote is really in your message.",
    tandem: "Your airport is confirmed with one tap so searches can start. Everything else is confirmed together in one summary.",
    look: { table: "constraint_patch", columns: ["owner", "state"], note: "Proposals in this room." },
    deeper: [{ kind: "entry", name: "propose_constraint_patch" }, { kind: "entry", name: "confirm_intake" }],
  },
  {
    chapter: "AI worker", title: "Jobs and leases",
    takeaway: "The worker borrows a job for a limited time and must prove it still holds it.",
    diagram: ["Claim job", "Lease + attempt #", "Every write checked"],
    plain: "Slow work (searching, thinking) happens outside the database. The worker claims a job and gets a lease that expires unless renewed.",
    tandem: "If the worker crashes, the lease expires, another worker takes the job with a higher attempt number, and the old one's late writes are refused.",
    look: { table: "job", columns: ["kind", "status", "attempt"], note: "This room's jobs." },
    deeper: [{ kind: "concept", name: "lease" }, { kind: "entry", name: "claim_job" }],
  },
  {
    chapter: "AI worker", title: "Timers are just rows",
    takeaway: "Put a row with a time in a schedule table, and a reducer runs then.",
    diagram: ["Insert timer row", "Time arrives", "Reducer runs"],
    plain: "Scheduled reducers are driven by data, so timers survive restarts.",
    tandem: "lease_timer frees jobs from crashed workers; review_reminder nudges only people who opted in.",
    deeper: [{ kind: "concept", name: "schedule" }, { kind: "entry", name: "expire_lease" }],
  },
  {
    chapter: "AI worker", title: "Retries never count twice",
    takeaway: "A request key makes a retry return the first result.",
    diagram: ["Call with key", "Receipt stored", "Retry → same result"],
    plain: "Networks fail after work is done. Storing a receipt per request key turns a retry into a harmless replay.",
    tandem: "Every worker write has a key; paid steps (Claude, flight searches) go in a ledger first, so a restart replays instead of paying again.",
    deeper: [{ kind: "concept", name: "receipt" }],
  },
  {
    chapter: "Plans", title: "Agents' messages are checked first",
    takeaway: "A message reaches the room only if it passes every rule.",
    diagram: ["Agent writes", "Checks (facts, privacy, permission)", "Room sees it"],
    plain: "Each agent message must cite facts the room holds and must not contain anyone's private reason.",
    tandem: "Also checked: the thread is open, turns aren't used up, and the owner allowed it (e.g. objecting needs 'speak up' or permission).",
    look: { table: "agent_message", columns: ["type", "utterance"], note: "What agents said here." },
    deeper: [{ kind: "entry", name: "submit_agent_message" }],
  },
  {
    chapter: "Plans", title: "Publishing re-checks everything",
    takeaway: "The database doesn't take the worker's word for a plan.",
    diagram: ["Worker's plan", "Re-check every limit", "Publish + fingerprint"],
    plain: "Publishing checks every hard limit again, in the same transaction that publishes.",
    tandem: "It stores the exact terms with a fingerprint, each person's private cost line, and a pending approval for everyone. Room #9 has a published plan to look at.",
    look: { table: "proposal", columns: ["id", "state"], note: "Plans in this room." },
    deeper: [{ kind: "entry", name: "publish_proposal" }],
  },
  {
    chapter: "Plans", title: "Approvals bind to the exact plan",
    takeaway: "You approve this exact version, or nothing.",
    diagram: ["Approve (version + fingerprint)", "Match?", "All approved → agreed"],
    plain: "An approval names the plan's version and fingerprint. If the plan changed, the approval is refused.",
    tandem: "The plan is agreed only when every person accepted those exact terms. The worker can never approve for anyone.",
    deeper: [{ kind: "entry", name: "respond_to_proposal" }],
  },
];
