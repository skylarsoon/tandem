import { SenderError, type InferSchema, type ReducerCtx } from 'spacetimedb/server';
import type { Identity } from 'spacetimedb';
import { sha256, bytesToHex } from '@spacetimedb/crypto';
import spacetimedb from './schema';

export type Ctx = ReducerCtx<InferSchema<typeof spacetimedb>>;
export type RoomRow = NonNullable<ReturnType<Ctx['db']['room']['id']['find']>>;
export type JobRow = NonNullable<ReturnType<Ctx['db']['job']['id']['find']>>;
export type MembershipRow = NonNullable<ReturnType<Ctx['db']['membership']['id']['find']>>;
export type AgentRow = NonNullable<ReturnType<Ctx['db']['agent']['id']['find']>>;

// ---------- Vocabulary ----------

export const ROOM_STATUS = ['COLLECTING', 'NEGOTIATING', 'AWAITING_REVIEW', 'AGREED'] as const;
export const JOB_KINDS = ['PLAN', 'INTERPRET', 'INTAKE', 'PROFILE'] as const;
export const BRIEF_STATES = ['DRAFT', 'PROPOSED', 'CONFIRMED'] as const;
export const INTAKE_STATES = ['ASKING', 'SUMMARY', 'CONFIRMED'] as const;
// Essential onboarding topics; everything else is captured if mentioned or asked just in time.
export const INTAKE_TOPICS = ['origin', 'dates', 'money', 'settings'] as const;
export const COVERAGE = ['OPEN', 'ANSWERED', 'UNCLEAR', 'SKIPPED'] as const;
export const MAX_INTAKE_QUESTIONS = 5;
export const COMPONENTS = ['FLIGHT', 'STAY', 'TRANSFER'];
export const OPEN_JOB_STATUSES = ['PENDING', 'CLAIMED', 'WAITING_FOR_INPUT'];
export const STAGES = ['UNDERSTAND', 'GOALS_DATES', 'TRAVEL', 'STAY_EXPERIENCES', 'COMPLETE_PLAN'] as const;
export const HARDNESS = ['HARD', 'SOFT'] as const;
export const QUESTION_KINDS = [
  'MISSING_FACT',
  'PARTICIPATION_CONFIRMATION',
  'OBJECTION_DISCLOSURE',
  'CONCESSION_CONSULTATION',
  'BROADER_EXPLORATION',
] as const;
export const GRANT_KINDS = ['OBJECTION_DISCLOSURE', 'CONCESSION_CONSULTATION', 'BROADER_EXPLORATION'];
export const MESSAGE_TYPES = [
  'PROPOSE',
  'EVALUATE',
  'OBJECT',
  'COUNTERPROPOSE',
  'CLARIFY',
  'OWNER_RESPONSE',
  'VALIDATE_REQUEST',
  'VALIDATION_RESULT',
  'REQUEST_HUMAN_REVIEW',
  'ROUND_PAUSED',
  'ROUND_CLOSED',
] as const;
export const AUDIENCES = ['ROOM', 'OWNER_PRIVATE', 'INTERNAL'] as const;
export const AGENT_ACTIVITIES = ['IDLE', 'THINKING', 'SEARCHING', 'CHECKING', 'WRITING', 'WAITING_FOR_OWNER'] as const;
export const SANITIZATION_STATUSES = ['VALIDATED', 'TEMPLATE_FALLBACK'] as const;
export const RESPONSES = ['ACCEPT', 'NOT_COMFORTABLE', 'ASK_MY_AGENT', 'SUGGEST_ALTERNATIVE', 'DECIDE_LATER'] as const;
export const CANDIDATE_MODES = ['APIFY_OBSERVED', 'PROVIDER_VERIFIED', 'CACHED_WITH_TIMESTAMP', 'SANDBOX', 'FIXTURE'] as const;
export const CANDIDATE_CATEGORIES = ['FLIGHT', 'STAY', 'TRANSFER', 'ACTIVITY', 'DINNER'] as const;

export const MESSAGE_SCHEMA_VERSION = 2;
export const DEFAULT_THREAD_TURN_BUDGET = 6;
export const DEFAULT_ROUND_TURN_BUDGET = 24;
export const MIN_LEASE_SECONDS = 2;
export const MAX_LEASE_SECONDS = 300;
const MIN_PRIVATE_TEXT_CHECK_LENGTH = 6;

