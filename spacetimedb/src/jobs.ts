import { t } from 'spacetimedb/server';
import { ScheduleAt, Timestamp } from 'spacetimedb';
import spacetimedb, { leaseTimer } from './schema';
import {
  type Ctx,
  type JobRow,
  MAX_LEASE_SECONDS,
  MIN_LEASE_SECONDS,
  fail,
  nonEmpty,
  num,
  requireLease,
  requireRoom,
  closeJobThreads,
  oneOf,
  requireWorker,
  runProcedure,
  withReceipt,
} from './guards';
import { resumeJobIfReady } from './effects';

function micros(ts: Timestamp | undefined): string | null {
  return ts ? ts.microsSinceUnixEpoch.toString() : null;
}

function leaseFields(j: JobRow) {
  return {
    job_id: num(j.id),
    generation: num(j.id),
    room_id: num(j.roomId),
    kind: j.kind,
    attempt: j.attempt,
    input_revision: num(j.inputRevision),
    membership_revision: num(j.membershipRevision),
    permission_revision: num(j.permissionRevision),
    lease_expires_at_micros: micros(j.leaseExpiresAt),
    graph_thread_ref: j.graphThreadRef ?? null,
  };
}

function boundedLease(seconds: number): bigint {
  if (seconds < MIN_LEASE_SECONDS || seconds > MAX_LEASE_SECONDS) {
    fail('INVALID_ARGUMENT', `leaseSeconds must be between ${MIN_LEASE_SECONDS} and ${MAX_LEASE_SECONDS}`);
  }
  return BigInt(seconds) * 1_000_000n;
}

function grantLease(ctx: Ctx, j: JobRow, leaseSeconds: number, attempt: number): JobRow {
  const expires = new Timestamp(ctx.timestamp.microsSinceUnixEpoch + boundedLease(leaseSeconds));
  const updated = ctx.db.job.id.update({
    ...j,
    status: 'CLAIMED',
    attempt,
    leaseOwner: ctx.sender,
    leaseExpiresAt: expires,
    updatedAt: ctx.timestamp,
  });
  ctx.db.leaseTimer.insert({
    scheduledId: 0n,
    scheduledAt: ScheduleAt.time(expires.microsSinceUnixEpoch),
    jobId: j.id,
    attempt,
  });
  return updated;
}

function isClaimable(ctx: Ctx, j: JobRow): boolean {
  if (j.status === 'PENDING') return true;
  // A lapsed lease is claimable even if its expiry timer hasn't fired yet.
  return (
    j.status === 'CLAIMED' &&
    j.leaseExpiresAt !== undefined &&
    j.leaseExpiresAt.microsSinceUnixEpoch <= ctx.timestamp.microsSinceUnixEpoch
  );
}

export const claimJob = spacetimedb.procedure(
  { jobId: t.option(t.u64()), leaseSeconds: t.u32(), requestKey: t.string() },
  t.string(),
  (ctx, args) =>
    runProcedure(() =>
      ctx.withTx(tx => {
        requireWorker(tx);
        return withReceipt(tx, 'claim_job', args.requestKey, args, () => {
          let j: JobRow | undefined;
          if (args.jobId !== undefined) {
            j = tx.db.job.id.find(args.jobId) ?? undefined;
            if (!j) fail('NOT_FOUND', `job ${args.jobId}`);
            if (!isClaimable(tx, j)) fail('JOB_NOT_ELIGIBLE', `job ${args.jobId} is ${j.status}`);
          } else {
            for (const candidate of tx.db.job.byStatus.filter('PENDING')) {
              if (!j || candidate.id < j.id) j = candidate;
            }
            if (!j) fail('JOB_NOT_ELIGIBLE', 'no pending jobs');
          }
          return leaseFields(grantLease(tx, j, args.leaseSeconds, j.attempt + 1));
        }) as object;
      })
    )
);

export const renewLease = spacetimedb.reducer(
  { jobId: t.u64(), attempt: t.u32(), leaseSeconds: t.u32() },
  (ctx, { jobId, attempt, leaseSeconds }) => {
    const j = requireLease(ctx, jobId, attempt);
    grantLease(ctx, j, leaseSeconds, attempt);
  }
);

