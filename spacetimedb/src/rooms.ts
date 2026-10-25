import { t } from 'spacetimedb/server';
import spacetimedb from './schema';
import {
  type Ctx,
  STAGES,
  emitEvent,
  fail,
  findMembership,
  invalidateRoom,
  nonEmpty,
  parseJson,
  validateRoomSettings,
  requireHuman,
  requireRoom,
} from './guards';
import { applyParticipationPreferences, reopenBroken } from './effects';
import { startIntake } from './onboarding';

const DEFAULT_REMINDER_DELAY_SECONDS = 600;
const DEFAULT_ROOM_SETTINGS = JSON.stringify({ arrival_overlap_seconds: 7200 });
const INVITE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
const INVITE_LENGTH = 8;

function newInviteCode(ctx: Ctx): string {
  for (;;) {
    let code = '';
    for (let i = 0; i < INVITE_LENGTH; i++) {
      code += INVITE_ALPHABET[ctx.random.integerInRange(0, INVITE_ALPHABET.length - 1)];
    }
    let taken = false;
    for (const _ of ctx.db.room.byInviteCode.filter(code)) taken = true;
    if (!taken) return code;
  }
}

function ensureProfile(ctx: Ctx, displayName: string): void {
  if (!ctx.db.profile.owner.find(ctx.sender)) {
    ctx.db.profile.insert({
      owner: ctx.sender,
      displayName,
      avatarRef: '',
      selfDescription: '',
      priorityRanking: [],
      revision: 1n,
      updatedAt: ctx.timestamp,
    });
  }
  // Preselected defaults grant nothing until the owner confirms them.
  if (!ctx.db.participationPreferences.owner.find(ctx.sender)) {
    ctx.db.participationPreferences.insert({
      owner: ctx.sender,
      speakUpForMe: false,
      askMeFirst: true,
      giveMeTime: true,
      remindersOptedIn: false,
      confirmed: false,
      confirmedAt: undefined,
      proposedBy: 'DEFAULT',
      revision: 0n,
    });
  }
}

function addMember(ctx: Ctx, roomId: bigint, displayName: string, role: string): void {
  const m = ctx.db.membership.insert({
    id: 0n,
    roomId,
    member: ctx.sender,
    displayName,
    role,
    active: true,
    joinedAt: ctx.timestamp,
  });
  ctx.db.agent.insert({ id: 0n, roomId, kind: 'PERSONAL', membershipId: m.id });
}

export const init = spacetimedb.init(ctx => {
  ctx.db.config.insert({ id: 0, owner: ctx.sender, reminderDelaySeconds: DEFAULT_REMINDER_DELAY_SECONDS });
});

// Owner-only: allowlist the worker and dispatcher service identities.
export const addWorker = spacetimedb.reducer(
  { identity: t.identity(), label: t.string() },
  (ctx, { identity, label }) => {
    const cfg = ctx.db.config.id.find(0);
    if (!cfg || !cfg.owner.isEqual(ctx.sender)) fail('NOT_AUTHORIZED', 'only the database owner can add workers');
    if (ctx.db.worker.identity.find(identity)) return;
    ctx.db.worker.insert({ identity, label });
  }
);

export const createRoom = spacetimedb.reducer(
  { goal: t.string(), displayName: t.string() },
  (ctx, { goal, displayName }) => {
    requireHuman(ctx);
    nonEmpty(goal, 'goal');
    nonEmpty(displayName, 'displayName');
    ensureProfile(ctx, displayName);
    const r = ctx.db.room.insert({
      id: 0n,
      goal,
      organizer: ctx.sender,
      status: 'COLLECTING',
      inputRevision: 1n,
      membershipRevision: 1n,
      permissionRevision: 1n,
      inviteCode: newInviteCode(ctx),
      settingsJson: DEFAULT_ROOM_SETTINGS,
      createdAt: ctx.timestamp,
    });
    ctx.db.agent.insert({ id: 0n, roomId: r.id, kind: 'MEDIATOR', membershipId: undefined });
    addMember(ctx, r.id, displayName, 'ORGANIZER');
    for (const stage of STAGES) {
      ctx.db.milestone.insert({
        id: 0n,
        roomId: r.id,
        stage,
        state: 'NOT_STARTED',
        proposalId: undefined,
        updatedAt: ctx.timestamp,
      });
    }
    emitEvent(ctx, r.id, 'ROOM_CREATED', { goal });
  }
);

