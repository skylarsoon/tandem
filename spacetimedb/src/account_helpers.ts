import { ScheduleAt, Timestamp, type Identity } from 'spacetimedb';
import { type Ctx, fail, isWorker } from './guards';
export const minute = 60_000_000n;
export function gateway(ctx: Ctx) {
  if (!ctx.db.adminGateway.identity.find(ctx.sender)?.enabled) fail('NOT_AUTHORIZED', 'account gateway required');
}
export function hash(value: string) {
  if (!/^[a-f0-9]{64}$/.test(value)) fail('INVALID_ARGUMENT', 'invalid digest');
}
export function email(value: string) {
  const normalized = value.trim().toLowerCase();
  if (normalized.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalized)) fail('INVALID_ARGUMENT', 'valid email required');
  return normalized;
}
export function credential(passwordHash: string, passwordSalt: string) {
  if (!/^[a-f0-9]{128}$/.test(passwordHash) || !/^[a-f0-9]{64}$/.test(passwordSalt)) fail('INVALID_ARGUMENT', 'scrypt credential required');
}
export function requireParticipant(ctx: Ctx) {
  if (isWorker(ctx) || ctx.db.adminGateway.identity.find(ctx.sender) || ctx.db.config.id.find(0)?.owner.isEqual(ctx.sender))
    fail('NOT_AUTHORIZED', 'participant identity required');
}
export function validSession(ctx: Ctx, tokenHash: string) {
  hash(tokenHash);
  const session = ctx.db.travellerSession.tokenHash.find(tokenHash);
  const account = session && ctx.db.travellerCredential.email.find(session.email);
  if (!session || session.revoked || session.expiresAt.microsSinceUnixEpoch <= ctx.timestamp.microsSinceUnixEpoch ||
      !account?.enabled || account.version !== session.version) fail('NOT_AUTHORIZED', 'sign in again');
  return { session, account };
}
export function startSession(ctx: Ctx, account: { email: string; version: number }, tokenHash: string) {
  hash(tokenHash);
  if (ctx.db.adminSession.tokenHash.find(tokenHash)) fail('INVALID_ARGUMENT', 'session collision');
  const expiresAt = new Timestamp(ctx.timestamp.microsSinceUnixEpoch + 480n * minute);
  ctx.db.travellerSession.insert({ tokenHash, email: account.email, version: account.version, expiresAt, revoked: false });
  ctx.db.travellerSessionExpiry.insert({ scheduledId: 0n, scheduledAt: ScheduleAt.time(expiresAt.microsSinceUnixEpoch), sessionHash: tokenHash });
}
export function bindDevice(ctx: Ctx, email: string, owner: Identity, sessionHash: string) {
  const row = { identity: ctx.sender, email, owner, sessionHash };
  if (ctx.db.participantDevice.identity.find(ctx.sender)) ctx.db.participantDevice.identity.update(row);
  else ctx.db.participantDevice.insert(row);
  if (ctx.connectionId) {
    const old = ctx.db.presence.identity.find(ctx.sender);
    const presence = { identity: ctx.sender, connections: Math.max(1, old?.connections ?? 0), online: true, lastSeen: ctx.timestamp };
    if (old) ctx.db.presence.identity.update(presence); else ctx.db.presence.insert(presence);
  }
}
