import { ScheduleAt } from 'spacetimedb';
import type { Identity } from 'spacetimedb';
import { addOwnedRule, brokenCommitments, mirrorLegacy } from './room_rules';
import {
  type Ctx,
  HARDNESS,
  OPEN_JOB_STATUSES,
  createJob,
  emitEvent,
  fail,
  invalidateRoom,
  nonEmpty,
  oneOf,
  parseJson,
  refreshMilestone,
  requireRoom,
} from './guards';

// Shared state transitions used by several reducers. Not exported from the
// module entry, so nothing here is callable by clients directly.

// Confirming (or later editing) participation preferences changes what the
// owner's agent may do, so every room they belong to starts a new generation
// under a bumped permission revision.
export function applyParticipationPreferences(
  ctx: Ctx,
  speakUpForMe: boolean,
  askMeFirst: boolean,
  giveMeTime: boolean,
  remindersOptedIn: boolean
): void {
  const prefs = ctx.db.participationPreferences.owner.find(ctx.sender);
  if (!prefs) fail('NOT_FOUND', 'create or join a room first');
  ctx.db.participationPreferences.owner.update({
    ...prefs,
    speakUpForMe,
    askMeFirst,
    giveMeTime,
    remindersOptedIn,
    confirmed: true,
    confirmedAt: ctx.timestamp,
    proposedBy: 'OWNER',
    revision: prefs.revision + 1n,
  });
  for (const m of [...ctx.db.membership.byMember.filter(ctx.sender)]) {
    if (!m.active) continue;
    invalidateRoom(ctx, m.roomId, { permission: true }, 'participation preferences changed');
    emitEvent(ctx, m.roomId, 'PARTICIPATION_PREFERENCES_CONFIRMED', {}, ctx.sender);
  }
}

export type ConstraintInput = {
  key: string;
  valueJson: string;
  unit: string;
  hardness: string;
  authorizedRangeJson?: string;
  privateRationale?: string;
  sourceQuote?: string;
};

// Accepts the patch shape the worker proposes:
// {"key", "value", "unit", "hardness", "authorized_range"?, "private_rationale"?}
export function constraintFromPatch(patchJson: string): ConstraintInput {
  const patch = parseJson(patchJson, 'patchJson') as Record<string, unknown> | null;
  if (!patch || typeof patch !== 'object') fail('INVALID_ARGUMENT', 'patchJson must be an object');
  const key = typeof patch.key === 'string' ? patch.key : fail('INVALID_ARGUMENT', 'patch.key is required');
  if (!('value' in patch)) fail('INVALID_ARGUMENT', 'patch.value is required');
  return {
    key,
    valueJson: JSON.stringify(patch.value),
    unit: typeof patch.unit === 'string' ? patch.unit : '',
    hardness: typeof patch.hardness === 'string' ? patch.hardness : fail('INVALID_ARGUMENT', 'patch.hardness is required'),
    authorizedRangeJson: patch.authorized_range === undefined ? undefined : JSON.stringify(patch.authorized_range),
    privateRationale: typeof patch.private_rationale === 'string' ? patch.private_rationale : undefined,
    sourceQuote: typeof patch.source_quote === 'string' ? patch.source_quote : undefined,
  };
}

// A confirmed patch: a generic rule ({"rule": {...}}) or an old named limit.
export function applyPatch(ctx: Ctx, roomId: bigint, patchJson: string, replan = true): void {
  const patch = parseJson(patchJson, 'patchJson') as { rule?: unknown; source_quote?: unknown } | null;
  if (patch && typeof patch === 'object' && patch.rule !== undefined) {
    addOwnedRule(ctx, roomId, patch.rule, typeof patch.source_quote === 'string' ? patch.source_quote : '');
    reopenBroken(ctx, roomId);
    if (replan) invalidateRoom(ctx, roomId, { input: true }, 'rule confirmed');
    emitEvent(ctx, roomId, 'REQUIREMENTS_UPDATED', { message: 'A participant updated their requirements' });
    return;
  }
  upsertConstraint(ctx, roomId, constraintFromPatch(patchJson), replan);
}