// ---------- Errors ----------

// Reducers surface errors as HTTP 530 with body "CODE: detail".
export function fail(code: string, detail: string): never {
  throw new SenderError(`${code}: ${detail}`);
}

export function oneOf<T extends string>(value: string, allowed: readonly T[], field: string): T {
  if (!(allowed as readonly string[]).includes(value)) {
    fail('INVALID_ARGUMENT', `${field} must be one of ${allowed.join(', ')}`);
  }
  return value as T;
}

export function parseJson(text: string, field: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    fail('INVALID_ARGUMENT', `${field} is not valid JSON`);
  }
}

export function nonEmpty(value: string, field: string): string {
  if (value.trim().length === 0) fail('INVALID_ARGUMENT', `${field} must not be empty`);
  return value;
}

// ---------- Serialization ----------

// Deterministic JSON with sorted keys; bigint and Identity/Timestamp become strings.
export function canonical(value: unknown): string {
  return JSON.stringify(normalize(value));
}

function normalize(value: unknown): unknown {
  if (typeof value === 'bigint') return value.toString();
  if (value === undefined) return null;
  if (value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(normalize);
  const obj = value as Record<string, unknown>;
  if ('__identity__' in obj) return `0x${(obj.__identity__ as bigint).toString(16).padStart(64, '0')}`;
  if ('__timestamp_micros_since_unix_epoch__' in obj) {
    return (obj.__timestamp_micros_since_unix_epoch__ as bigint).toString();
  }
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(obj).sort()) out[key] = normalize(obj[key]);
  return out;
}

export function digest(text: string): string {
  return bytesToHex(sha256(new TextEncoder().encode(text)));
}

// Byte-for-byte equal to Python's json.dumps(value, sort_keys=True,
// separators=(",", ":")) with its default ensure_ascii=True, so the backend can
// verify digests the worker computes. Values must be JSON-native (no floats
// that differ between languages, e.g. 1.0).
export function pythonJson(value: unknown): string {
  return stringifySorted(value).replace(
    /[\u007f-\uffff]/g,
    ch => (ch === '\u007f' ? ch : `\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}`)
  );
}

