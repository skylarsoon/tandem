import { t } from 'spacetimedb/server';
import type { Identity } from 'spacetimedb';
import spacetimedb from './schema';
import {
  type AgentRow,
  type Ctx,
  type JobRow,
  type RoomRow,
  AUDIENCES,
  CANDIDATE_CATEGORIES,
  CANDIDATE_MODES,
  DEFAULT_ROUND_TURN_BUDGET,
  DEFAULT_THREAD_TURN_BUDGET,
  MESSAGE_SCHEMA_VERSION,
  MESSAGE_TYPES,
  SANITIZATION_STATUSES,
  containsPrivateText,
  digest,
  emitEvent as insertEvent,
  fail,
  nonEmpty,
  oneOf,
  parseJson,
  pythonJson,
  refResolves,
  requireLease,
  requireMembershipById,
  requireRoom,
  requireWorker,
  withReceipt,
} from './guards';
import { constraintFromPatch } from './effects';

const ThreadOpen = t.object('ThreadOpen', {
  topicRef: t.string(),
  participantAgentIds: t.array(t.u64()),
  turnBudget: t.option(t.u32()),
  successorOf: t.option(t.u64()),
});

const CandidateInput = t.object('CandidateInput', {
  candidateRef: t.string(),
  candidateSetVersion: t.u32(),
  category: t.string(),
  mode: t.string(),
  sourceUrl: t.string(),
  observedAt: t.string(),
  factsJson: t.string(),
  missingFields: t.array(t.string()),
});

function requireAgentInRoom(ctx: Ctx, r: RoomRow, agentId: bigint, role: string): AgentRow {
  const a = ctx.db.agent.id.find(agentId);
  if (!a || a.roomId !== r.id) fail('NOT_AUTHORIZED', `${role} agent ${agentId} is not in room ${r.id}`);
  if (a.kind === 'PERSONAL') requireMembershipById(ctx, r.id, a.membershipId!);
  return a;
}

function ownerOf(ctx: Ctx, a: AgentRow): Identity | undefined {
  if (a.kind !== 'PERSONAL') return undefined;
  return ctx.db.membership.id.find(a.membershipId!)!.member;
}

function resolveThread(
  ctx: Ctx,
  j: JobRow,
  r: RoomRow,
  roundId: string,
  threadId: bigint | undefined,
  openThread: { topicRef: string; participantAgentIds: bigint[]; turnBudget: number | undefined; successorOf: bigint | undefined } | undefined,
  sender: AgentRow,
  recipient: AgentRow
) {
  if ((threadId === undefined) === (openThread === undefined)) {
    fail('INVALID_ARGUMENT', 'pass exactly one of threadId or openThread');
  }
  if (openThread) {
    nonEmpty(openThread.topicRef, 'openThread.topicRef');
    const ids = openThread.participantAgentIds;
    if (!ids.includes(sender.id) || !ids.includes(recipient.id)) {
      fail('NOT_AUTHORIZED', 'thread participants must include sender and recipient');
    }
    for (const id of ids) requireAgentInRoom(ctx, r, id, 'participant');
    if (openThread.successorOf !== undefined) {
      const prev = ctx.db.conversationThread.id.find(openThread.successorOf);
      if (!prev || prev.roomId !== r.id || prev.state !== 'CLOSED') {
        fail('INVALID_ARGUMENT', 'successorOf must be a closed thread in this room');
      }
    }
    const budget = openThread.turnBudget ?? DEFAULT_THREAD_TURN_BUDGET;
    if (budget < 1 || budget > DEFAULT_THREAD_TURN_BUDGET) {
      fail('INVALID_ARGUMENT', `turnBudget must be between 1 and ${DEFAULT_THREAD_TURN_BUDGET}`);
    }
    return ctx.db.conversationThread.insert({
      id: 0n,
      roomId: r.id,
      jobId: j.id,
      roundId,
      topicRef: openThread.topicRef,
      participantAgentIds: ids,
      inputRevision: j.inputRevision,
      turnBudget: budget,
      turnsUsed: 0,
      state: 'OPEN',
      closureReason: undefined,
      successorOf: openThread.successorOf,
      openedAt: ctx.timestamp,
    });
  }
  const th = ctx.db.conversationThread.id.find(threadId!);
  if (!th || th.roomId !== r.id) fail('NOT_FOUND', `thread ${threadId}`);
  if (th.jobId !== j.id) fail('THREAD_CLOSED', `thread ${th.id} belongs to an earlier generation`);
  if (th.state !== 'OPEN') {
    if (th.closureReason === 'TURN_BUDGET' || th.closureReason === 'ROUND_BUDGET') {
      fail('TURN_BUDGET_EXHAUSTED', `thread ${th.id} used its turn budget`);
    }
    fail('THREAD_CLOSED', `thread ${th.id} is closed (${th.closureReason ?? 'closed'})`);
  }
  if (th.roundId !== roundId) fail('INVALID_ARGUMENT', `thread ${th.id} belongs to round ${th.roundId}`);
  if (!th.participantAgentIds.includes(sender.id) || !th.participantAgentIds.includes(recipient.id)) {
    fail('NOT_AUTHORIZED', `sender and recipient must both be participants of thread ${th.id}`);
  }
  return th;
}