// The caller is the owner. A confirmed constraint change bumps the input
// revision and starts a new generation.
export function upsertConstraint(ctx: Ctx, roomId: bigint, input: ConstraintInput, replan = true): void {
  nonEmpty(input.key, 'key');
  oneOf(input.hardness, HARDNESS, 'hardness');
  parseJson(input.valueJson, 'valueJson');
  if (input.authorizedRangeJson !== undefined) parseJson(input.authorizedRangeJson, 'authorizedRangeJson');

  let existing;
  for (const c of ctx.db.participantConstraint.byRoomOwnerKey.filter([roomId, ctx.sender, input.key])) existing = c;
  const fields = {
    valueJson: input.valueJson,
    unit: input.unit,
    hardness: input.hardness,
    authorizedRangeJson: input.authorizedRangeJson,
    privateRationale: input.privateRationale,
    confirmedAt: ctx.timestamp,
  };
  if (existing) {
    ctx.db.participantConstraint.id.update({ ...existing, ...fields, revision: existing.revision + 1n });
  } else {
    ctx.db.participantConstraint.insert({ id: 0n, roomId, owner: ctx.sender, key: input.key, ...fields, revision: 1n });
  }
  // The same limit in the generic rule language, which is what planning reads.
  mirrorLegacy(ctx, roomId, ctx.sender, input.key, input);
  reopenBroken(ctx, roomId);
  // A batch of confirmations (an onboarding summary) replans once, at the end.
  if (replan) invalidateRoom(ctx, roomId, { input: true }, 'constraint confirmed');
  emitEvent(ctx, roomId, 'REQUIREMENTS_UPDATED', { message: 'A participant updated their requirements' });
  emitEvent(ctx, roomId, 'MY_CONSTRAINT_SAVED', { key: input.key }, ctx.sender);
}

// A waiting job becomes claimable again once every question it waits on is answered.
export function resumeJobIfReady(ctx: Ctx, jobId: bigint): void {
  const j = ctx.db.job.id.find(jobId);
  if (!j || j.status !== 'WAITING_FOR_INPUT') return;
  for (const qid of j.waitingQuestionIds) {
    const q = ctx.db.question.id.find(qid);
    if (!q || q.state !== 'ANSWERED') return;
  }
  ctx.db.job.id.update({ ...j, status: 'PENDING', updatedAt: ctx.timestamp });
}

// The owner's message goes to their own agent and queues an INTERPRET job.
export function messageOwnAgent(ctx: Ctx, roomId: bigint, body: string): void {
  const r = requireRoom(ctx, roomId);
  const j = createJob(ctx, r, 'INTERPRET', 'private message');
  ctx.db.privateMessage.insert({
    id: 0n,
    roomId,
    owner: ctx.sender,
    sender: 'HUMAN',
    body,
    jobId: j.id,
    createdAt: ctx.timestamp,
  });
}

// Agreement is recomputed from consent rows in the same transaction as every
// response: all affected members must have ACCEPTED these exact terms.
export function recomputeAgreement(ctx: Ctx, proposalId: bigint): void {
  const p = ctx.db.proposal.id.find(proposalId);
  if (!p || (p.state !== 'AWAITING_REVIEW' && p.state !== 'AGREED')) return;
  let allAccepted = p.affectedMembers.length > 0;
  for (const member of p.affectedMembers) {
    let accepted = false;
    for (const c of ctx.db.consent.byProposalMember.filter([proposalId, member])) {
      accepted = c.response === 'ACCEPTED' && c.termsDigest === p.termsDigest;
    }
    if (!accepted) allAccepted = false;
  }
  const next = allAccepted ? 'AGREED' : 'AWAITING_REVIEW';
  if (next === p.state) return;
  ctx.db.proposal.id.update({ ...p, state: next });
  settleCompromise(ctx, p, next === 'AGREED');
  refreshMilestone(ctx, p.roomId, p.stage);
  const decision = decisionOf(ctx, p);
  emitEvent(ctx, p.roomId, next === 'AGREED' ? 'PROPOSAL_AGREED' : 'PROPOSAL_REOPENED', {
    proposal_id: Number(p.id),
    version: p.version,
    stage: p.stage,
    decision_id: decision.decisionId,
  });
  // Sequential planning: once one part of the trip is agreed, plan the next
  // one on top of it. A complete plan or general agreement needs no follow-up.
  if (next === 'AGREED' && PARTIAL_SCOPES.includes(decision.scope)) {
    const open = [...ctx.db.job.byRoom.filter(p.roomId)].some(j => j.kind === 'PLAN' && OPEN_JOB_STATUSES.includes(j.status));
    if (!open) createJob(ctx, requireRoom(ctx, p.roomId), 'PLAN', `decision ${decision.decisionId} agreed`);
  }
}

const PARTIAL_SCOPES = ['FLIGHT_BUNDLE', 'SINGLE_SHARED_BOOKING', 'ACTIVITY_DECISION'];

type ProposalRow = NonNullable<ReturnType<Ctx['db']['proposal']['id']['find']>>;

// The decision a proposal belongs to. Proposals from before decisions existed
// count as one decision per stage.
export function decisionOf(ctx: Ctx, p: ProposalRow): { decisionId: string; scope: string; selectedRefs: string[] } {
  const d = ctx.db.proposalDecision.proposalId.find(p.id);
  return d ?? { decisionId: p.stage, scope: 'LEGACY', selectedRefs: [] };
}