function stringifySorted(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stringifySorted).join(',')}]`;
  const obj = value as Record<string, unknown>;
  return `{${Object.keys(obj)
    .sort()
    .map(k => `${JSON.stringify(k)}:${stringifySorted(obj[k])}`)
    .join(',')}}`;
}

export function num(value: bigint): number {
  return Number(value);
}

// ---------- Receipts (idempotency) ----------

// Same key + same request → returns the stored outcome without re-running.
// Same key + different request → KEY_BODY_MISMATCH.
export function withReceipt(ctx: Ctx, op: string, key: string, request: unknown, run: () => unknown): unknown {
  nonEmpty(key, 'request key');
  const receiptKey = `${op}:${key}`;
  // The attempt is an authorization fence, not part of the logical operation:
  // a re-claimed attempt replaying the same key gets the original outcome.
  const { attempt: _attempt, ...logical } = request as Record<string, unknown>;
  const requestDigest = digest(canonical(logical));
  const existing = ctx.db.commandReceipt.key.find(receiptKey);
  if (existing) {
    // A re-claimed attempt may run under another registered worker identity.
    const sameCaller = existing.caller.isEqual(ctx.sender);
    const workerReplay = isWorker(ctx) && ctx.db.worker.identity.find(existing.caller) != null;
    if (!sameCaller && !workerReplay) fail('NOT_AUTHORIZED', `${op} key ${key} belongs to another caller`);
    if (existing.requestDigest !== requestDigest) {
      fail('KEY_BODY_MISMATCH', `${op} key ${key} was already used with a different request`);
    }
    return JSON.parse(existing.outcomeJson);
  }
  const outcome = run() ?? {};
  ctx.db.commandReceipt.insert({
    key: receiptKey,
    caller: ctx.sender,
    requestDigest,
    outcomeJson: canonical(outcome),
    createdAt: ctx.timestamp,
  });
  return outcome;
}

// Procedures return a JSON string and never throw: throwing inside a procedure
// surfaces as an HTTP 500 "fatal error", so failures become {"ok": false, "code"}.
export function runProcedure(fn: () => object): string {
  try {
    return JSON.stringify({ ok: true, ...fn() });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    const match = e instanceof SenderError ? /^([A-Z_]+): (.*)$/s.exec(message) : null;
    return JSON.stringify(
      match ? { ok: false, code: match[1], detail: match[2] } : { ok: false, code: 'INTERNAL', detail: message }
    );
  }
}

// ---------- Identity checks ----------

export function isWorker(ctx: Ctx): boolean {
  return ctx.db.worker.identity.find(ctx.sender) != null;
}

export function requireWorker(ctx: Ctx): void {
  if (!isWorker(ctx)) fail('NOT_AUTHORIZED', 'caller is not a registered worker');
}

export function requireHuman(ctx: Ctx): void {
  if (isWorker(ctx)) fail('NOT_AUTHORIZED', 'worker identities cannot perform human actions');
}

export function requireRoom(ctx: Ctx, roomId: bigint): RoomRow {
  const row = ctx.db.room.id.find(roomId);
  if (!row) fail('NOT_FOUND', `room ${roomId}`);
  return row;
}

export function findMembership(ctx: Ctx, roomId: bigint, member: Identity): MembershipRow | undefined {
  for (const m of ctx.db.membership.byRoomMember.filter([roomId, member])) return m;
  return undefined;
}

export function requireMember(ctx: Ctx, roomId: bigint, member: Identity = ctx.sender): MembershipRow {
  const m = findMembership(ctx, roomId, member);
  if (!m || !m.active) fail('NOT_A_MEMBER', `not an active member of room ${roomId}`);
  return m;
}

export function requireMembershipById(ctx: Ctx, roomId: bigint, membershipId: bigint): MembershipRow {
  const m = ctx.db.membership.id.find(membershipId);
  if (!m || m.roomId !== roomId || !m.active) fail('NOT_A_MEMBER', `member ${membershipId} is not active in room ${roomId}`);
  return m;
}

export function personalAgentFor(ctx: Ctx, roomId: bigint, membershipId: bigint): AgentRow | undefined {
  for (const a of ctx.db.agent.byRoom.filter(roomId)) {
    if (a.kind === 'PERSONAL' && a.membershipId === membershipId) return a;
  }
  return undefined;
}

// ---------- Leases and snapshots ----------

// Every worker write runs through this: the job must be claimed by the caller
// under this attempt, the lease must be live, and for PLAN jobs the room's
// revisions must still match the job's snapshot.
export function requireLease(ctx: Ctx, jobId: bigint, attempt: number): JobRow {
  requireWorker(ctx);
  const j = ctx.db.job.id.find(jobId);
  if (!j) fail('NOT_FOUND', `job ${jobId}`);
  if (j.status === 'SUPERSEDED' || j.status === 'CANCELLED') fail('STALE_SNAPSHOT', `job ${jobId} is ${j.status}`);
  if (j.status !== 'CLAIMED') fail('LEASE_LOST', `job ${jobId} is ${j.status}`);
  if (j.attempt !== attempt) fail('LEASE_LOST', `attempt ${attempt} was replaced by attempt ${j.attempt}`);
  if (!j.leaseOwner || !j.leaseOwner.isEqual(ctx.sender)) fail('LEASE_LOST', 'lease is held by another worker');
  if (!j.leaseExpiresAt || j.leaseExpiresAt.microsSinceUnixEpoch <= ctx.timestamp.microsSinceUnixEpoch) {
    fail('LEASE_LOST', `lease for attempt ${attempt} expired`);
  }
  if (j.kind === 'PLAN') {
    const r = requireRoom(ctx, j.roomId);
    if (
      r.inputRevision !== j.inputRevision ||
      r.membershipRevision !== j.membershipRevision ||
      r.permissionRevision !== j.permissionRevision
    ) {
      fail('STALE_SNAPSHOT', `room ${r.id} changed since job ${jobId} started`);
    }
  }
  return j;
}

// ---------- Jobs, events, invalidation ----------

export function createJob(ctx: Ctx, r: RoomRow, kind: string, reason: string): JobRow {
  return ctx.db.job.insert({
    id: 0n,
    roomId: r.id,
    kind,
    status: 'PENDING',
    reason,
    inputRevision: r.inputRevision,
    membershipRevision: r.membershipRevision,
    permissionRevision: r.permissionRevision,
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
}

export function emitEvent(
  ctx: Ctx,
  roomId: bigint,
  type: string,
  payload: unknown,
  owner: Identity | undefined = undefined
): void {
  ctx.db.decisionEvent.insert({
    id: 0n,
    roomId,
    type,
    audience: owner ? 'OWNER_PRIVATE' : 'ROOM',
    owner,
    safePayloadJson: canonical(payload),
    createdAt: ctx.timestamp,
  });
}

function roomHasConstraints(ctx: Ctx, roomId: bigint): boolean {
  for (const _ of ctx.db.participantConstraint.byRoomOwnerKey.filter(roomId)) return true;
  return false;
}

export type RevisionChange = { input?: boolean; membership?: boolean; permission?: boolean };

// A confirmed change to inputs, membership or permissions starts a new
// generation: open PLAN jobs, their questions and threads are superseded,
// proposals awaiting review become SUPERSEDED with STALE consents, and a fresh
// PLAN job is queued if the room has any constraints to plan with.
export function invalidateRoom(ctx: Ctx, roomId: bigint, change: RevisionChange, reason: string): RoomRow {
  const before = requireRoom(ctx, roomId);
  const r = ctx.db.room.id.update({
    ...before,
    inputRevision: before.inputRevision + (change.input ? 1n : 0n),
    membershipRevision: before.membershipRevision + (change.membership ? 1n : 0n),
    permissionRevision: before.permissionRevision + (change.permission ? 1n : 0n),
  });

  for (const j of [...ctx.db.job.byRoom.filter(roomId)]) {
    if (j.kind !== 'PLAN' || !OPEN_JOB_STATUSES.includes(j.status)) continue;
    ctx.db.job.id.update({
      ...j,
      status: 'SUPERSEDED',
      leaseOwner: undefined,
      leaseExpiresAt: undefined,
      updatedAt: ctx.timestamp,
    });
    for (const q of [...ctx.db.question.byJob.filter(j.id)]) {
      if (q.state === 'OPEN') ctx.db.question.id.update({ ...q, state: 'SUPERSEDED' });
    }
    closeJobThreads(ctx, j.id, 'SUPERSEDED');
  }

  for (const p of [...ctx.db.proposal.byRoomStage.filter(roomId)]) {
    if (p.state !== 'AWAITING_REVIEW') continue;
    ctx.db.proposal.id.update({ ...p, state: 'SUPERSEDED' });
    for (const c of [...ctx.db.consent.byProposalMember.filter(p.id)]) {
      ctx.db.consent.id.update({ ...c, response: 'STALE', updatedAt: ctx.timestamp });
    }
    refreshMilestone(ctx, roomId, p.stage);
  }

  if (roomHasConstraints(ctx, roomId)) createJob(ctx, r, 'PLAN', reason);
  return r;
}

export function closeJobThreads(ctx: Ctx, jobId: bigint, reason: string): void {
  for (const th of [...ctx.db.conversationThread.byJob.filter(jobId)]) {
    if (th.state === 'OPEN') ctx.db.conversationThread.id.update({ ...th, state: 'CLOSED', closureReason: reason });
  }
}

// A stage holds several independent decisions, so its milestone is derived from
// all of them: IN_REVIEW while any is awaiting review, AGREED once those that
// remain are agreed, REOPENED when none is live.
export function refreshMilestone(ctx: Ctx, roomId: bigint, stage: string): void {
  let pending: bigint | undefined;
  let agreed: bigint | undefined;
  for (const p of ctx.db.proposal.byRoomStage.filter([roomId, stage])) {
    if (p.state === 'AWAITING_REVIEW' && (pending === undefined || p.id > pending)) pending = p.id;
    if (p.state === 'AGREED' && (agreed === undefined || p.id > agreed)) agreed = p.id;
  }
  if (pending !== undefined) setMilestone(ctx, roomId, stage, 'IN_REVIEW', pending);
  else if (agreed !== undefined) setMilestone(ctx, roomId, stage, 'AGREED', agreed);
  else setMilestone(ctx, roomId, stage, 'REOPENED', undefined);
}

export function setMilestone(ctx: Ctx, roomId: bigint, stage: string, state: string, proposalId: bigint | undefined): void {
  for (const m of ctx.db.milestone.byRoomStage.filter([roomId, stage])) {
    ctx.db.milestone.id.update({ ...m, state, proposalId, updatedAt: ctx.timestamp });
    return;
  }
}

// Claim and evidence refs must point at facts the room already holds:
// a recorded candidate ref, "msg:<id>", "proposal:<id>", or "draft:<ref>".
export function refResolves(ctx: Ctx, roomId: bigint, ref: string): boolean {
  // `<ref>#<field>` cites one field of a record (e.g. `apify-x#price`); the record must resolve.
  const hash = ref.indexOf('#');
  if (hash >= 0) {
    if (!/^[a-z_]+$/.test(ref.slice(hash + 1))) return false;
    ref = ref.slice(0, hash);
  }
  const colon = ref.indexOf(':');
  const prefix = colon >= 0 ? ref.slice(0, colon) : '';
  const rest = colon >= 0 ? ref.slice(colon + 1) : ref;
  if (prefix === 'draft') return rest.length > 0;
  if (prefix === 'msg' || prefix === 'proposal') {
    if (!/^\d+$/.test(rest)) return false;
    const row = prefix === 'msg' ? ctx.db.agentMessage.id.find(BigInt(rest)) : ctx.db.proposal.id.find(BigInt(rest));
    return row != null && row.roomId === roomId;
  }
  for (const _ of ctx.db.candidate.byRoomRef.filter([roomId, ref])) return true;
  return false;
}