function requireRound(ctx: Ctx, j: JobRow, roundId: string) {
  nonEmpty(roundId, 'roundId');
  for (const round of ctx.db.negotiationRound.byJobRound.filter([j.id, roundId])) {
    if (round.state !== 'OPEN' || round.turnsUsed >= round.turnBudget) {
      fail('TURN_BUDGET_EXHAUSTED', `round ${roundId} used its turn budget`);
    }
    return round;
  }
  return ctx.db.negotiationRound.insert({
    id: 0n,
    jobId: j.id,
    roundId,
    turnBudget: DEFAULT_ROUND_TURN_BUDGET,
    turnsUsed: 0,
    state: 'OPEN',
  });
}

const MAX_UTTERANCE_LENGTH = 4096;

type MessagePayload = {
  draft_ref?: unknown;
  candidate_refs?: unknown;
  concession?: unknown;
  exceeds_exploration?: unknown;
};

// Permissions an owner must have granted before their agent may do this in
// front of the room, from the typed act, payload flags and their settings.
function requiredGrants(
  type: string,
  payload: MessagePayload,
  prefs: { speakUpForMe: boolean; askMeFirst: boolean }
): string[] {
  const kinds: string[] = [];
  if (type === 'OBJECT' && !prefs.speakUpForMe) kinds.push('OBJECTION_DISCLOSURE');
  if (payload.concession === true && prefs.askMeFirst) kinds.push('CONCESSION_CONSULTATION');
  if (payload.exceeds_exploration === true) kinds.push('BROADER_EXPLORATION');
  return kinds;
}

// A grant counts while the room's inputs and membership are unchanged and it
// concerns this exact action (the draft the message is about).
function findGrant(ctx: Ctx, r: RoomRow, owner: Identity, kind: string, actionRef: string): bigint | undefined {
  for (const g of ctx.db.actionGrant.byOwnerKindRef.filter([owner, kind, actionRef])) {
    if (g.roomId === r.id && g.granted && g.inputRevision === r.inputRevision && g.membershipRevision === r.membershipRevision) {
      return g.id;
    }
  }
  return undefined;
}

