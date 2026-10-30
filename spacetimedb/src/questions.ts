import { participantIdentity } from './access';
import { t } from 'spacetimedb/server';
import spacetimedb from './schema';
import {
  GRANT_KINDS,
  QUESTION_KINDS,
  RECOVERY_CHOICES,
  canonical,
  digest,
  fail,
  invalidateRoom,
  nonEmpty,
  oneOf,
  parseJson,
  requireHuman,
  requireLease,
  requireMembershipById,
  requireRoom,
  withReceipt,
} from './guards';
import {
  applyParticipationPreferences,
  applyPatch,
  constraintFromPatch,
  resumeJobIfReady,
  upsertConstraint,
} from './effects';

type Choice = { id?: unknown; label?: unknown; kind?: unknown; patches?: unknown };

// The ways forward a PLAN_RECOVERY question offers: each a distinct id, its words, a known
// kind, and for a change the patches it confirms (checked like any patch when picked).
function recoveryChoices(action: Record<string, unknown>): Choice[] {
  const choices = action.choices;
  if (!Array.isArray(choices) || choices.length === 0) fail('INVALID_ARGUMENT', 'a recovery question needs choices');
  const ids = new Set<string>();
  for (const c of choices as Choice[]) {
    if (!c || typeof c.id !== 'string' || !c.id || ids.has(c.id)) fail('INVALID_ARGUMENT', 'each choice needs its own id');
    if (typeof c.label !== 'string' || !c.label.trim()) fail('INVALID_ARGUMENT', `choice ${c.id} needs words`);
    oneOf(c.kind as string, RECOVERY_CHOICES, 'choice kind');
    if (c.kind === 'change' && (!Array.isArray(c.patches) || c.patches.length === 0)) {
      fail('INVALID_ARGUMENT', `choice ${c.id} changes nothing`);
    }
    ids.add(c.id);
  }
  return choices as Choice[];
}

// Worker asks one owner a private, versioned question about an exact proposed
// action. The worker never answers it; only the owner can.
export const askOwner = spacetimedb.reducer(
  {
    jobId: t.u64(),
    attempt: t.u32(),
    requestKey: t.string(),
    memberId: t.u64(),
    kind: t.string(),
    actionJson: t.string(),
    prompt: t.string(),
    answerSchemaJson: t.string(),
    threadId: t.option(t.u64()),
  },
  (ctx, args) => {
    withReceipt(ctx, 'ask_owner', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      const r = requireRoom(ctx, j.roomId);
      const m = requireMembershipById(ctx, r.id, args.memberId);
      oneOf(args.kind, QUESTION_KINDS, 'kind');
      const action = parseJson(args.actionJson, 'actionJson') as Record<string, unknown> | null;
      if (args.kind === 'PLAN_RECOVERY') recoveryChoices(action ?? {});
      parseJson(args.answerSchemaJson, 'answerSchemaJson');
      nonEmpty(args.prompt, 'prompt');
      if (args.threadId !== undefined) {
        const th = ctx.db.conversationThread.id.find(args.threadId);
        if (!th || th.jobId !== j.id) fail('NOT_FOUND', `thread ${args.threadId} for job ${j.id}`);
        if (th.state === 'OPEN') {
          ctx.db.conversationThread.id.update({ ...th, state: 'CLOSED', closureReason: 'WAITING_FOR_OWNER' });
        }
      }
      ctx.db.question.insert({
        id: 0n,
        requestKey: args.requestKey,
        roomId: r.id,
        owner: m.member,
        jobId: j.id,
        version: 1,
        kind: args.kind,
        actionJson: args.actionJson,
        inputRevision: r.inputRevision,
        permissionRevision: r.permissionRevision,
        prompt: args.prompt,
        answerSchemaJson: args.answerSchemaJson,
        threadId: args.threadId,
        state: 'OPEN',
        answerJson: undefined,
        answeredAt: undefined,
        createdAt: ctx.timestamp,
      });
      return {};
    });
  }
);