// A newer version of the same decision replaces this one: the old terms can no
// longer be approved, and its compromise stops counting.
export function supersedeProposal(ctx: Ctx, p: ProposalRow): void {
  ctx.db.proposal.id.update({ ...p, state: 'SUPERSEDED' });
  for (const c of [...ctx.db.consent.byProposalMember.filter(p.id)]) {
    ctx.db.consent.id.update({ ...c, response: 'STALE', updatedAt: ctx.timestamp });
  }
  for (const e of [...ctx.db.compromiseEntry.byProposal.filter(p.id)]) {
    if (e.state === 'ACTIVE') ctx.db.compromiseEntry.id.update({ ...e, state: 'SUPERSEDED', updatedAt: ctx.timestamp });
  }
}

// An agreed decision a confirmed change breaks (a pick against someone's rules or the
// trip's dates, or a whole-trip limit with everything agreed) is reopened, never kept
// silently: it stops counting, and the next plan decides it again. The room hears only that.
export function reopenBroken(ctx: Ctx, roomId: bigint): void {
  for (const c of brokenCommitments(ctx, roomId, commitments(ctx, roomId))) {
    supersedeProposal(ctx, c.proposal);
    refreshMilestone(ctx, roomId, c.proposal.stage);
    emitEvent(ctx, roomId, 'DECISION_REOPENED', { decision_id: c.decisionId, message: 'A change means an agreed decision is planned again' });
  }
}

// Agreed decisions are the room's commitments: what later decisions build on
// and are checked against.
export function commitments(ctx: Ctx, roomId: bigint) {
  return [...ctx.db.proposal.byRoomStage.filter(roomId)]
    .filter(p => p.state === 'AGREED')
    .map(p => ({ proposal: p, ...decisionOf(ctx, p) }));
}

// The compromise ledger follows agreement, never suggestion. On AGREED, the
// burdens frozen at publish become ACTIVE entries, one per member, so a
// repeated transition can't count twice. When agreement is lost (a withdrawn
// approval) they are REVOKED, and come back if everyone accepts again.
function settleCompromise(ctx: Ctx, p: ProposalRow, agreed: boolean): void {
  const entries = [...ctx.db.compromiseEntry.byProposal.filter(p.id)];
  if (!agreed) {
    for (const e of entries) {
      if (e.state === 'ACTIVE') ctx.db.compromiseEntry.id.update({ ...e, state: 'REVOKED', updatedAt: ctx.timestamp });
    }
    return;
  }
  const { decisionId } = decisionOf(ctx, p);
  for (const b of ctx.db.proposalBurden.byProposal.filter(p.id)) {
    const existing = entries.find(e => e.memberId === b.memberId);
    if (existing) {
      ctx.db.compromiseEntry.id.update({ ...existing, state: 'ACTIVE', updatedAt: ctx.timestamp });
      continue;
    }
    const m = ctx.db.membership.id.find(b.memberId);
    if (!m) continue;
    ctx.db.compromiseEntry.insert({
      id: 0n,
      roomId: p.roomId,
      memberId: b.memberId,
      owner: m.member,
      decisionId,
      proposalId: p.id,
      proposalVersion: p.version,
      termsDigest: p.termsDigest,
      stage: p.stage,
      burdenMicros: b.burdenMicros,
      state: 'ACTIVE',
      createdAt: ctx.timestamp,
      updatedAt: ctx.timestamp,
    });
  }
}

// H_i per member: the sum of their ACTIVE entries in this room.
export function compromiseHistory(ctx: Ctx, roomId: bigint): Map<bigint, number> {
  const micros = new Map<bigint, number>();
  for (const m of ctx.db.membership.byRoomMember.filter(roomId)) {
    for (const e of ctx.db.compromiseEntry.byOwner.filter(m.member)) {
      if (e.roomId === roomId && e.state === 'ACTIVE') micros.set(m.id, (micros.get(m.id) ?? 0) + e.burdenMicros);
    }
  }
  return micros;
}

// Reminders are opt-in: only reviewers who confirmed preferences, explicitly
// opted in, and did not ask for time get one private nudge if still pending.
export function scheduleReviewReminders(ctx: Ctx, proposalId: bigint, reviewers: Identity[]): void {
  const delay = BigInt(ctx.db.config.id.find(0)?.reminderDelaySeconds ?? 600) * 1_000_000n;
  for (const member of reviewers) {
    const prefs = ctx.db.participationPreferences.owner.find(member);
    if (!prefs || !prefs.confirmed || !prefs.remindersOptedIn || prefs.giveMeTime) continue;
    ctx.db.reviewReminder.insert({
      scheduledId: 0n,
      scheduledAt: ScheduleAt.time(ctx.timestamp.microsSinceUnixEpoch + delay),
      proposalId,
      member,
    });
  }
}