// Room-visible representation rules enforced server-side. Semantic checks on
// the wording itself (unsupported prices, concessions, commitments) happen in
// the worker before it reports VALIDATED or TEMPLATE_FALLBACK.
// Returns the disclosure/consultation grant the message relied on, if any.
function checkRoomUtterance(
  ctx: Ctx,
  r: RoomRow,
  sender: AgentRow,
  participantIds: bigint[],
  type: string,
  utterance: string,
  claimRefs: string[],
  payload: MessagePayload,
  sanitizationStatus: string
): bigint | undefined {
  nonEmpty(utterance, 'utterance');
  oneOf(sanitizationStatus, SANITIZATION_STATUSES, 'sanitizationStatus');
  for (const id of participantIds) {
    const owner = ownerOf(ctx, ctx.db.agent.id.find(id)!);
    if (owner && !ctx.db.participationPreferences.owner.find(owner)?.confirmed) {
      fail('PREFERENCES_UNCONFIRMED', 'every participant must confirm participation preferences first');
    }
  }
  let grantUsed: bigint | undefined;
  const owner = ownerOf(ctx, sender);
  if (owner) {
    const prefs = ctx.db.participationPreferences.owner.find(owner)!;
    const actionRef = typeof payload.draft_ref === 'string' ? payload.draft_ref : '';
    for (const kind of requiredGrants(type, payload, prefs)) {
      const grant = findGrant(ctx, r, owner, kind, actionRef);
      if (grant === undefined) fail('CONSULTATION_REQUIRED', `${kind} permission is needed for ${actionRef || 'this action'}`);
      grantUsed ??= grant;
    }
  }
  if (containsPrivateText(ctx, r.id, utterance)) {
    fail('PRIVATE_CONTENT_DETECTED', 'utterance contains a participant\u2019s private reason');
  }
  // Claims cite room facts, or the canonical sentence for this act ("act:<TYPE>").
  for (const ref of claimRefs) {
    if (ref !== `act:${type}` && !refResolves(ctx, r.id, ref)) {
      fail('UTTERANCE_PAYLOAD_MISMATCH', `reference ${ref} is not a known fact in this room`);
    }
  }
  return grantUsed;
}

