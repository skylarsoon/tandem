import { t } from 'spacetimedb/server';
import spacetimedb from './schema';
import {
  type Ctx,
  type RoomRow,
  COVERAGE,
  INTAKE_TOPICS,
  MAX_INTAKE_QUESTIONS,
  emitEvent,
  fail,
  invalidateRoom,
  nonEmpty,
  oneOf,
  parseJson,
  requireHuman,
  requireLease,
  requireMember,
  requireMembershipById,
  requireRoom,
  requireWorker,
  validateRoomSettings,
  withReceipt,
} from './guards';
import { applyParticipationPreferences, constraintFromPatch, upsertConstraint } from './effects';
import { MONEY_KEYS, findIntake, ownerConstraintKeys, queueAgent, startIntake } from './onboarding';

// Conversational onboarding. The organizer describes the trip in their own
// words; their agent proposes a structured brief they confirm. Then each
// traveller's agent asks a few questions about the essentials (where they fly
// from, whether the dates work, money, how the agent should act) and proposes
// what it heard as patches, each quoting the traveller's own words. Nothing
// becomes a constraint until the owner confirms it: the origin inline, the rest
// together in one summary, so the room replans once per person.

function requireOrganizer(ctx: Ctx, roomId: bigint): RoomRow {
  const r = requireRoom(ctx, roomId);
  if (!r.organizer.isEqual(ctx.sender)) fail('NOT_AUTHORIZED', 'only the organizer can describe the trip');
  return r;
}

// ---------- Organizer ----------

export const setTripDescription = spacetimedb.reducer({ roomId: t.u64(), description: t.string() }, (ctx, args) => {
  requireHuman(ctx);
  const r = requireOrganizer(ctx, args.roomId);
  const description = nonEmpty(args.description, 'description').trim();
  if (description.length > 4000) fail('PAYLOAD_TOO_LARGE', 'description is limited to 4000 characters');
  const existing = ctx.db.tripBrief.roomId.find(r.id);
  const row = {
    roomId: r.id,
    description,
    briefJson: existing?.briefJson ?? '{}',
    unclearJson: '[]',
    state: 'DRAFT',
    revision: (existing?.revision ?? 0n) + 1n,
    updatedAt: ctx.timestamp,
  };
  if (existing) ctx.db.tripBrief.roomId.update(row);
  else ctx.db.tripBrief.insert(row);
  queueAgent(ctx, r, requireMember(ctx, r.id), 'BRIEF', 'trip description');
});

// The organizer confirms the brief their agent proposed. It becomes the room's
// shared settings (what to plan, where, when) and starts everyone's onboarding.
// Re-confirming a changed brief reopens only the topics it affects.
export const confirmTripBrief = spacetimedb.reducer({ roomId: t.u64(), revision: t.u64() }, (ctx, args) => {
  requireHuman(ctx);
  const r = requireOrganizer(ctx, args.roomId);
  const brief = ctx.db.tripBrief.roomId.find(r.id);
  if (!brief || brief.state !== 'PROPOSED') fail('INVALID_STATE', 'no proposed brief to confirm');
  if (brief.revision !== args.revision) fail('STALE_SNAPSHOT', 'the brief changed; review it again');
  const proposed = JSON.parse(brief.briefJson) as Record<string, unknown>;
  const before = JSON.parse(r.settingsJson) as Record<string, unknown>;
  const settings = validateRoomSettings({
    ...before,
    destination: proposed.destination,
    destination_airport: proposed.destination_airport,
    start_date: proposed.start_date,
    end_date: proposed.end_date,
    components: proposed.components ?? before.components,
  });
  const reopen = [
    ...(before.destination_airport !== undefined && before.destination_airport !== settings.destination_airport ? ['origin'] : []),
    ...(before.start_date !== undefined && (before.start_date !== settings.start_date || before.end_date !== settings.end_date)
      ? ['dates']
      : []),
  ];
  ctx.db.tripBrief.roomId.update({ ...brief, state: 'CONFIRMED', updatedAt: ctx.timestamp });
  ctx.db.room.id.update({ ...r, settingsJson: JSON.stringify(settings) });
  const updated = invalidateRoom(ctx, r.id, { input: true }, 'trip brief confirmed');
  for (const m of ctx.db.membership.byRoomMember.filter(r.id)) startIntake(ctx, updated, m, reopen);
  emitEvent(ctx, r.id, 'TRIP_BRIEF_CONFIRMED', {
    destination: settings.destination,
    start_date: settings.start_date,
    end_date: settings.end_date,
    components: settings.components,
  });
});

