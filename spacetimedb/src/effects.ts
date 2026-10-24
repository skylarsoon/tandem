import { ScheduleAt } from 'spacetimedb';
import type { Identity } from 'spacetimedb';
import {
  type Ctx,
  HARDNESS,
  createJob,
  emitEvent,
  fail,
  invalidateRoom,
  nonEmpty,
  oneOf,
  parseJson,
  requireRoom,
  setMilestone,
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
  };
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
  setMilestone(ctx, p.roomId, p.stage, next === 'AGREED' ? 'AGREED' : 'IN_REVIEW', p.id);
  emitEvent(ctx, p.roomId, next === 'AGREED' ? 'PROPOSAL_AGREED' : 'PROPOSAL_REOPENED', {
    proposal_id: Number(p.id),
    version: p.version,
    stage: p.stage,
  });
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