export const expireLease = spacetimedb.reducer(
  { onSchedule: leaseTimer },
  { arg: leaseTimer.rowType },
  (ctx, { arg }) => {
    const j = ctx.db.job.id.find(arg.jobId);
    if (!j || j.status !== 'CLAIMED' || j.attempt !== arg.attempt) return;
    if (!j.leaseExpiresAt || j.leaseExpiresAt.microsSinceUnixEpoch > ctx.timestamp.microsSinceUnixEpoch) return;
    ctx.db.job.id.update({
      ...j,
      status: 'PENDING',
      leaseOwner: undefined,
      leaseExpiresAt: undefined,
      updatedAt: ctx.timestamp,
    });
  }
);

function parsed(text: string | undefined): unknown {
  if (text === undefined) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

// One consistent snapshot for the worker. It includes private constraint data
// because the worker is the trusted processor; it must scope that data per
// agent (architecture §5, §11) before any model call.
function buildContext(ctx: Ctx, j: JobRow) {
  const r = requireRoom(ctx, j.roomId);
  const byId = <T extends { id: bigint }>(rows: T[]) => rows.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const members = byId([...ctx.db.membership.byRoomMember.filter(r.id)]);
  const agents = [...ctx.db.agent.byRoom.filter(r.id)];
  const memberIdOf = new Map(members.map(m => [m.member.toHexString(), num(m.id)]));

  const people = members.map(m => {
    const prefs = ctx.db.participationPreferences.owner.find(m.member);
    const profile = ctx.db.profile.owner.find(m.member);
    const agentRow = agents.find(a => a.kind === 'PERSONAL' && a.membershipId === m.id);
    const confirmed = prefs?.confirmed ?? false;
    const speakUp = confirmed && (prefs?.speakUpForMe ?? false);
    return {
      member_id: num(m.id),
      agent_id: agentRow ? num(agentRow.id) : null,
      display_name: m.displayName,
      role: m.role,
      active: m.active,
      profile: profile
        ? {
            self_description: profile.selfDescription,
            priority_ranking: profile.priorityRanking,
            revision: num(profile.revision),
          }
        : null,
      participation_preferences: {
        speak_up_for_me: prefs?.speakUpForMe ?? false,
        ask_me_first: prefs?.askMeFirst ?? true,
        give_me_time: prefs?.giveMeTime ?? true,
        reminders_opted_in: prefs?.remindersOptedIn ?? false,
        confirmed,
        revision: prefs ? num(prefs.revision) : 0,
      },
      tool_policy: {
        may_speak_in_room: confirmed,
        may_object_in_room_without_grant: speakUp,
        consult_before_visible_concession: !confirmed || (prefs?.askMeFirst ?? true),
        reminders_allowed: confirmed && (prefs?.remindersOptedIn ?? false) && !(prefs?.giveMeTime ?? true),
      },
    };
  });

  const constraints = byId([...ctx.db.participantConstraint.byRoomOwnerKey.filter(r.id)]).map(c => ({
    constraint_id: num(c.id),
    member_id: memberIdOf.get(c.owner.toHexString()) ?? null,
    key: c.key,
    value: parsed(c.valueJson),
    unit: c.unit,
    hardness: c.hardness,
    authorized_range: parsed(c.authorizedRangeJson),
    private_rationale: c.privateRationale ?? null,
    revision: num(c.revision),
  }));

  const questions = byId([...ctx.db.question.byJob.filter(j.id)]).map(q => ({
    question_id: num(q.id),
    request_key: q.requestKey,
    member_id: memberIdOf.get(q.owner.toHexString()) ?? null,
    kind: q.kind,
    version: q.version,
    action: parsed(q.actionJson),
    state: q.state,
    answer: parsed(q.answerJson),
    thread_id: q.threadId !== undefined ? num(q.threadId) : null,
  }));

  // Grants stay valid until inputs or membership change.
  const grants = [...ctx.db.actionGrant.byRoom.filter(r.id)]
    .filter(g => g.inputRevision === r.inputRevision && g.membershipRevision === r.membershipRevision)
    .map(g => ({
      grant_id: num(g.id),
      question_id: num(g.questionId),
      member_id: memberIdOf.get(g.owner.toHexString()) ?? null,
      kind: g.kind,
      action_ref: g.actionRef,
      action_digest: g.actionDigest,
      granted: g.granted,
    }));

  const threads = byId([...ctx.db.conversationThread.byJob.filter(j.id)]).map(th => ({
    thread_id: num(th.id),
    round_id: th.roundId,
    topic_ref: th.topicRef,
    participant_agent_ids: th.participantAgentIds.map(num),
    turn_budget: th.turnBudget,
    turns_used: th.turnsUsed,
    state: th.state,
    closure_reason: th.closureReason ?? null,
  }));

  const messages = byId([...ctx.db.agentMessage.byJob.filter(j.id)]).map(m => ({
    message_id: num(m.id),
    message_key: m.messageKey,
    thread_id: num(m.threadId),
    round_id: m.roundId,
    sender_agent_id: num(m.senderAgentId),
    recipient_agent_id: num(m.recipientAgentId),
    type: m.type,
    reply_to: m.replyTo !== undefined ? num(m.replyTo) : null,
    draft_ref: m.draftRef ?? null,
    payload: parsed(m.payloadJson),
    utterance: m.utterance,
    effective_audience: m.effectiveAudience,
    processing_state: m.processingState,
    processed_reply_ids: m.processedReplyIds.map(num),
  }));

  const candidates = [...ctx.db.candidate.byRoomRef.filter(r.id)].map(c => ({
    candidate_ref: c.candidateRef,
    candidate_set_version: c.candidateSetVersion,
    category: c.category,
    mode: c.mode,
    source_url: c.sourceUrl,
    observed_at: c.observedAt,
    facts: parsed(c.factsJson),
    missing_fields: c.missingFields,
    input_revision: num(c.inputRevision),
    membership_revision: num(c.membershipRevision),
  }));

  const proposals = [...ctx.db.proposal.byRoomStage.filter(r.id)]
    .filter(p => p.state === 'AWAITING_REVIEW' || p.state === 'AGREED')
    .map(p => ({
      proposal_id: num(p.id),
      stage: p.stage,
      version: p.version,
      state: p.state,
      terms_digest: p.termsDigest,
      terms: parsed(p.termsJson),
    }));

  return {
    job: leaseFields(j),
    room: { room_id: num(r.id), goal: r.goal, status: r.status, settings: parsed(r.settingsJson) },
    mediator_agent_id: num(agents.find(a => a.kind === 'MEDIATOR')!.id),
    members: people,
    constraints,
    questions,
    grants,
    threads,
    messages,
    candidates,
    proposals,
  };
}

export const loadExecutionContext = spacetimedb.procedure(
  { jobId: t.u64(), attempt: t.u32() },
  t.string(),
  (ctx, { jobId, attempt }) => runProcedure(() => ctx.withTx(tx => buildContext(tx, requireLease(tx, jobId, attempt))))
);

export const parkJob = spacetimedb.reducer(
  {
    jobId: t.u64(),
    attempt: t.u32(),
    requestKey: t.string(),
    graphThreadRef: t.string(),
    questionIds: t.array(t.u64()),
  },
  (ctx, args) => {
    withReceipt(ctx, 'park_job', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      nonEmpty(args.graphThreadRef, 'graphThreadRef');
      if (args.questionIds.length === 0) fail('INVALID_ARGUMENT', 'park_job needs at least one question');
      for (const qid of args.questionIds) {
        const q = ctx.db.question.id.find(qid);
        if (!q || q.jobId !== j.id) fail('NOT_FOUND', `question ${qid} for job ${j.id}`);
        if (q.state === 'SUPERSEDED') fail('QUESTION_SUPERSEDED', `question ${qid}`);
      }
      closeJobThreads(ctx, j.id, 'NEEDS_OWNER_INPUT');
      ctx.db.job.id.update({
        ...j,
        status: 'WAITING_FOR_INPUT',
        leaseOwner: undefined,
        leaseExpiresAt: undefined,
        graphThreadRef: args.graphThreadRef,
        waitingQuestionIds: args.questionIds,
        updatedAt: ctx.timestamp,
      });
      // Handles the early-answer race: answers that arrived before parking.
      resumeJobIfReady(ctx, j.id);
      return {};
    });
  }
);