// ---------- Traveller ----------

// Where someone flies from is confirmed the moment they say it, so searches can
// start long before the rest of their onboarding is done.
export const confirmOrigin = spacetimedb.reducer({ roomId: t.u64(), patchId: t.u64() }, (ctx, args) => {
  requireHuman(ctx);
  requireMember(ctx, args.roomId);
  const patch = ctx.db.constraintPatch.id.find(args.patchId);
  if (!patch || patch.roomId !== args.roomId || !patch.owner.isEqual(ctx.sender)) fail('NOT_FOUND', 'patch');
  if (patch.state !== 'PENDING') fail('INVALID_STATE', 'patch is not pending');
  const input = constraintFromPatch(patch.patchJson);
  if (input.key !== 'origin_airport') fail('INVALID_ARGUMENT', 'only the origin is confirmed on its own');
  ctx.db.constraintPatch.id.update({ ...patch, state: 'CONFIRMED' });
  const intake = findIntake(ctx, args.roomId, ctx.sender);
  if (intake) {
    const coverage = { ...(JSON.parse(intake.coverageJson) as Record<string, string>), origin: 'ANSWERED' };
    ctx.db.intake.id.update({ ...intake, coverageJson: JSON.stringify(coverage), updatedAt: ctx.timestamp });
  }
  upsertConstraint(ctx, args.roomId, input);
});

// The owner confirms their onboarding summary in one go: the patches they accept
// become constraints, the rest are rejected, and the room replans once.
export const confirmIntake = spacetimedb.reducer(
  {
    roomId: t.u64(),
    acceptedPatchIds: t.array(t.u64()),
    speakUpForMe: t.option(t.bool()),
    askMeFirst: t.option(t.bool()),
    giveMeTime: t.option(t.bool()),
    remindersOptedIn: t.option(t.bool()),
  },
  (ctx, args) => {
    requireHuman(ctx);
    requireMember(ctx, args.roomId);
    const intake = findIntake(ctx, args.roomId, ctx.sender);
    if (!intake || intake.state !== 'SUMMARY') fail('INVALID_STATE', 'no onboarding summary to confirm');
    const accepted = new Set(args.acceptedPatchIds.map(id => id.toString()));
    const seen = new Set<string>();
    for (const patch of [...ctx.db.constraintPatch.byOwner.filter(ctx.sender)]) {
      if (patch.roomId !== args.roomId || patch.state !== 'PENDING') continue;
      if (accepted.has(patch.id.toString())) {
        upsertConstraint(ctx, args.roomId, constraintFromPatch(patch.patchJson), false);
        ctx.db.constraintPatch.id.update({ ...patch, state: 'CONFIRMED' });
        seen.add(patch.id.toString());
      } else {
        ctx.db.constraintPatch.id.update({ ...patch, state: 'REJECTED' });
      }
    }
    if (seen.size !== accepted.size) fail('NOT_FOUND', 'an accepted patch is not pending in this room');

    const settings = [args.speakUpForMe, args.askMeFirst, args.giveMeTime];
    if (settings.some(v => v !== undefined)) {
      if (settings.some(v => v === undefined)) fail('INVALID_ARGUMENT', 'confirm all three agent settings together');
      applyParticipationPreferences(ctx, args.speakUpForMe!, args.askMeFirst!, args.giveMeTime!, args.remindersOptedIn ?? false);
    }
    if (!ctx.db.participationPreferences.owner.find(ctx.sender)?.confirmed) {
      fail('PREFERENCES_UNCONFIRMED', 'confirm how your agent should act for you');
    }
    const keys = ownerConstraintKeys(ctx, args.roomId, ctx.sender);
    if (!MONEY_KEYS.some(k => keys.has(k))) fail('INTAKE_INCOMPLETE', 'a budget or an explicit "no limit" is needed');
    if (!keys.has('origin_airport')) fail('INTAKE_INCOMPLETE', 'confirm where you fly from');

    ctx.db.intake.id.update({ ...intake, state: 'CONFIRMED', updatedAt: ctx.timestamp });
    invalidateRoom(ctx, args.roomId, { input: true }, 'onboarding confirmed');
    emitEvent(ctx, args.roomId, 'TRAVELLER_READY', { message: 'A traveller finished onboarding' });
  }
);

