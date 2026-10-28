import { hasAppAccess } from './access';
import { t } from 'spacetimedb/server';
import type { Identity } from 'spacetimedb';
import spacetimedb, { profileCalibration } from './schema';
import {
  type Ctx,
  OPEN_JOB_STATUSES,
  fail,
  parseJson,
  requireHuman,
  requireLease,
  requireWorker,
  runProcedure,
  withReceipt,
} from './guards';
import { CATALOG_VERSION, profileQuestion, validateAnswer } from './profile_rules';

// Persistent profile onboarding: the person's own answers (private), and the
// agent's "Does this sound like you?" summary they confirm or correct. The
// profile is a set of defaults: each room still confirms what applies to it.

const SUMMARY_MAX = 1200;
const CORRECTION_MAX = 1000;

function ensureProfile(ctx: Ctx) {
  const existing = ctx.db.profile.owner.find(ctx.sender);
  if (existing) return existing;
  return ctx.db.profile.insert({
    owner: ctx.sender,
    displayName: '',
    avatarRef: '',
    selfDescription: '',
    priorityRanking: [],
    revision: 0n,
    updatedAt: ctx.timestamp,
  });
}

function findAnswer(ctx: Ctx, owner: Identity, questionId: string) {
  for (const a of ctx.db.profileAnswer.byOwner.filter(owner)) if (a.questionId === questionId) return a;
  return undefined;
}

// The fields earlier parts of the system read (room views, the worker's context)
// stay in step with the matching answers.
function syncProfile(ctx: Ctx, questionId: string, value: unknown) {
  const p = ensureProfile(ctx);
  const v = value as Record<string, unknown> | string | null;
  const fields: Partial<typeof p> = {};
  if (questionId === 'C01') fields.displayName = typeof v === 'string' ? v.trim() : '';
  if (questionId === 'A01') fields.avatarRef = v && typeof v === 'object' && typeof v.avatar === 'string' ? v.avatar : '';
  if (questionId === 'C03') fields.priorityRanking = v && typeof v === 'object' ? (v.ranked as string[]) : [];
  if (questionId === 'F03') fields.selfDescription = typeof v === 'string' ? v.trim() : '';
  ctx.db.profile.owner.update({ ...p, ...fields, revision: p.revision + 1n, updatedAt: ctx.timestamp });
  // Keep an already-requested summary aligned with edits made while it waits.
  const calibration = ctx.db.profileCalibration.owner.find(ctx.sender);
  if (calibration?.state === 'PENDING') queueSummary(ctx, undefined);
}

function store(ctx: Ctx, questionId: string, status: string, value: unknown) {
  const q = profileQuestion(questionId);
  const row = { owner: ctx.sender, questionId, key: q.key, status, valueJson: JSON.stringify(value), catalogVersion: CATALOG_VERSION, updatedAt: ctx.timestamp };
  const existing = findAnswer(ctx, ctx.sender, questionId);
  if (existing) ctx.db.profileAnswer.id.update({ ...existing, ...row });
  else ctx.db.profileAnswer.insert({ id: 0n, ...row });
  syncProfile(ctx, questionId, value);
}

// Answer (or change) one question. The value is checked against the catalog.
export const saveProfileAnswer = spacetimedb.reducer({ questionId: t.string(), valueJson: t.string() }, (ctx, args) => {
  requireHuman(ctx);
  const q = profileQuestion(args.questionId);
  const value = parseJson(args.valueJson, 'valueJson');
  validateAnswer(q, value);
  store(ctx, args.questionId, 'ANSWERED', value);
});

// "Skip for now": known to be unanswered, never read as flexible or unrestricted.
export const skipProfileQuestion = spacetimedb.reducer({ questionId: t.string() }, (ctx, { questionId }) => {
  requireHuman(ctx);
  if (profileQuestion(questionId).required) fail('INVALID_STATE', 'this question is needed to finish');
  store(ctx, questionId, 'SKIPPED', null);
});

// Back to "not shared yet".
export const clearProfileAnswer = spacetimedb.reducer({ questionId: t.string() }, (ctx, { questionId }) => {
  requireHuman(ctx);
  if (profileQuestion(questionId).required) fail('INVALID_STATE', 'this question is needed to finish');
  const existing = findAnswer(ctx, ctx.sender, questionId);
  if (!existing) return;
  ctx.db.profileAnswer.id.delete(existing.id);
  syncProfile(ctx, questionId, null);
});

