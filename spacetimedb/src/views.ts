import { t, type InferSchema, type ViewCtx } from 'spacetimedb/server';
import type { Identity, Timestamp } from 'spacetimedb';
import spacetimedb, {
  agent,
  candidate,
  constraintPatch,
  conversationThread,
  decisionEvent,
  job,
  milestone,
  participantConstraint,
  participationPreferences,
  privateMessage,
  profile,
  question,
  room,
} from './schema';

// Every view is computed per caller (ctx.sender) and reads only through
// indexes. Shared views never include payload JSON, private constraints,
// validation details or other members' identities.

type ViewDb = ViewCtx<InferSchema<typeof spacetimedb>>['db'];

function myRoomIds(db: ViewDb, sender: Identity): bigint[] {
  return [...db.membership.byMember.filter(sender)].filter(m => m.active).map(m => m.roomId);
}

const MemberRow = t.row('MemberRow', {
  id: t.u64().primaryKey(),
  roomId: t.u64(),
  displayName: t.string(),
  role: t.string(),
  active: t.bool(),
  isMe: t.bool(),
  agentId: t.option(t.u64()),
});

const ChatRow = t.row('ChatRow', {
  id: t.u64().primaryKey(),
  roomId: t.u64(),
  threadId: t.u64(),
  roundId: t.string(),
  senderAgentId: t.u64(),
  recipientAgentId: t.u64(),
  type: t.string(),
  utterance: t.string(),
  replyTo: t.option(t.u64()),
  draftRef: t.option(t.string()),
  proposalId: t.option(t.u64()),
  claimRefs: t.array(t.string()),
  evidenceRefs: t.array(t.string()),
  sanitizationStatus: t.string(),
  createdAt: t.timestamp(),
});

const ProposalRow = t.row('ProposalRow', {
  id: t.u64().primaryKey(),
  roomId: t.u64(),
  stage: t.string(),
  version: t.u32(),
  termsJson: t.string(),
  termsDigest: t.string(),
  sharedSummaryJson: t.string(),
  evidenceRefs: t.array(t.string()),
  state: t.string(),
  reviewerCount: t.u32(),
  isReviewer: t.bool(),
  createdAt: t.timestamp(),
});

const ConsentRow = t.row('ConsentRow', {
  id: t.u64().primaryKey(),
  proposalId: t.u64(),
  roomId: t.u64(),
  response: t.string(),
  termsDigest: t.string(),
  note: t.option(t.string()),
  decideLaterAvailable: t.bool(),
  updatedAt: t.timestamp(),
});

const ConsentCountRow = t.row('ConsentCountRow', {
  proposalId: t.u64().primaryKey(),
  roomId: t.u64(),
  required: t.u32(),
  accepted: t.u32(),
  declined: t.u32(),
  pending: t.u32(),
});

const ImpactRow = t.row('ImpactRow', {
  id: t.u64().primaryKey(),
  proposalId: t.u64(),
  roomId: t.u64(),
  impactJson: t.string(),
});

const PresenceRow = t.row('PresenceRow', {
  membershipId: t.u64().primaryKey(),
  roomId: t.u64(),
  online: t.bool(),
  lastSeen: t.option(t.timestamp()),
});

const ActivityRow = t.row('ActivityRow', {
  agentId: t.u64().primaryKey(),
  roomId: t.u64(),
  activity: t.string(),
  threadId: t.option(t.u64()),
  updatedAt: t.timestamp(),
});

export const myRooms = spacetimedb.view({ name: 'my_rooms', public: true }, t.array(room.rowType), ctx =>
  myRoomIds(ctx.db, ctx.sender).flatMap(id => {
    const r = ctx.db.room.id.find(id);
    return r ? [r] : [];
  })
);

export const roomMembers = spacetimedb.view({ name: 'room_members', public: true }, t.array(MemberRow), ctx =>
  myRoomIds(ctx.db, ctx.sender).flatMap(roomId => {
    const agents = [...ctx.db.agent.byRoom.filter(roomId)];
    return [...ctx.db.membership.byRoomMember.filter(roomId)].map(m => ({
      id: m.id,
      roomId: m.roomId,
      displayName: m.displayName,
      role: m.role,
      active: m.active,
      isMe: m.member.isEqual(ctx.sender),
      agentId: agents.find(a => a.membershipId === m.id)?.id,
    }));
  })
);

export const roomAgents = spacetimedb.view({ name: 'room_agents', public: true }, t.array(agent.rowType), ctx =>
  myRoomIds(ctx.db, ctx.sender).flatMap(roomId => [...ctx.db.agent.byRoom.filter(roomId)])
);