export const joinRoom = spacetimedb.reducer(
  { inviteCode: t.string(), displayName: t.string() },
  (ctx, { inviteCode, displayName }) => {
    requireHuman(ctx);
    nonEmpty(displayName, 'displayName');
    let roomId: bigint | undefined;
    for (const r of ctx.db.room.byInviteCode.filter(inviteCode.trim().toUpperCase())) roomId = r.id;
    if (roomId === undefined) fail('NOT_FOUND', 'invalid invite code');
    const existing = findMembership(ctx, roomId, ctx.sender);
    if (existing?.active) fail('INVALID_STATE', 'already a member of this room');
    ensureProfile(ctx, displayName);
    if (existing) {
      ctx.db.membership.id.update({ ...existing, active: true, displayName });
    } else {
      addMember(ctx, roomId, displayName, 'PARTICIPANT');
    }
    const r = invalidateRoom(ctx, roomId, { membership: true }, 'member joined');
    emitEvent(ctx, roomId, 'MEMBER_JOINED', { display_name: displayName });
    // Joining a trip whose brief is confirmed starts this traveller's onboarding.
    startIntake(ctx, r, findMembership(ctx, roomId, ctx.sender)!);
  }
);

export const upsertProfile = spacetimedb.reducer(
  {
    displayName: t.string(),
    avatarRef: t.string(),
    selfDescription: t.string(),
    priorityRanking: t.array(t.string()),
  },
  (ctx, { displayName, avatarRef, selfDescription, priorityRanking }) => {
    requireHuman(ctx);
    nonEmpty(displayName, 'displayName');
    ensureProfile(ctx, displayName);
    const p = ctx.db.profile.owner.find(ctx.sender)!;
    ctx.db.profile.owner.update({
      ...p,
      displayName,
      avatarRef,
      selfDescription,
      priorityRanking,
      revision: p.revision + 1n,
      updatedAt: ctx.timestamp,
    });
  }
);

export const confirmParticipationPreferences = spacetimedb.reducer(
  { speakUpForMe: t.bool(), askMeFirst: t.bool(), giveMeTime: t.bool(), remindersOptedIn: t.bool() },
  (ctx, { speakUpForMe, askMeFirst, giveMeTime, remindersOptedIn }) => {
    requireHuman(ctx);
    applyParticipationPreferences(ctx, speakUpForMe, askMeFirst, giveMeTime, remindersOptedIn);
  }
);

// Organizer-only shared rules, e.g. {"arrival_overlap_seconds": 7200,
// "components": ["FLIGHT", "STAY", "TRANSFER"]}. These
// are planning inputs, so a change starts a new generation.
export const setRoomSettings = spacetimedb.reducer(
  { roomId: t.u64(), settingsJson: t.string() },
  (ctx, { roomId, settingsJson }) => {
    requireHuman(ctx);
    const r = requireRoom(ctx, roomId);
    if (!r.organizer.isEqual(ctx.sender)) fail('NOT_AUTHORIZED', 'only the organizer can change room settings');
    const settings = validateRoomSettings(parseJson(settingsJson, 'settingsJson'));
    ctx.db.room.id.update({ ...r, settingsJson });
    reopenBroken(ctx, roomId);  // new dates can leave an agreed pick outside the trip
    invalidateRoom(ctx, roomId, { input: true }, 'room settings changed');
    emitEvent(ctx, roomId, 'ROOM_SETTINGS_UPDATED', settings);
  }
);