function queueSummary(ctx: Ctx, correction: string | undefined) {
  const p = ctx.db.profile.owner.find(ctx.sender);
  const name = findAnswer(ctx, ctx.sender, 'C01');
  if (!p || !name || name.status !== 'ANSWERED') fail('INVALID_STATE', 'tell us what to call you first');
  const previous = ctx.db.profileCalibration.owner.find(ctx.sender);
  // Only the newest summary request runs; an older pending one is cancelled.
  if (previous?.jobId !== undefined) {
    const old = ctx.db.job.id.find(previous.jobId);
    if (old && OPEN_JOB_STATUSES.includes(old.status)) {
      ctx.db.job.id.update({ ...old, status: 'CANCELLED', leaseOwner: undefined, leaseExpiresAt: undefined, updatedAt: ctx.timestamp });
    }
  }
  const job = ctx.db.job.insert({
    id: 0n,
    roomId: 0n,
    kind: 'PROFILE',
    status: 'PENDING',
    reason: correction === undefined ? 'profile summary' : 'profile correction',
    inputRevision: p.revision,
    membershipRevision: 0n,
    permissionRevision: 0n,
    attempt: 0,
    leaseOwner: undefined,
    leaseExpiresAt: undefined,
    graphThreadRef: undefined,
    waitingQuestionIds: [],
    outcome: undefined,
    outcomeJson: undefined,
    createdAt: ctx.timestamp,
    updatedAt: ctx.timestamp,
  });
  ctx.db.profileJob.insert({ jobId: job.id, owner: ctx.sender });
  const row = {
    owner: ctx.sender,
    state: 'PENDING',
    summary: previous?.summary,
    correction: correction ?? previous?.correction,
    profileRevision: p.revision,
    jobId: job.id,
    updatedAt: ctx.timestamp,
  };
  if (previous) ctx.db.profileCalibration.owner.update(row);
  else ctx.db.profileCalibration.insert(row);
}

// Ask the agent for its "Does this sound like you?" summary of the answers so far.
export const requestProfileSummary = spacetimedb.reducer({}, ctx => {
  requireHuman(ctx);
  queueSummary(ctx, undefined);
});

// "Yes, that's me" for the exact summary shown (the profile must not have changed since).
export const confirmProfileSummary = spacetimedb.reducer({ profileRevision: t.u64() }, (ctx, { profileRevision }) => {
  requireHuman(ctx);
  const c = ctx.db.profileCalibration.owner.find(ctx.sender);
  if (!c || c.state !== 'PROPOSED') fail('INVALID_STATE', 'there is no summary waiting for you');
  const p = ctx.db.profile.owner.find(ctx.sender)!;
  if (c.profileRevision !== profileRevision || p.revision !== profileRevision) {
    fail('STALE_SNAPSHOT', 'your answers changed since this summary; ask for a fresh one');
  }
  ctx.db.profileCalibration.owner.update({ ...c, state: 'CONFIRMED', updatedAt: ctx.timestamp });
});

// "Not quite": the person's own words go to their agent for a revised summary.
// Context only: nothing here becomes a requirement or a permission.
export const correctProfileSummary = spacetimedb.reducer({ correction: t.string() }, (ctx, { correction }) => {
  requireHuman(ctx);
  const text = correction.trim();
  if (text.length === 0 || text.length > CORRECTION_MAX) fail('INVALID_ARGUMENT', `a correction is 1-${CORRECTION_MAX} characters`);
  const c = ctx.db.profileCalibration.owner.find(ctx.sender);
  if (!c || (c.state !== 'PROPOSED' && c.state !== 'CONFIRMED')) fail('INVALID_STATE', 'there is no summary to correct');
  queueSummary(ctx, text);
});

function profileJobOwner(ctx: Ctx, jobId: bigint): Identity {
  const j = ctx.db.job.id.find(jobId);
  if (!j || j.kind !== 'PROFILE') fail('INVALID_STATE', 'not a profile job');
  return (ctx.db.profileJob.jobId.find(jobId) ?? fail('NOT_FOUND', 'profile job owner')).owner;
}

