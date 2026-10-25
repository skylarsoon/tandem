import { t } from 'spacetimedb/server';
import spacetimedb from './schema';
import {
  GRANT_KINDS,
  QUESTION_KINDS,
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
import { applyParticipationPreferences, constraintFromPatch, resumeJobIfReady, upsertConstraint } from './effects';

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
      parseJson(args.actionJson, 'actionJson');
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
//   and resumes the waiting job. Neither is ever consent to a proposal.
export const answerQuestion = spacetimedb.reducer(
  { questionId: t.u64(), version: t.u32(), answerJson: t.string() },
  (ctx, { questionId, version, answerJson }) => {
    requireHuman(ctx);
    const q = ctx.db.question.id.find(questionId);
    if (!q || !q.owner.isEqual(ctx.sender)) fail('NOT_FOUND', `question ${questionId}`);
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

    if (GRANT_KINDS.includes(q.kind)) {
      if (typeof answer.granted !== 'boolean') fail('INVALID_ARGUMENT', 'answer needs boolean granted');
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