export const submitAgentMessage = spacetimedb.reducer(
  {
    jobId: t.u64(),
    attempt: t.u32(),
    messageKey: t.string(),
    schemaVersion: t.u32(),
    roundId: t.string(),
    threadId: t.option(t.u64()),
    openThread: t.option(ThreadOpen),
    senderAgentId: t.u64(),
    recipientAgentId: t.u64(),
    type: t.string(),
    replyTo: t.option(t.u64()),
    draftRef: t.option(t.string()),
    proposalId: t.option(t.u64()),
    proposalVersion: t.option(t.u32()),
    payloadJson: t.string(),
    utterance: t.string(),
    claimRefs: t.array(t.string()),
    evidenceRefs: t.array(t.string()),
    audience: t.string(),
    audienceMemberId: t.option(t.u64()),
    sanitizationStatus: t.string(),
    // Optional worker validation digest: sha256 of Python-canonical
    // {type, utterance, payload, claim_refs, evidence_refs}.
    validationDigest: t.option(t.string()),
  },
  (ctx, args) => {
    requireWorker(ctx);
    withReceipt(ctx, 'submit_agent_message', args.messageKey, args, () => {
      if (args.schemaVersion !== MESSAGE_SCHEMA_VERSION) {
        fail('UNSUPPORTED_SCHEMA', `schema_version ${args.schemaVersion}; expected ${MESSAGE_SCHEMA_VERSION}`);
      }
      const j = requireLease(ctx, args.jobId, args.attempt);
      const r = requireRoom(ctx, j.roomId);
      oneOf(args.type, MESSAGE_TYPES, 'type');
      oneOf(args.audience, AUDIENCES, 'audience');
      if (args.utterance.length > MAX_UTTERANCE_LENGTH) fail('PAYLOAD_TOO_LARGE', 'utterance is too long');
      const payload = parseJson(args.payloadJson, 'payloadJson') as MessagePayload;
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        fail('INVALID_ARGUMENT', 'payloadJson must be an object');
      }
      if (args.validationDigest !== undefined) {
        const content = {
          type: args.type,
          utterance: args.utterance,
          payload,
          claim_refs: args.claimRefs,
          evidence_refs: args.evidenceRefs,
        };
        if (digest(pythonJson(content)) !== args.validationDigest) {
          fail('INVALID_VALIDATION_METADATA', 'validation digest does not match the submitted content');
        }
      }

      const sender = requireAgentInRoom(ctx, r, args.senderAgentId, 'sender');
      const recipient = requireAgentInRoom(ctx, r, args.recipientAgentId, 'recipient');
      if (sender.id === recipient.id) fail('NOT_AUTHORIZED', 'sender and recipient must differ');
      if (args.proposalId !== undefined) {
        const p = ctx.db.proposal.id.find(args.proposalId);
        if (!p || p.roomId !== r.id) fail('NOT_FOUND', `proposal ${args.proposalId}`);
      }
      const candidateRefs = Array.isArray(payload.candidate_refs) ? payload.candidate_refs.map(String) : [];
      for (const ref of [...args.evidenceRefs, ...candidateRefs]) {
        if (!refResolves(ctx, r.id, ref)) fail('INVALID_EVIDENCE_REF', `evidence ${ref} is not recorded in this room`);
      }

      const round = requireRound(ctx, j, args.roundId);
      const thread = resolveThread(ctx, j, r, args.roundId, args.threadId, args.openThread, sender, recipient);
      // A reply answers a message the sender received in this same thread.
      if (args.replyTo !== undefined) {
        const parent = ctx.db.agentMessage.id.find(args.replyTo);
        if (!parent || parent.threadId !== thread.id || parent.recipientAgentId !== sender.id) {
          fail('INVALID_REPLY', `message ${args.replyTo} is not a message to the sender in this thread`);
        }
      }

      // The effective audience is derived here, never taken from the model.
      let audienceOwner: Identity | undefined;
      let grantUsed: bigint | undefined;
      if (args.audience === 'ROOM') {
        grantUsed = checkRoomUtterance(
          ctx,
          r,
          sender,
          thread.participantAgentIds,
          args.type,
          args.utterance,
          args.claimRefs,
          payload,
          args.sanitizationStatus
        );
      } else if (args.audience === 'OWNER_PRIVATE') {
        nonEmpty(args.utterance, 'utterance');
        oneOf(args.sanitizationStatus, SANITIZATION_STATUSES, 'sanitizationStatus');
        const senderOwner = ownerOf(ctx, sender);
        const target =
          args.audienceMemberId === undefined ? undefined : requireMembershipById(ctx, r.id, args.audienceMemberId).member;
        if (senderOwner) {
          if (target && !target.isEqual(senderOwner)) {
            fail('NOT_AUTHORIZED', 'a personal agent may only speak privately to its own owner');
          }
          audienceOwner = senderOwner;
        } else {
          if (!target) fail('INVALID_ARGUMENT', 'mediator private messages need audienceMemberId');
          audienceOwner = target;
        }
      }

      const threadTurns = thread.turnsUsed + 1;
      ctx.db.conversationThread.id.update({
        ...thread,
        turnsUsed: threadTurns,
        state: threadTurns >= thread.turnBudget ? 'CLOSED' : 'OPEN',
        closureReason: threadTurns >= thread.turnBudget ? 'TURN_BUDGET' : undefined,
      });
      const roundTurns = round.turnsUsed + 1;
      const roundExhausted = roundTurns >= round.turnBudget;
      ctx.db.negotiationRound.id.update({ ...round, turnsUsed: roundTurns, state: roundExhausted ? 'CLOSED' : 'OPEN' });
      if (roundExhausted) {
        for (const th of [...ctx.db.conversationThread.byJob.filter(j.id)]) {
          if (th.roundId === args.roundId && th.state === 'OPEN') {
            ctx.db.conversationThread.id.update({ ...th, state: 'CLOSED', closureReason: 'ROUND_BUDGET' });
          }
        }
      }

      const m = ctx.db.agentMessage.insert({
        id: 0n,
        messageKey: args.messageKey,
        schemaVersion: args.schemaVersion,
        roomId: r.id,
        jobId: j.id,
        attempt: args.attempt,
        roundId: args.roundId,
        threadId: thread.id,
        senderAgentId: sender.id,
        recipientAgentId: recipient.id,
        type: args.type,
        replyTo: args.replyTo,
        draftRef: args.draftRef,
        proposalId: args.proposalId,
        proposalVersion: args.proposalVersion,
        inputRevision: j.inputRevision,
        membershipRevision: j.membershipRevision,
        permissionRevision: j.permissionRevision,
        payloadJson: args.payloadJson,
        utterance: args.utterance,
        claimRefs: args.claimRefs,
        evidenceRefs: args.evidenceRefs,
        effectiveAudience: args.audience,
        audienceOwner,
        sanitizationStatus: args.sanitizationStatus,
        grantRef: grantUsed,
        processingState: 'DELIVERED',
        processedReplyIds: [],
        createdAt: ctx.timestamp,
      });
      if (r.status === 'COLLECTING') ctx.db.room.id.update({ ...r, status: 'NEGOTIATING' });
      return { message_id: Number(m.id), thread_id: Number(thread.id) };
    });
  }
);