// The one person's answers for their profile job: the worker never sees anyone else's.
export const loadProfileContext = spacetimedb.procedure({ jobId: t.u64(), attempt: t.u32() }, t.string(), (ctx, args) =>
  runProcedure(() =>
    ctx.withTx(tx => {
      const j = requireLease(tx, args.jobId, args.attempt);
      const owner = profileJobOwner(tx, j.id);
      const p = tx.db.profile.owner.find(owner);
      const c = tx.db.profileCalibration.owner.find(owner);
      return {
        job: { job_id: Number(j.id), kind: j.kind, attempt: j.attempt, profile_revision: Number(j.inputRevision) },
        catalog_version: CATALOG_VERSION,
        profile_revision: p ? Number(p.revision) : 0,
        answers: [...tx.db.profileAnswer.byOwner.filter(owner)]
          .sort((a, b) => (a.questionId < b.questionId ? -1 : 1))
          .map(a => ({ question_id: a.questionId, key: a.key, status: a.status, value: JSON.parse(a.valueJson) })),
        calibration: c ? { state: c.state, summary: c.summary ?? null, correction: c.correction ?? null } : null,
      } as object;
    })
  )
);

// The agent's summary, for the person to confirm. Refused if their answers
// changed after the job started: a newer request supersedes it.
export const proposeProfileSummary = spacetimedb.reducer(
  { jobId: t.u64(), attempt: t.u32(), requestKey: t.string(), summary: t.string() },
  (ctx, args) => {
    requireWorker(ctx);
    withReceipt(ctx, 'propose_profile_summary', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      const owner = profileJobOwner(ctx, j.id);
      const text = args.summary.trim();
      if (text.length === 0 || text.length > SUMMARY_MAX) fail('INVALID_ARGUMENT', `a summary is 1-${SUMMARY_MAX} characters`);
      const p = ctx.db.profile.owner.find(owner)!;
      const c = ctx.db.profileCalibration.owner.find(owner);
      if (!c || c.jobId !== j.id || p.revision !== j.inputRevision) fail('STALE_SNAPSHOT', 'the profile changed after this job started');
      ctx.db.profileCalibration.owner.update({ ...c, state: 'PROPOSED', summary: text, updatedAt: ctx.timestamp });
      ctx.db.job.id.update({
        ...j,
        status: 'SUCCEEDED',
        outcome: 'PROPOSED',
        leaseOwner: undefined,
        leaseExpiresAt: undefined,
        updatedAt: ctx.timestamp,
      });
      return {};
    });
  }
);

const ProfileAnswerRow = t.row('ProfileAnswerRow', {
  id: t.u64().primaryKey(),
  questionId: t.string(),
  key: t.string(),
  status: t.string(),
  valueJson: t.string(),
  catalogVersion: t.u32(),
  updatedAt: t.timestamp(),
});

// Your own answers only.
export const myProfileAnswers = spacetimedb.view({ name: 'my_profile_answers', public: true }, t.array(ProfileAnswerRow), ctx =>
  !hasAppAccess(ctx.db, ctx.sender) ? [] :
  [...ctx.db.profileAnswer.byOwner.filter(ctx.sender)].map(a => ({
    id: a.id,
    questionId: a.questionId,
    key: a.key,
    status: a.status,
    valueJson: a.valueJson,
    catalogVersion: a.catalogVersion,
    updatedAt: a.updatedAt,
  }))
);

export const myProfileCalibration = spacetimedb.view(
  { name: 'my_profile_calibration', public: true },
  t.option(profileCalibration.rowType),
  ctx => {
    if (!hasAppAccess(ctx.db, ctx.sender)) return undefined;
    const row = ctx.db.profileCalibration.owner.find(ctx.sender);
    if (!row || row.state !== 'PENDING') return row ?? undefined;
    const profile = ctx.db.profile.owner.find(ctx.sender);
    if (profile && profile.revision !== row.profileRevision) return { ...row, state: 'STALE' };
    const job = row.jobId === undefined ? undefined : ctx.db.job.id.find(row.jobId);
    if (!job || ['FINISHED', 'FAILED', 'CANCELLED'].includes(job.status)) return { ...row, state: 'FAILED' };
    return row;
  }
);
