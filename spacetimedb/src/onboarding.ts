import type { Identity } from 'spacetimedb';
import { type Ctx, type MembershipRow, type RoomRow, createJob, emitEvent } from './guards';
import { hasMoneyRule } from './room_rules';

// Onboarding state transitions shared by reducers. Not exported from the module
// entry, so nothing here is callable by clients directly.

export const MONEY_KEYS = ['flight_budget_total', 'trip_budget_total', 'no_budget_limit'];

export function ownerConstraintKeys(ctx: Ctx, roomId: bigint, owner: Identity): Set<string> {
  const keys = new Set<string>();
  for (const c of ctx.db.participantConstraint.byRoomOwnerKey.filter([roomId, owner])) keys.add(c.key);
  return keys;
}

// Topics already settled by earlier answers or direct entry count as answered.
function initialCoverage(ctx: Ctx, roomId: bigint, owner: Identity): Record<string, string> {
  const keys = ownerConstraintKeys(ctx, roomId, owner);
  const prefs = ctx.db.participationPreferences.owner.find(owner);
  return {
    origin: keys.has('origin_airport') ? 'ANSWERED' : 'OPEN',
    dates: 'OPEN',
    money: MONEY_KEYS.some(k => keys.has(k)) || hasMoneyRule(ctx, roomId, owner) ? 'ANSWERED' : 'OPEN',
    settings: prefs?.confirmed ? 'ANSWERED' : 'OPEN',
  };
}

export function findIntake(ctx: Ctx, roomId: bigint, owner: Identity) {
  for (const i of ctx.db.intake.byOwner.filter(owner)) if (i.roomId === roomId) return i;
  return undefined;
}

export function queueAgent(ctx: Ctx, r: RoomRow, m: MembershipRow, purpose: 'BRIEF' | 'INTAKE', reason: string): void {
  const j = createJob(ctx, r, 'INTAKE', reason);
  ctx.db.jobSubject.insert({ jobId: j.id, memberId: m.id, purpose });
}

// Start (or reopen topics of) one traveller's onboarding for this room.
export function startIntake(ctx: Ctx, r: RoomRow, m: MembershipRow, reopen: string[] = []): void {
  const brief = ctx.db.tripBrief.roomId.find(r.id);
  if (!brief || brief.state !== 'CONFIRMED' || !m.active) return;
  const existing = findIntake(ctx, r.id, m.member);
  if (!existing) {
    ctx.db.intake.insert({
      id: 0n,
      roomId: r.id,
      owner: m.member,
      memberId: m.id,
      state: 'ASKING',
      coverageJson: JSON.stringify(initialCoverage(ctx, r.id, m.member)),
      questionCount: 0,
      briefRevision: brief.revision,
      createdAt: ctx.timestamp,
      updatedAt: ctx.timestamp,
    });
  } else if (reopen.length > 0) {
    const coverage = JSON.parse(existing.coverageJson) as Record<string, string>;
    for (const topic of reopen) coverage[topic] = 'OPEN';
    ctx.db.intake.id.update({
      ...existing,
      state: 'ASKING',
      coverageJson: JSON.stringify(coverage),
      briefRevision: brief.revision,
      updatedAt: ctx.timestamp,
    });
  } else {
    return;
  }
  queueAgent(ctx, r, m, 'INTAKE', 'onboarding');
  emitEvent(ctx, r.id, 'INTAKE_STARTED', {}, m.member);
}

// Whose onboarding conversation a job serves: INTAKE jobs record it, INTERPRET
// jobs are found through the private message that queued them.
export function jobSubject(ctx: Ctx, jobId: bigint): { memberId: bigint; purpose: string } | undefined {
  const s = ctx.db.jobSubject.jobId.find(jobId);
  if (s) return { memberId: s.memberId, purpose: s.purpose };
  return undefined;
}