export const myProfile = spacetimedb.view({ name: 'my_profile', public: true }, t.option(profile.rowType), ctx =>
  ctx.db.profile.owner.find(ctx.sender) ?? undefined
);

export const myParticipationPreferences = spacetimedb.view(
  { name: 'my_participation_preferences', public: true },
  t.option(participationPreferences.rowType),
  ctx => ctx.db.participationPreferences.owner.find(ctx.sender) ?? undefined
);

export const myConstraints = spacetimedb.view(
  { name: 'my_constraints', public: true },
  t.array(participantConstraint.rowType),
  ctx => [...ctx.db.participantConstraint.byOwner.filter(ctx.sender)]
);

export const myConstraintPatches = spacetimedb.view(
  { name: 'my_constraint_patches', public: true },
  t.array(constraintPatch.rowType),
  ctx => [...ctx.db.constraintPatch.byOwner.filter(ctx.sender)]
);

export const myPrivateMessages = spacetimedb.view(
  { name: 'my_private_messages', public: true },
  t.array(privateMessage.rowType),
  ctx => [...ctx.db.privateMessage.byOwner.filter(ctx.sender)]
);

export const myQuestions = spacetimedb.view({ name: 'my_questions', public: true }, t.array(question.rowType), ctx => [
  ...ctx.db.question.byOwner.filter(ctx.sender),
]);

function toChat(m: {
  id: bigint;
  roomId: bigint;
  threadId: bigint;
  roundId: string;
  senderAgentId: bigint;
  recipientAgentId: bigint;
  type: string;
  utterance: string;
  replyTo: bigint | undefined;
  draftRef: string | undefined;
  proposalId: bigint | undefined;
  claimRefs: string[];
  evidenceRefs: string[];
  sanitizationStatus: string;
  createdAt: Timestamp;
}) {
  return {
    id: m.id,
    roomId: m.roomId,
    threadId: m.threadId,
    roundId: m.roundId,
    senderAgentId: m.senderAgentId,
    recipientAgentId: m.recipientAgentId,
    type: m.type,
    utterance: m.utterance,
    replyTo: m.replyTo,
    draftRef: m.draftRef,
    proposalId: m.proposalId,
    claimRefs: m.claimRefs,
    evidenceRefs: m.evidenceRefs,
    sanitizationStatus: m.sanitizationStatus,
    createdAt: m.createdAt,
  };
}

// The live negotiation chat: committed room-audience utterances only.
export const roomMessages = spacetimedb.view({ name: 'room_messages', public: true }, t.array(ChatRow), ctx =>
  myRoomIds(ctx.db, ctx.sender).flatMap(roomId =>
    [...ctx.db.agentMessage.byRoom.filter(roomId)].filter(m => m.effectiveAudience === 'ROOM').map(toChat)
  )
);

// My Agent: private utterances addressed to me.
export const myAgentMessages = spacetimedb.view({ name: 'my_agent_messages', public: true }, t.array(ChatRow), ctx =>
  myRoomIds(ctx.db, ctx.sender).flatMap(roomId =>
    [...ctx.db.agentMessage.byRoom.filter(roomId)]
      .filter(m => m.effectiveAudience === 'OWNER_PRIVATE' && m.audienceOwner?.isEqual(ctx.sender))
      .map(toChat)
  )
);

export const roomThreads = spacetimedb.view(
  { name: 'room_threads', public: true },
  t.array(conversationThread.rowType),
  ctx => myRoomIds(ctx.db, ctx.sender).flatMap(roomId => [...ctx.db.conversationThread.byRoom.filter(roomId)])
);

export const roomEvents = spacetimedb.view({ name: 'room_events', public: true }, t.array(decisionEvent.rowType), ctx =>
  myRoomIds(ctx.db, ctx.sender).flatMap(roomId =>
    [...ctx.db.decisionEvent.byRoom.filter(roomId)].filter(
      e => e.audience === 'ROOM' || (e.owner !== undefined && e.owner.isEqual(ctx.sender))
    )
  )
);