export const recordMessageProcessed = spacetimedb.reducer(
  { jobId: t.u64(), attempt: t.u32(), messageId: t.u64(), recipientAgentId: t.u64(), replyIds: t.array(t.u64()) },
  (ctx, args) => {
    const j = requireLease(ctx, args.jobId, args.attempt);
    const m = ctx.db.agentMessage.id.find(args.messageId);
    if (!m || m.roomId !== j.roomId) fail('NOT_FOUND', `message ${args.messageId}`);
    if (m.recipientAgentId !== args.recipientAgentId) fail('NOT_AUTHORIZED', 'only the recipient can process a message');
    for (const id of args.replyIds) {
      const reply = ctx.db.agentMessage.id.find(id);
      if (!reply || reply.roomId !== j.roomId) fail('NOT_FOUND', `reply ${id}`);
    }
    if (m.processingState === 'PROCESSED') {
      const same =
        m.processedReplyIds.length === args.replyIds.length && m.processedReplyIds.every((id, i) => id === args.replyIds[i]);
      if (!same) fail('KEY_BODY_MISMATCH', `message ${m.id} was already processed with different replies`);
      return;
    }
    ctx.db.agentMessage.id.update({ ...m, processingState: 'PROCESSED', processedReplyIds: args.replyIds });
  }
);

export const replyToOwner = spacetimedb.reducer(
  { jobId: t.u64(), attempt: t.u32(), requestKey: t.string(), memberId: t.u64(), body: t.string() },
  (ctx, args) => {
    requireWorker(ctx);
    withReceipt(ctx, 'reply_to_owner', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      const m = requireMembershipById(ctx, j.roomId, args.memberId);
      ctx.db.privateMessage.insert({
        id: 0n,
        roomId: j.roomId,
        owner: m.member,
        sender: 'AGENT',
        body: nonEmpty(args.body, 'body'),
        jobId: j.id,
        createdAt: ctx.timestamp,
      });
      return {};
    });
  }
);

export const proposeConstraintPatch = spacetimedb.reducer(
  {
    jobId: t.u64(),
    attempt: t.u32(),
    requestKey: t.string(),
    memberId: t.u64(),
    patchJson: t.string(),
    sourceMessageId: t.option(t.u64()),
  },
  (ctx, args) => {
    requireWorker(ctx);
    withReceipt(ctx, 'propose_constraint_patch', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      const m = requireMembershipById(ctx, j.roomId, args.memberId);
      constraintFromPatch(args.patchJson);
      // A patch that quotes the owner must quote them exactly, from their own message.
      const quote = (JSON.parse(args.patchJson) as { source_quote?: unknown }).source_quote;
      if (quote !== undefined) {
        const source = args.sourceMessageId === undefined ? undefined : ctx.db.privateMessage.id.find(args.sourceMessageId);
        if (
          typeof quote !== 'string' ||
          quote.trim().length === 0 ||
          !source ||
          source.roomId !== j.roomId ||
          !source.owner.isEqual(m.member) ||
          source.sender !== 'HUMAN' ||
          !source.body.includes(quote)
        ) {
          fail('INVALID_EVIDENCE_REF', 'source_quote must be the owner\'s exact words from the cited message');
        }
      }
      ctx.db.constraintPatch.insert({
        id: 0n,
        roomId: j.roomId,
        owner: m.member,
        jobId: j.id,
        patchJson: args.patchJson,
        sourceMessageId: args.sourceMessageId,
        state: 'PENDING',
        createdAt: ctx.timestamp,
      });
      return {};
    });
  }
);