// The owner answers. Effects by kind:
// - PARTICIPATION_CONFIRMATION → confirms preferences (new generation), or with
//   {"confirmed": false} records a decline and resumes the waiting job
// - MISSING_FACT with {"constraint": {...}} → confirms a constraint (new generation)
// - MISSING_FACT otherwise → resumes the waiting job
// - OBJECTION_DISCLOSURE / CONCESSION_CONSULTATION / BROADER_EXPLORATION with
//   {"granted": bool} → records a grant for this owner, kind and action_ref
//   (the draft it concerns). A granted permission changes what the agent may do,
//   so it bumps the permission revision and starts a new generation; the grant
//   stays usable while inputs and membership are unchanged. A refusal is stored
//   (with their own "reason", if they gave one) and resumes the waiting job.
//   Neither is ever consent to a proposal.
// - PLAN_RECOVERY with {"choice": id} → one of the offered ways forward. A change is
//   confirmed like any other (kept as a confirmed patch) and the room replans;
//   anything else resumes the waiting job.
export const answerQuestion = spacetimedb.reducer(
  { questionId: t.u64(), version: t.u32(), answerJson: t.string() },
  (ctx, { questionId, version, answerJson }) => {
    requireHuman(ctx);
    const q = ctx.db.question.id.find(questionId);
    if (!q || !q.owner.isEqual(participantIdentity(ctx.db, ctx.sender))) fail('NOT_FOUND', `question ${questionId}`);
    if (q.state !== 'OPEN' || q.version !== version) {
      fail('QUESTION_SUPERSEDED', `question ${questionId} v${version} is no longer open`);
    }
    const answer = parseJson(answerJson, 'answerJson') as Record<string, unknown> | null;
    if (!answer || typeof answer !== 'object') fail('INVALID_ARGUMENT', 'answerJson must be an object');

    ctx.db.question.id.update({ ...q, state: 'ANSWERED', answerJson, answeredAt: ctx.timestamp });

    if (q.kind === 'PARTICIPATION_CONFIRMATION' && answer.confirmed === false) {
      // Declining leaves settings unconfirmed; the waiting job resumes and the
      // worker treats it as the owner declining to take part for now.
      resumeJobIfReady(ctx, q.jobId);
      return;
    }
    if (q.kind === 'PARTICIPATION_CONFIRMATION') {
      const flags = ['speak_up_for_me', 'ask_me_first', 'give_me_time'].map(k => answer[k]);
      if (flags.some(f => typeof f !== 'boolean')) {
        fail('INVALID_ARGUMENT', 'answer needs boolean speak_up_for_me, ask_me_first, give_me_time');
      }
      const optIn = answer.reminders_opted_in === true;
      applyParticipationPreferences(ctx, flags[0] as boolean, flags[1] as boolean, flags[2] as boolean, optIn);
      return;
    }

    if (q.kind === 'MISSING_FACT' && answer.constraint !== undefined) {
      const nudge = (JSON.parse(q.actionJson) as { nudge?: { yes?: unknown; no?: unknown } }).nudge;
      if (nudge) {
        // A just-in-time question the waiting job asked: the answer must be one of the
        // two it offered. It's recorded now and the job resumes and re-plans with it,
        // so several people's answers don't cancel each other's questions.
        const picked = canonical(answer.constraint);
        if (picked !== canonical(nudge.yes) && picked !== canonical(nudge.no)) {
          fail('INVALID_ARGUMENT', 'answer must be one of the offered choices');
        }
        upsertConstraint(ctx, q.roomId, constraintFromPatch(JSON.stringify(answer.constraint)), false);
        resumeJobIfReady(ctx, q.jobId);
        return;
      }
      upsertConstraint(ctx, q.roomId, constraintFromPatch(JSON.stringify(answer.constraint)));
      return;
    }

    if (q.kind === 'PLAN_RECOVERY') {
      const picked = recoveryChoices(JSON.parse(q.actionJson)).find(c => c.id === answer.choice);
      if (typeof answer.choice !== 'string' || !picked) fail('INVALID_ARGUMENT', 'answer must be one of the offered choices');
      if (picked.kind === 'change') {
        // What they picked is their confirmed change: kept with the others, then one replan.
        for (const patch of picked.patches as unknown[]) {
          const patchJson = JSON.stringify(patch);
          ctx.db.constraintPatch.insert({
            id: 0n,
            roomId: q.roomId,
            owner: q.owner,
            jobId: q.jobId,
            patchJson,
            sourceMessageId: undefined,
            state: 'CONFIRMED',
            createdAt: ctx.timestamp,
          });
          applyPatch(ctx, q.roomId, patchJson, false);
        }
        invalidateRoom(ctx, q.roomId, { input: true }, 'owner picked a way forward');
        return;
      }
      resumeJobIfReady(ctx, q.jobId);
      return;
    }

    if (GRANT_KINDS.includes(q.kind)) {
      if (typeof answer.granted !== 'boolean') fail('INVALID_ARGUMENT', 'answer needs boolean granted');
      if (answer.reason !== undefined && (typeof answer.reason !== 'string' || answer.reason.length > 500)) {
        fail('INVALID_ARGUMENT', 'reason must be text of at most 500 characters');
      }
      const r = requireRoom(ctx, q.roomId);
      const action = JSON.parse(q.actionJson) as Record<string, unknown>;
      ctx.db.actionGrant.insert({
        id: 0n,
        questionId: q.id,
        roomId: q.roomId,
        owner: q.owner,
        kind: q.kind,
        actionRef: typeof action.action_ref === 'string' ? action.action_ref : '',
        actionDigest: digest(canonical(action)),
        inputRevision: r.inputRevision,
        membershipRevision: r.membershipRevision,
        granted: answer.granted,
        createdAt: ctx.timestamp,
      });
      if (answer.granted) {
        invalidateRoom(ctx, q.roomId, { permission: true }, 'owner granted a permission');
        return;
      }
    }

    resumeJobIfReady(ctx, q.jobId);
  }
);
