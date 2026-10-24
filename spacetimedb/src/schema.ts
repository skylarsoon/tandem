import { schema, table, t } from 'spacetimedb/server';

// All tables are private. Clients read through per-caller views (views.ts)
// and write through reducers/procedures that enforce the rules.
//
// Status-like columns are plain strings validated against the constants in
// guards.ts, so Python and SQL clients see readable values ("PENDING").
// Fast-evolving payloads (constraint values, terms, message payloads) are JSON
// strings validated by the worker's Pydantic models.

// ---------- Access ----------

const config = table(
  { name: 'config' },
  {
    id: t.u32().primaryKey(),
    owner: t.identity(),
    reminderDelaySeconds: t.u32(),
  }
);

const worker = table(
  { name: 'worker' },
  {
    identity: t.identity().primaryKey(),
    label: t.string(),
  }
);

// ---------- Rooms and people ----------

const room = table(
  {
    name: 'room',
    indexes: [{ accessor: 'byInviteCode', algorithm: 'btree', columns: ['inviteCode'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    goal: t.string(),
    organizer: t.identity(),
    status: t.string(),
    inputRevision: t.u64(),
    membershipRevision: t.u64(),
    permissionRevision: t.u64(),
    inviteCode: t.string(),
    // Shared, non-private room rules, e.g. {"arrival_overlap_seconds": 7200}.
    settingsJson: t.string(),
    createdAt: t.timestamp(),
  }
);

const membership = table(
  {
    name: 'membership',
    indexes: [
      { accessor: 'byRoomMember', algorithm: 'btree', columns: ['roomId', 'member'] },
      { accessor: 'byMember', algorithm: 'btree', columns: ['member'] },
    ],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    member: t.identity(),
    displayName: t.string(),
    role: t.string(),
    active: t.bool(),
    joinedAt: t.timestamp(),
  }
);

const agent = table(
  {
    name: 'agent',
    indexes: [{ accessor: 'byRoom', algorithm: 'btree', columns: ['roomId'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    kind: t.string(),
    membershipId: t.option(t.u64()),
  }
);

const profile = table(
  { name: 'profile' },
  {
    owner: t.identity().primaryKey(),
    displayName: t.string(),
    avatarRef: t.string(),
    selfDescription: t.string(),
    priorityRanking: t.array(t.string()),
    revision: t.u64(),
    updatedAt: t.timestamp(),
  }
);

const participationPreferences = table(
  { name: 'participation_preferences' },
  {
    owner: t.identity().primaryKey(),
    speakUpForMe: t.bool(),
    askMeFirst: t.bool(),
    giveMeTime: t.bool(),
    remindersOptedIn: t.bool(),
    confirmed: t.bool(),
    confirmedAt: t.option(t.timestamp()),
    proposedBy: t.string(),
    revision: t.u64(),
  }
);

// ---------- Live presence and activity ----------

// One row per human identity; `connections` counts open tabs/devices.
const presence = table(
  { name: 'presence' },
  {
    identity: t.identity().primaryKey(),
    connections: t.u32(),
    online: t.bool(),
    lastSeen: t.timestamp(),
  }
);

// What each agent is doing right now, set by the worker under its lease.
// A normal private table (not an event table) so a per-room view can filter it.
const agentStatus = table(
  { name: 'agent_status' },
  {
    agentId: t.u64().primaryKey(),
    roomId: t.u64().index('btree'),
    jobId: t.u64(),
    activity: t.string(),
    threadId: t.option(t.u64()),
    updatedAt: t.timestamp(),
  }
);

// Scheduled nudges for reviewers who did not ask for time.
export const reviewReminder = table(
  { name: 'review_reminder' },
  {
    scheduledId: t.u64().primaryKey().autoInc(),
    scheduledAt: t.scheduleAt(),
    proposalId: t.u64(),
    member: t.identity(),
  }
);

// ---------- Private inputs ----------

const participantConstraint = table(
  {
    name: 'participant_constraint',
    indexes: [
      { accessor: 'byRoomOwnerKey', algorithm: 'btree', columns: ['roomId', 'owner', 'key'] },
      { accessor: 'byOwner', algorithm: 'btree', columns: ['owner'] },
    ],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    owner: t.identity(),
    key: t.string(),
    valueJson: t.string(),
    unit: t.string(),
    hardness: t.string(),
    authorizedRangeJson: t.option(t.string()),
    privateRationale: t.option(t.string()),
    confirmedAt: t.timestamp(),
    revision: t.u64(),
  }
);

const constraintPatch = table(
  {
    name: 'constraint_patch',
    indexes: [{ accessor: 'byOwner', algorithm: 'btree', columns: ['owner'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    owner: t.identity(),
    jobId: t.u64(),
    patchJson: t.string(),
    sourceMessageId: t.option(t.u64()),
    state: t.string(),
    createdAt: t.timestamp(),
  }
);

const privateMessage = table(
  {
    name: 'private_message',
    indexes: [{ accessor: 'byOwner', algorithm: 'btree', columns: ['owner'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    owner: t.identity(),
    sender: t.string(),
    body: t.string(),
    jobId: t.option(t.u64()),
    createdAt: t.timestamp(),
  }
);

// ---------- Onboarding ----------

// The organizer's own words about the trip, the structured brief their agent
// proposes from them (destination, dates, what to plan), and its state
// DRAFT → PROPOSED → CONFIRMED. A confirmed brief is shared room context.
const tripBrief = table(
  { name: 'trip_brief' },
  {
    roomId: t.u64().primaryKey(),
    description: t.string(),
    briefJson: t.string(),
    unclearJson: t.string(),
    state: t.string(),
    revision: t.u64(),
    updatedAt: t.timestamp(),
  }
);

// One traveller's onboarding conversation for one trip. coverageJson maps each
// essential topic to OPEN | ANSWERED | UNCLEAR | SKIPPED.
// State: ASKING → SUMMARY → CONFIRMED (a new message in SUMMARY goes back to ASKING).
const intake = table(
  {
    name: 'intake',
    indexes: [
      { accessor: 'byRoom', algorithm: 'btree', columns: ['roomId'] },
      { accessor: 'byOwner', algorithm: 'btree', columns: ['owner'] },
    ],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    owner: t.identity(),
    memberId: t.u64(),
    state: t.string(),
    coverageJson: t.string(),
    questionCount: t.u32(),
    briefRevision: t.u64(),
    createdAt: t.timestamp(),
    updatedAt: t.timestamp(),
  }
);

// Whose conversation an INTAKE job serves (purpose BRIEF or INTAKE). INTERPRET
// jobs find their owner through private_message.jobId instead.
const jobSubject = table(
  { name: 'job_subject' },
  {
    jobId: t.u64().primaryKey(),
    memberId: t.u64(),
    purpose: t.string(),
  }
);

// ---------- Questions and scoped permissions ----------

const question = table(
  {
    name: 'question',
    indexes: [
      { accessor: 'byOwner', algorithm: 'btree', columns: ['owner'] },
      { accessor: 'byJob', algorithm: 'btree', columns: ['jobId'] },
      { accessor: 'byRoom', algorithm: 'btree', columns: ['roomId'] },
    ],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    requestKey: t.string().unique(),
    roomId: t.u64(),
    owner: t.identity(),
    jobId: t.u64(),
    version: t.u32(),
    kind: t.string(),
    actionJson: t.string(),
    inputRevision: t.u64(),
    permissionRevision: t.u64(),
    prompt: t.string(),
    answerSchemaJson: t.string(),
    threadId: t.option(t.u64()),
    state: t.string(),
    answerJson: t.option(t.string()),
    answeredAt: t.option(t.timestamp()),
    createdAt: t.timestamp(),
  }
);

const actionGrant = table(
  {
    name: 'action_grant',
    indexes: [
      { accessor: 'byRoom', algorithm: 'btree', columns: ['roomId'] },
      { accessor: 'byOwnerKindRef', algorithm: 'btree', columns: ['owner', 'kind', 'actionRef'] },
    ],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    questionId: t.u64().unique(),
    roomId: t.u64(),
    owner: t.identity(),
    kind: t.string(),
    actionRef: t.string(),
    actionDigest: t.string(),
    inputRevision: t.u64(),
    membershipRevision: t.u64(),
    granted: t.bool(),
    createdAt: t.timestamp(),
  }
);

// ---------- Jobs ----------

const job = table(
  {
    name: 'job',
    indexes: [
      { accessor: 'byStatus', algorithm: 'btree', columns: ['status'] },
      { accessor: 'byRoom', algorithm: 'btree', columns: ['roomId'] },
    ],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    kind: t.string(),
    status: t.string(),
    reason: t.string(),
    inputRevision: t.u64(),
    membershipRevision: t.u64(),
    permissionRevision: t.u64(),
    attempt: t.u32(),
    leaseOwner: t.option(t.identity()),
    leaseExpiresAt: t.option(t.timestamp()),
    graphThreadRef: t.option(t.string()),
    waitingQuestionIds: t.array(t.u64()),
    outcome: t.option(t.string()),
    outcomeJson: t.option(t.string()),
    createdAt: t.timestamp(),
    updatedAt: t.timestamp(),
  }
);

export const leaseTimer = table(
  { name: 'lease_timer' },
  {
    scheduledId: t.u64().primaryKey().autoInc(),
    scheduledAt: t.scheduleAt(),
    jobId: t.u64(),
    attempt: t.u32(),
  }
);

const commandReceipt = table(
  { name: 'command_receipt' },
  {
    key: t.string().primaryKey(),
    caller: t.identity(),
    requestDigest: t.string(),
    outcomeJson: t.string(),
    createdAt: t.timestamp(),
  }
);

// Receipt-backed external operations (model calls, research, solver runs) so a
// re-claimed attempt reuses results instead of paying twice, plus per-generation
// call budgets that survive crashes.
const operationRecord = table(
  { name: 'operation_record' },
  {
    key: t.string().primaryKey(),
    jobId: t.u64().index('btree'),
    kind: t.string(),
    requestDigest: t.string(),
    status: t.string(),
    resultJson: t.option(t.string()),
    updatedAt: t.timestamp(),
  }
);

const jobBudget = table(
  { name: 'job_budget' },
  {
    jobId: t.u64().primaryKey(),
    modelCalls: t.u32(),
    researchCalls: t.u32(),
  }
);

// ---------- Agent dialogue ----------

const negotiationRound = table(
  {
    name: 'negotiation_round',
    indexes: [{ accessor: 'byJobRound', algorithm: 'btree', columns: ['jobId', 'roundId'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    jobId: t.u64(),
    roundId: t.string(),
    turnBudget: t.u32(),
    turnsUsed: t.u32(),
    state: t.string(),
  }
);

const conversationThread = table(
  {
    name: 'conversation_thread',
    indexes: [
      { accessor: 'byRoom', algorithm: 'btree', columns: ['roomId'] },
      { accessor: 'byJob', algorithm: 'btree', columns: ['jobId'] },
    ],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    jobId: t.u64(),
    roundId: t.string(),
    topicRef: t.string(),
    participantAgentIds: t.array(t.u64()),
    inputRevision: t.u64(),
    turnBudget: t.u32(),
    turnsUsed: t.u32(),
    state: t.string(),
    closureReason: t.option(t.string()),
    successorOf: t.option(t.u64()),
    openedAt: t.timestamp(),
  }
);

const agentMessage = table(
  {
    name: 'agent_message',
    indexes: [
      { accessor: 'byRoom', algorithm: 'btree', columns: ['roomId'] },
      { accessor: 'byThread', algorithm: 'btree', columns: ['threadId'] },
      { accessor: 'byJob', algorithm: 'btree', columns: ['jobId'] },
    ],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    messageKey: t.string().unique(),
    schemaVersion: t.u32(),
    roomId: t.u64(),
    jobId: t.u64(),
    attempt: t.u32(),
    roundId: t.string(),
    threadId: t.u64(),
    senderAgentId: t.u64(),
    recipientAgentId: t.u64(),
    type: t.string(),
    replyTo: t.option(t.u64()),
    draftRef: t.option(t.string()),
    proposalId: t.option(t.u64()),
    proposalVersion: t.option(t.u32()),
    inputRevision: t.u64(),
    membershipRevision: t.u64(),
    permissionRevision: t.u64(),
    payloadJson: t.string(),
    utterance: t.string(),
    claimRefs: t.array(t.string()),
    evidenceRefs: t.array(t.string()),
    effectiveAudience: t.string(),
    audienceOwner: t.option(t.identity()),
    sanitizationStatus: t.string(),
    grantRef: t.option(t.u64()),
    processingState: t.string(),
    processedReplyIds: t.array(t.u64()),
    createdAt: t.timestamp(),
  }
);

const decisionEvent = table(
  {
    name: 'decision_event',
    indexes: [{ accessor: 'byRoom', algorithm: 'btree', columns: ['roomId'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    type: t.string(),
    audience: t.string(),
    owner: t.option(t.identity()),
    safePayloadJson: t.string(),
    createdAt: t.timestamp(),
  }
);

const candidate = table(
  {
    name: 'candidate',
    indexes: [{ accessor: 'byRoomRef', algorithm: 'btree', columns: ['roomId', 'candidateRef'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    candidateRef: t.string(),
    candidateSetVersion: t.u32(),
    category: t.string(),
    mode: t.string(),
    sourceUrl: t.string(),
    observedAt: t.string(),
    factsJson: t.string(),
    missingFields: t.array(t.string()),
    inputRevision: t.u64(),
    membershipRevision: t.u64(),
    recordedAt: t.timestamp(),
  }
);

// ---------- Proposals and consent ----------

const proposal = table(
  {
    name: 'proposal',
    indexes: [{ accessor: 'byRoomStage', algorithm: 'btree', columns: ['roomId', 'stage'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    stage: t.string(),
    version: t.u32(),
    jobId: t.u64(),
    inputRevision: t.u64(),
    membershipRevision: t.u64(),
    permissionRevision: t.u64(),
    termsJson: t.string(),
    termsDigest: t.string(),
    sharedSummaryJson: t.string(),
    validationJson: t.string(),
    evidenceRefs: t.array(t.string()),
    affectedMembers: t.array(t.identity()),
    state: t.string(),
    createdAt: t.timestamp(),
  }
);

const participantImpact = table(
  {
    name: 'participant_impact',
    indexes: [{ accessor: 'byOwner', algorithm: 'btree', columns: ['owner'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    proposalId: t.u64(),
    roomId: t.u64(),
    owner: t.identity(),
    impactJson: t.string(),
  }
);

const consent = table(
  {
    name: 'consent',
    indexes: [
      { accessor: 'byProposalMember', algorithm: 'btree', columns: ['proposalId', 'member'] },
      { accessor: 'byMember', algorithm: 'btree', columns: ['member'] },
    ],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    proposalId: t.u64(),
    roomId: t.u64(),
    member: t.identity(),
    termsDigest: t.string(),
    response: t.string(),
    note: t.option(t.string()),
    updatedAt: t.timestamp(),
  }
);

const milestone = table(
  {
    name: 'milestone',
    indexes: [{ accessor: 'byRoomStage', algorithm: 'btree', columns: ['roomId', 'stage'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    roomId: t.u64(),
    stage: t.string(),
    state: t.string(),
    proposalId: t.option(t.u64()),
    updatedAt: t.timestamp(),
  }
);

const spacetimedb = schema({
  config,
  worker,
  room,
  membership,
  agent,
  profile,
  participationPreferences,
  presence,
  agentStatus,
  reviewReminder,
  participantConstraint,
  constraintPatch,
  privateMessage,
  tripBrief,
  intake,
  jobSubject,
  question,
  actionGrant,
  job,
  leaseTimer,
  commandReceipt,
  operationRecord,
  jobBudget,
  negotiationRound,
  conversationThread,
  agentMessage,
  decisionEvent,
  candidate,
  proposal,
  participantImpact,
  consent,
  milestone,
});
export default spacetimedb;

export {
  room,
  membership,
  agent,
  profile,
  participationPreferences,
  participantConstraint,
  constraintPatch,
  privateMessage,
  tripBrief,
  intake,
  question,
  job,
  conversationThread,
  agentMessage,
  decisionEvent,
  candidate,
  milestone,
};