// The onboarding agent may suggest settings; they stay unconfirmed and grant
// nothing until the owner confirms them.
export const proposeParticipationPreferences = spacetimedb.reducer(
  {
    jobId: t.u64(),
    attempt: t.u32(),
    requestKey: t.string(),
    memberId: t.u64(),
    speakUpForMe: t.bool(),
    askMeFirst: t.bool(),
    giveMeTime: t.bool(),
  },
  (ctx, args) => {
    requireWorker(ctx);
    withReceipt(ctx, 'propose_participation_preferences', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      const m = requireMembershipById(ctx, j.roomId, args.memberId);
      const prefs = ctx.db.participationPreferences.owner.find(m.member);
      if (!prefs) fail('NOT_FOUND', 'preferences');
      if (prefs.confirmed) fail('INVALID_STATE', 'preferences are already confirmed; ask the owner instead');
      ctx.db.participationPreferences.owner.update({
        ...prefs,
        speakUpForMe: args.speakUpForMe,
        askMeFirst: args.askMeFirst,
        giveMeTime: args.giveMeTime,
        proposedBy: 'AGENT',
      });
      return {};
    });
  }
);

export const emitEvent = spacetimedb.reducer(
  {
    jobId: t.u64(),
    attempt: t.u32(),
    requestKey: t.string(),
    type: t.string(),
    memberId: t.option(t.u64()),
    payloadJson: t.string(),
  },
  (ctx, args) => {
    requireWorker(ctx);
    withReceipt(ctx, 'emit_event', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      nonEmpty(args.type, 'type');
      const payload = parseJson(args.payloadJson, 'payloadJson');
      const owner = args.memberId === undefined ? undefined : requireMembershipById(ctx, j.roomId, args.memberId).member;
      if (!owner && containsPrivateText(ctx, j.roomId, args.payloadJson)) {
        fail('PRIVATE_CONTENT_DETECTED', 'room event contains a participant’s private reason');
      }
      insertEvent(ctx, j.roomId, args.type, payload, owner);
      return {};
    });
  }
);

export const recordCandidates = spacetimedb.reducer(
  { jobId: t.u64(), attempt: t.u32(), requestKey: t.string(), candidates: t.array(CandidateInput) },
  (ctx, args) => {
    requireWorker(ctx);
    withReceipt(ctx, 'record_candidates', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      for (const c of args.candidates) {
        nonEmpty(c.candidateRef, 'candidateRef');
        oneOf(c.category, CANDIDATE_CATEGORIES, 'category');
        oneOf(c.mode, CANDIDATE_MODES, 'mode');
        parseJson(c.factsJson, 'factsJson');
        const fields = {
          ...c,
          roomId: j.roomId,
          inputRevision: j.inputRevision,
          membershipRevision: j.membershipRevision,
          recordedAt: ctx.timestamp,
        };
        let existing;
        for (const row of ctx.db.candidate.byRoomRef.filter([j.roomId, c.candidateRef])) existing = row;
        if (existing) ctx.db.candidate.id.update({ ...existing, ...fields });
        else ctx.db.candidate.insert({ id: 0n, ...fields });
      }
      return {};
    });
  }
);