export const finishJob = spacetimedb.reducer(
  { jobId: t.u64(), attempt: t.u32(), requestKey: t.string(), outcome: t.string(), outcomeJson: t.string() },
  (ctx, args) => {
    withReceipt(ctx, 'finish_job', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      nonEmpty(args.outcome, 'outcome');
      closeJobThreads(ctx, j.id, args.outcome);
      ctx.db.job.id.update({
        ...j,
        status: 'FINISHED',
        outcome: args.outcome,
        outcomeJson: args.outcomeJson,
        leaseOwner: undefined,
        leaseExpiresAt: undefined,
        updatedAt: ctx.timestamp,
      });
      return {};
    });
  }
);

export const failJob = spacetimedb.reducer(
  {
    jobId: t.u64(),
    attempt: t.u32(),
    requestKey: t.string(),
    retryable: t.bool(),
    code: t.string(),
    detail: t.string(),
  },
  (ctx, args) => {
    withReceipt(ctx, 'fail_job', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      closeJobThreads(ctx, j.id, args.code);
      ctx.db.job.id.update({
        ...j,
        status: args.retryable ? 'PENDING' : 'FAILED',
        outcome: args.code,
        outcomeJson: JSON.stringify({ code: args.code, detail: args.detail }),
        leaseOwner: undefined,
        leaseExpiresAt: undefined,
        updatedAt: ctx.timestamp,
      });
      return {};
    });
  }
);