// ---------- Worker ----------

const BRIEF_COMPONENTS = ['FLIGHT', 'STAY', 'TRANSFER'];

export const proposeTripBrief = spacetimedb.reducer(
  { jobId: t.u64(), attempt: t.u32(), requestKey: t.string(), briefJson: t.string(), unclearJson: t.string() },
  (ctx, args) => {
    requireWorker(ctx);
    withReceipt(ctx, 'propose_trip_brief', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      const brief = ctx.db.tripBrief.roomId.find(j.roomId);
      if (!brief || brief.state === 'CONFIRMED') fail('INVALID_STATE', 'no trip description awaiting a brief');
      const proposed = parseJson(args.briefJson, 'briefJson') as Record<string, unknown>;
      if (!proposed || typeof proposed !== 'object' || Array.isArray(proposed)) fail('INVALID_ARGUMENT', 'brief must be an object');
      nonEmpty(String(proposed.destination ?? ''), 'destination');
      validateRoomSettings({
        destination_airport: proposed.destination_airport,
        start_date: proposed.start_date,
        end_date: proposed.end_date,
        components: proposed.components,
      });
      if (!Array.isArray(proposed.components) || proposed.components.some(c => !BRIEF_COMPONENTS.includes(c as string))) {
        fail('INVALID_ARGUMENT', 'components');
      }
      const unclear = parseJson(args.unclearJson, 'unclearJson');
      if (!Array.isArray(unclear)) fail('INVALID_ARGUMENT', 'unclearJson must be a list');
      ctx.db.tripBrief.roomId.update({
        ...brief,
        briefJson: args.briefJson,
        unclearJson: args.unclearJson,
        state: 'PROPOSED',
        revision: brief.revision + 1n,
        updatedAt: ctx.timestamp,
      });
      return {};
    });
  }
);

export const updateIntake = spacetimedb.reducer(
  {
    jobId: t.u64(),
    attempt: t.u32(),
    requestKey: t.string(),
    memberId: t.u64(),
    coverageJson: t.string(),
    state: t.string(),
    askedQuestion: t.bool(),
  },
  (ctx, args) => {
    requireWorker(ctx);
    withReceipt(ctx, 'update_intake', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      const m = requireMembershipById(ctx, j.roomId, args.memberId);
      const intake = findIntake(ctx, j.roomId, m.member);
      if (!intake) fail('NOT_FOUND', 'intake');
      if (intake.state === 'CONFIRMED') fail('INVALID_STATE', 'onboarding is already confirmed');
      if (args.state !== 'ASKING' && args.state !== 'SUMMARY') fail('INVALID_ARGUMENT', 'state must be ASKING or SUMMARY');
      const coverage = parseJson(args.coverageJson, 'coverageJson') as Record<string, unknown>;
      for (const topic of INTAKE_TOPICS) oneOf(String(coverage?.[topic]), COVERAGE, `coverage.${topic}`);
      const questions = intake.questionCount + (args.askedQuestion ? 1 : 0);
      // Onboarding stays short; anything else is asked when a plan depends on it.
      if (questions > MAX_INTAKE_QUESTIONS) fail('TURN_BUDGET_EXHAUSTED', 'onboarding question limit reached');
      ctx.db.intake.id.update({
        ...intake,
        coverageJson: args.coverageJson,
        state: args.state,
        questionCount: questions,
        updatedAt: ctx.timestamp,
      });
      return {};
    });
  }
);

// When the owner writes again after the summary, the agent withdraws its pending
// patches before proposing corrected ones, so stale items can't be confirmed.
export const withdrawConstraintPatches = spacetimedb.reducer(
  { jobId: t.u64(), attempt: t.u32(), requestKey: t.string(), memberId: t.u64() },
  (ctx, args) => {
    requireWorker(ctx);
    withReceipt(ctx, 'withdraw_constraint_patches', args.requestKey, args, () => {
      const j = requireLease(ctx, args.jobId, args.attempt);
      const m = requireMembershipById(ctx, j.roomId, args.memberId);
      for (const patch of [...ctx.db.constraintPatch.byOwner.filter(m.member)]) {
        if (patch.roomId === j.roomId && patch.state === 'PENDING') ctx.db.constraintPatch.id.update({ ...patch, state: 'REJECTED' });
      }
      return {};
    });
  }
);