export const roomProposals = spacetimedb.view({ name: 'room_proposals', public: true }, t.array(ProposalRow), ctx =>
  myRoomIds(ctx.db, ctx.sender).flatMap(roomId =>
    [...ctx.db.proposal.byRoomStage.filter(roomId)].map(p => ({
      id: p.id,
      roomId: p.roomId,
      stage: p.stage,
      version: p.version,
      termsJson: p.termsJson,
      termsDigest: p.termsDigest,
      sharedSummaryJson: p.sharedSummaryJson,
      evidenceRefs: p.evidenceRefs,
      state: p.state,
      reviewerCount: p.affectedMembers.length,
      isReviewer: p.affectedMembers.some(m => m.isEqual(ctx.sender)),
      createdAt: p.createdAt,
    }))
  )
);

export const myProposalImpacts = spacetimedb.view({ name: 'my_proposal_impacts', public: true }, t.array(ImpactRow), ctx =>
  [...ctx.db.participantImpact.byOwner.filter(ctx.sender)].map(i => ({
    id: i.id,
    proposalId: i.proposalId,
    roomId: i.roomId,
    impactJson: i.impactJson,
  }))
);

// Deciding later is always allowed; pending never turns into consent.
export const myConsents = spacetimedb.view({ name: 'my_consents', public: true }, t.array(ConsentRow), ctx => {
  return [...ctx.db.consent.byMember.filter(ctx.sender)].map(c => ({
    id: c.id,
    proposalId: c.proposalId,
    roomId: c.roomId,
    response: c.response,
    termsDigest: c.termsDigest,
    note: c.note,
    decideLaterAvailable: true,
    updatedAt: c.updatedAt,
  }));
});

// Neutral progress: counts only, never who is still pending.
export const roomConsentCounts = spacetimedb.view(
  { name: 'room_consent_counts', public: true },
  t.array(ConsentCountRow),
  ctx =>
    myRoomIds(ctx.db, ctx.sender).flatMap(roomId =>
      [...ctx.db.proposal.byRoomStage.filter(roomId)]
        .filter(p => p.state === 'AWAITING_REVIEW' || p.state === 'AGREED')
        .map(p => {
          const consents = [...ctx.db.consent.byProposalMember.filter(p.id)];
          const accepted = consents.filter(c => c.response === 'ACCEPTED').length;
          const declined = consents.filter(c => c.response === 'DECLINED').length;
          return {
            proposalId: p.id,
            roomId: p.roomId,
            required: p.affectedMembers.length,
            accepted,
            declined,
            pending: p.affectedMembers.length - accepted - declined,
          };
        })
    )
);

export const roomCandidates = spacetimedb.view({ name: 'room_candidates', public: true }, t.array(candidate.rowType), ctx =>
  myRoomIds(ctx.db, ctx.sender).flatMap(roomId => [...ctx.db.candidate.byRoomRef.filter(roomId)])
);

export const roomMilestones = spacetimedb.view({ name: 'room_milestones', public: true }, t.array(milestone.rowType), ctx =>
  myRoomIds(ctx.db, ctx.sender).flatMap(roomId => [...ctx.db.milestone.byRoomStage.filter(roomId)])
);

// Who in my rooms has the app open right now (green dots).
export const roomPresence = spacetimedb.view({ name: 'room_presence', public: true }, t.array(PresenceRow), ctx =>
  myRoomIds(ctx.db, ctx.sender).flatMap(roomId =>
    [...ctx.db.membership.byRoomMember.filter(roomId)]
      .filter(m => m.active)
      .map(m => {
        const p = ctx.db.presence.identity.find(m.member);
        return { membershipId: m.id, roomId, online: p?.online ?? false, lastSeen: p?.lastSeen };
      })
  )
);

// What each agent in my rooms is doing. Anything from a job that is no longer
// running shows as IDLE, so a crashed worker never leaves agents "thinking".
export const roomAgentActivity = spacetimedb.view(
  { name: 'room_agent_activity', public: true },
  t.array(ActivityRow),
  ctx =>
    myRoomIds(ctx.db, ctx.sender).flatMap(roomId =>
      [...ctx.db.agentStatus.roomId.filter(roomId)].map(a => {
        const live = ctx.db.job.id.find(a.jobId)?.status === 'CLAIMED';
        return {
          agentId: a.agentId,
          roomId: a.roomId,
          activity: live ? a.activity : 'IDLE',
          threadId: live ? a.threadId : undefined,
          updatedAt: a.updatedAt,
        };
      })
    )
);

// Only allowlisted worker/dispatcher identities see anything here.
export const workerPendingJobs = spacetimedb.view({ name: 'worker_pending_jobs', public: true }, t.array(job.rowType), ctx =>
  ctx.db.worker.identity.find(ctx.sender) ? [...ctx.db.job.byStatus.filter('PENDING')] : []
);