// Side-effect-free fence check: the worker calls this before external work and
// right before delivery (lease, attempt and snapshot revisions).
export const checkLease = spacetimedb.procedure({ jobId: t.u64(), attempt: t.u32() }, t.string(), (ctx, args) =>
  runProcedure(() => ctx.withTx(tx => leaseFields(requireLease(tx, args.jobId, args.attempt))))
);

export const closeThreads = spacetimedb.reducer(
  { jobId: t.u64(), attempt: t.u32(), reason: t.string() },
  (ctx, { jobId, attempt, reason }) => {
    const j = requireLease(ctx, jobId, attempt);
    closeJobThreads(ctx, j.id, nonEmpty(reason, 'reason'));
  }
);

const OPERATION_KINDS = ['model', 'research', 'local'] as const;

// Reserve an external operation before running it. Returns the stored result if
// it already completed (a replay after a crash), otherwise reserves budget and
// returns RESERVED. An unfinished earlier reservation is ambiguous (the call may
// have run), so it reserves again and counts against the budget again.
export const beginOperation = spacetimedb.procedure(
  { jobId: t.u64(), attempt: t.u32(), opKey: t.string(), requestDigest: t.string(), kind: t.string(), limit: t.u32() },
  t.string(),
  (ctx, args) =>
    runProcedure(() =>
      ctx.withTx(tx => {
        const j = requireLease(tx, args.jobId, args.attempt);
        oneOf(args.kind, OPERATION_KINDS, 'kind');
        const key = `${j.id}:${nonEmpty(args.opKey, 'opKey')}`;
        const existing = tx.db.operationRecord.key.find(key);
        if (existing && existing.requestDigest !== args.requestDigest) {
          fail('KEY_BODY_MISMATCH', `operation ${args.opKey} was started with a different request`);
        }
        if (existing?.status === 'DONE') return { status: 'DONE', result_json: existing.resultJson ?? null };
        if (args.kind !== 'local') {
          const budget = tx.db.jobBudget.jobId.find(j.id) ?? tx.db.jobBudget.insert({ jobId: j.id, modelCalls: 0, researchCalls: 0 });
          const used = args.kind === 'model' ? budget.modelCalls : budget.researchCalls;
          if (used >= args.limit) fail('CALL_BUDGET_EXHAUSTED', `${args.kind} budget of ${args.limit} is used up`);
          tx.db.jobBudget.jobId.update(
            args.kind === 'model' ? { ...budget, modelCalls: used + 1 } : { ...budget, researchCalls: used + 1 }
          );
        }
        const record = { kind: args.kind, requestDigest: args.requestDigest, status: 'RUNNING', resultJson: undefined, updatedAt: tx.timestamp };
        if (existing) tx.db.operationRecord.key.update({ ...existing, ...record });
        else tx.db.operationRecord.insert({ key, jobId: j.id, ...record });
        return { status: 'RESERVED' };
      })
    )
);

export const completeOperation = spacetimedb.reducer(
  { jobId: t.u64(), attempt: t.u32(), opKey: t.string(), resultJson: t.string() },
  (ctx, args) => {
    const j = requireLease(ctx, args.jobId, args.attempt);
    const op = ctx.db.operationRecord.key.find(`${j.id}:${args.opKey}`);
    if (!op) fail('NOT_FOUND', `operation ${args.opKey}`);
    if (op.status === 'DONE') {
      if (op.resultJson !== args.resultJson) fail('KEY_BODY_MISMATCH', `operation ${args.opKey} already has a different result`);
      return;
    }
    ctx.db.operationRecord.key.update({ ...op, status: 'DONE', resultJson: args.resultJson, updatedAt: ctx.timestamp });
  }
);