// Exact-substring check for owners' private reasons in text bound for the room.
// Semantic wording validation stays in the worker; this is defense in depth.
export function containsPrivateText(ctx: Ctx, roomId: bigint, text: string): boolean {
  const haystack = text.toLowerCase();
  for (const c of ctx.db.participantConstraint.byRoomOwnerKey.filter(roomId)) {
    const reason = c.privateRationale?.trim().toLowerCase();
    if (reason && reason.length >= MIN_PRIVATE_TEXT_CHECK_LENGTH && haystack.includes(reason)) return true;
  }
  return false;
}

// ---------- Room settings ----------

// Shared planning rules, validated the same way whether the organizer sets them
// directly or they come from a confirmed trip brief.
export function validateRoomSettings(settings: unknown): Record<string, unknown> {
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) {
    fail('INVALID_ARGUMENT', 'settingsJson must be an object');
  }
  const s = settings as Record<string, unknown>;
  const overlap = s.arrival_overlap_seconds;
  if (overlap !== undefined && (typeof overlap !== 'number' || !Number.isInteger(overlap) || overlap < 0)) {
    fail('INVALID_ARGUMENT', 'arrival_overlap_seconds must be a non-negative integer');
  }
  const components = s.components;
  if (
    components !== undefined &&
    (!Array.isArray(components) ||
      !components.includes('FLIGHT') ||
      new Set(components).size !== components.length ||
      components.some(c => !COMPONENTS.includes(c as string)))
  ) {
    fail('INVALID_ARGUMENT', 'components must list FLIGHT and optionally STAY and TRANSFER, once each');
  }
  const perRoom = s.people_per_bedroom;
  if (perRoom !== undefined && (typeof perRoom !== 'number' || !Number.isInteger(perRoom) || perRoom < 1 || perRoom > 8)) {
    fail('INVALID_ARGUMENT', 'people_per_bedroom must be an integer from 1 to 8');
  }
  for (const key of ['start_date', 'end_date']) {
    if (s[key] !== undefined && (typeof s[key] !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s[key] as string))) {
      fail('INVALID_ARGUMENT', `${key} must be YYYY-MM-DD`);
    }
  }
  if (s.destination_airport !== undefined && (typeof s.destination_airport !== 'string' || !/^[A-Z]{3}$/.test(s.destination_airport))) {
    fail('INVALID_ARGUMENT', 'destination_airport must be a 3-letter IATA code');
  }
  return s;
}

// "HH:MM" → minutes after midnight, or undefined if malformed.
export function minutesOf(value: unknown): number | undefined {
  if (typeof value !== 'string' || !/^\d{2}:\d{2}$/.test(value)) return undefined;
  const [h, m] = value.split(':').map(Number);
  return h < 24 && m < 60 ? h * 60 + m : undefined;
}

