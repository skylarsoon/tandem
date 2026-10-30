import { Identity, Timestamp, ScheduleAt } from 'spacetimedb';
import { t } from 'spacetimedb/server';
import spacetimedb, { adminSessionExpiry } from './schema';
import { type Ctx, digest, isWorker, fail, runProcedure } from './guards';

const minute = 60_000_000n;
const hour = 60n * minute;
function owner(ctx: Ctx) {
  if (!ctx.db.config.id.find(0)?.owner.isEqual(ctx.sender)) fail('NOT_AUTHORIZED', 'database owner required');
}
function gateway(ctx: Ctx) {
  if (!ctx.db.adminGateway.identity.find(ctx.sender)?.enabled) fail('NOT_AUTHORIZED', 'admin gateway required');
}
function hash(value: string) {
  if (!/^[a-f0-9]{64}$/.test(value)) fail('INVALID_ARGUMENT', 'invalid token digest');
}
function audit(ctx: Ctx, email: string, action: string, subject: string) {
  ctx.db.adminAudit.insert({ id: 0n, email, action, subject, at: ctx.timestamp });
}
function validSession(ctx: Ctx, tokenHash: string) {
  hash(tokenHash);
  const row = ctx.db.adminSession.tokenHash.find(tokenHash);
  const account = row && ctx.db.adminAccount.email.find(row.email);
  if (!row || row.revoked || row.expiresAt.microsSinceUnixEpoch <= ctx.timestamp.microsSinceUnixEpoch ||
      !account?.enabled || account.version !== row.version) fail('NOT_AUTHORIZED', 'sign in again');
  return account;
}

function session(ctx: Ctx, tokenHash: string) {
  gateway(ctx);
  return validSession(ctx, tokenHash);
}

// Provisioning remains exclusively with the database owner. Admins cannot add admins.
export const configureAdminAccount = spacetimedb.reducer(
  { email: t.string(), enabled: t.bool(), passwordHash: t.string(), passwordSalt: t.string() }, (ctx, args) => {
  owner(ctx);
  const email = args.email.trim().toLowerCase();
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) fail('INVALID_ARGUMENT', 'valid email required');
  if (!/^[a-f0-9]{128}$/.test(args.passwordHash) || !/^[a-f0-9]{64}$/.test(args.passwordSalt))
    fail('INVALID_ARGUMENT', 'scrypt credential required');
  const current = ctx.db.adminAccount.email.find(email);
  if (args.enabled && !current?.enabled && [...ctx.db.adminAccount.iter()].filter(a => a.enabled).length >= 3)
    fail('ADMIN_LIMIT', 'at most three active administrators');
  const row = { email, enabled: args.enabled, version: (current?.version ?? 0) + 1, createdAt: current?.createdAt ?? ctx.timestamp,
    passwordHash: args.passwordHash, passwordSalt: args.passwordSalt, mustChangePassword: true };
  if (current) ctx.db.adminAccount.email.update(row); else ctx.db.adminAccount.insert(row);
  audit(ctx, 'database-owner', args.enabled ? 'ADMIN_ENABLED_OR_RESET' : 'ADMIN_DISABLED', email);
});
export const configureAdminGateway = spacetimedb.reducer({ identity: t.identity(), enabled: t.bool() }, (ctx, args) => {
  owner(ctx);
  if (ctx.db.adminGateway.identity.find(args.identity)) ctx.db.adminGateway.identity.update(args);
  else ctx.db.adminGateway.insert(args);
});

// Persistent rate limits apply across Vercel instances, including unknown emails.
export const adminLoginCredential = spacetimedb.procedure({ email: t.string(), clientHash: t.string() }, t.string(),
  (ctx, args) => runProcedure(() => ctx.withTx(tx => {
    gateway(tx); hash(args.clientHash);
    const email = args.email.trim().toLowerCase(), account = tx.db.adminAccount.email.find(email);
    const now = tx.timestamp.microsSinceUnixEpoch, window = 15n * minute;
    for (const row of tx.db.adminLoginLimit.iter()) {
      if (now - row.windowStart.microsSinceUnixEpoch >= window) tx.db.adminLoginLimit.key.delete(row.key);
    }
    const limits: [string, number][] = [['global', 120], [`ip:${args.clientHash}`, 20]];
    if (account) limits.push([`email:${email}`, 10]);
    for (const [key, cap] of limits) {
      if ((tx.db.adminLoginLimit.key.find(key)?.count ?? 0) >= cap) return { limited: true };
    }
    for (const [key] of limits) {
      const current = tx.db.adminLoginLimit.key.find(key);
      if (current) tx.db.adminLoginLimit.key.update({ ...current, count: current.count + 1 });
      else tx.db.adminLoginLimit.insert({ key, windowStart: tx.timestamp, count: 1 });
    }
    return { limited: false, credential: account?.enabled ? { email, version: account.version,
      passwordHash: account.passwordHash, passwordSalt: account.passwordSalt } : null };
  }))
);
export const createAdminSession = spacetimedb.procedure(
  { email: t.string(), version: t.u32(), sessionHash: t.string() }, t.string(),
  (ctx, args) => runProcedure(() => ctx.withTx(tx => {
    gateway(tx); hash(args.sessionHash);
    const account = tx.db.adminAccount.email.find(args.email);
    if (!account?.enabled || account.version !== args.version) fail('NOT_AUTHORIZED', 'sign in again');
    for (const old of tx.db.adminSession.iter()) {
      if (old.expiresAt.microsSinceUnixEpoch <= tx.timestamp.microsSinceUnixEpoch) tx.db.adminSession.tokenHash.delete(old.tokenHash);
    }
    tx.db.adminSession.insert({ tokenHash: args.sessionHash, email: account.email, version: account.version,
      expiresAt: new Timestamp(tx.timestamp.microsSinceUnixEpoch + 8n * hour), revoked: false });
    audit(tx, account.email, 'SIGNED_IN', 'admin');
    return { email: account.email, mustChangePassword: account.mustChangePassword };
  }))
);
export const adminPasswordCredential = spacetimedb.procedure({ sessionHash: t.string() }, t.string(), (ctx, args) =>
  runProcedure(() => ctx.withTx(tx => {
    const a = session(tx, args.sessionHash);
    return { passwordHash: a.passwordHash, passwordSalt: a.passwordSalt, mustChangePassword: a.mustChangePassword };
  }))
);
export const changeAdminPassword = spacetimedb.procedure(
  { sessionHash: t.string(), passwordHash: t.string(), passwordSalt: t.string() }, t.string(),
  (ctx, args) => runProcedure(() => ctx.withTx(tx => {
    const account = session(tx, args.sessionHash);
    if (!/^[a-f0-9]{128}$/.test(args.passwordHash) || !/^[a-f0-9]{64}$/.test(args.passwordSalt))
      fail('INVALID_ARGUMENT', 'scrypt credential required');
    if (args.passwordHash === account.passwordHash) fail('INVALID_ARGUMENT', 'choose a different password');
    const version = account.version + 1;
    tx.db.adminAccount.email.update({ ...account, version, passwordHash: args.passwordHash, passwordSalt: args.passwordSalt, mustChangePassword: false });
    const current = tx.db.adminSession.tokenHash.find(args.sessionHash)!;
    tx.db.adminSession.tokenHash.update({ ...current, version }); // all other sessions are invalidated
    audit(tx, account.email, 'PASSWORD_CHANGED', 'admin');
    return {};
  }))
);

export const adminOverview = spacetimedb.procedure({ sessionHash: t.string() }, t.string(), (ctx, args) =>
  runProcedure(() => ctx.withTx(tx => {
    const account = session(tx, args.sessionHash);
    if (account.mustChangePassword) return { email: account.email, mustChangePassword: true };
    const invitations = [...tx.db.appInvitation.iter()].sort((a,b) => Number(b.expiresAt.microsSinceUnixEpoch - a.expiresAt.microsSinceUnixEpoch)).slice(0,100).map(i => ({
      hash: i.tokenHash, label: i.label, expiresAt: i.expiresAt.microsSinceUnixEpoch.toString(),
      status: i.revoked ? 'revoked' : i.redeemedBy ? 'used' : i.expiresAt.microsSinceUnixEpoch <= tx.timestamp.microsSinceUnixEpoch ? 'expired' : 'available',
    }));
    const users = [...tx.db.appAccess.iter()].sort((a,b) => Number(b.grantedAt.microsSinceUnixEpoch - a.grantedAt.microsSinceUnixEpoch)).slice(0,100).map(a => ({
      identity: a.identity.toHexString(), name: tx.db.profile.owner.find(a.identity)?.displayName || 'Traveller', revoked: a.revoked,
      grantedAt: a.grantedAt.microsSinceUnixEpoch.toString(),
    }));
    const jobs = [...tx.db.job.iter()];
    const counts: Record<string, number> = {};
    for (const job of jobs) counts[job.status] = (counts[job.status] || 0) + 1;
    const recentJobs = jobs.sort((a,b) => Number(b.updatedAt.microsSinceUnixEpoch - a.updatedAt.microsSinceUnixEpoch)).slice(0,30).map(j => ({
      id: j.id.toString(), kind: j.kind, status: j.status, attempt: j.attempt, updatedAt: j.updatedAt.microsSinceUnixEpoch.toString(),
    }));
    const events = [...tx.db.adminAudit.iter()].sort((a,b) => Number(b.id-a.id)).slice(0,30).map(a => ({
      id: a.id.toString(), email: a.email, action: a.action, at: a.at.microsSinceUnixEpoch.toString(),
    }));
    return { email: account.email, mustChangePassword: false, asOf: tx.timestamp.microsSinceUnixEpoch.toString(), invitations, users, counts, recentJobs, events,
      limits: { invitations: 100, users: 100, jobs: 30 } };
  }))
);
export const adminAction = spacetimedb.procedure(
  { sessionHash: t.string(), action: t.string(), payloadJson: t.string() }, t.string(),
  (ctx, args) => runProcedure(() => ctx.withTx(tx => {
    const account = session(tx, args.sessionHash);
    if (account.mustChangePassword && args.action !== 'logout') fail('PASSWORD_CHANGE_REQUIRED', 'change the temporary password first');
    if (args.payloadJson.length > 2048) fail('INVALID_ARGUMENT', 'request too large');
    const payload = JSON.parse(args.payloadJson);
    switch (args.action) {
      case 'open_app': {
        hash(payload.tokenHash);
        for (const old of tx.db.adminAppHandoff.iter()) {
          if (old.sessionHash === args.sessionHash || old.expiresAt.microsSinceUnixEpoch <= tx.timestamp.microsSinceUnixEpoch)
            tx.db.adminAppHandoff.tokenHash.delete(old.tokenHash);
        }
        tx.db.adminAppHandoff.insert({ tokenHash: payload.tokenHash, sessionHash: args.sessionHash,
          expiresAt: new Timestamp(tx.timestamp.microsSinceUnixEpoch + minute) });
        break;
      }
      case 'create_invitation': {
        hash(payload.tokenHash);
        if (typeof payload.label !== 'string' || !payload.label.trim() || payload.label.length > 100 ||
            !Number.isInteger(payload.hours) || payload.hours < 1 || payload.hours > 168) fail('INVALID_ARGUMENT', 'invalid invitation');
        tx.db.appInvitation.insert({ tokenHash: payload.tokenHash, label: payload.label.trim(),
          expiresAt: new Timestamp(tx.timestamp.microsSinceUnixEpoch + BigInt(payload.hours) * hour),
          redeemedBy: undefined, redeemedAt: undefined, revoked: false });
        audit(tx, account.email, 'INVITATION_CREATED', payload.tokenHash); break;
      }
      case 'revoke_invitation': {
        hash(payload.tokenHash);
        const i = tx.db.appInvitation.tokenHash.find(payload.tokenHash);
        if (!i) fail('NOT_FOUND', 'invitation not found');
        tx.db.appInvitation.tokenHash.update({ ...i, revoked: true });
        if (i.redeemedBy) {
          const a = tx.db.appAccess.identity.find(i.redeemedBy);
          if (a?.invitationHash === i.tokenHash) tx.db.appAccess.identity.update({ ...a, revoked: true });
        }
        audit(tx, account.email, 'INVITATION_REVOKED', payload.tokenHash); break;
      }
      case 'set_user_access': {
        if (typeof payload.identity !== 'string' || !/^(0x)?[a-f0-9]{64}$/i.test(payload.identity) || typeof payload.revoked !== 'boolean')
          fail('INVALID_ARGUMENT', 'invalid user access request');
        const identity = Identity.fromString(payload.identity);
        const access = tx.db.appAccess.identity.find(identity);
        if (!access) fail('NOT_FOUND', 'user not found');
        tx.db.appAccess.identity.update({ ...access, revoked: payload.revoked });
        audit(tx, account.email, payload.revoked ? 'USER_SUSPENDED' : 'USER_RESTORED', identity.toHexString()); break;
      }
      case 'logout': {
        const current = tx.db.adminSession.tokenHash.find(args.sessionHash)!;
        tx.db.adminSession.tokenHash.update({ ...current, revoked: true });
        audit(tx, account.email, 'SIGNED_OUT', 'admin'); break;
      }
      default: fail('NOT_AUTHORIZED', 'this action is not an administrator permission');
    }
    return {};
  }))
);

// Devices redeem a one-minute handoff for the signed-in account. No client-
// supplied identity or email can claim another traveller’s data.
export const redeemAdminAppAccess = spacetimedb.reducer({ token: t.string() }, (ctx, { token }) => {
  if (isWorker(ctx) || ctx.db.adminGateway.identity.find(ctx.sender) || ctx.db.config.id.find(0)?.owner.isEqual(ctx.sender)) fail('NOT_AUTHORIZED', 'participant identity required');
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) fail('INVALID_HANDOFF', 'open the app again from admin');
  const tokenHash = digest(token), handoff = ctx.db.adminAppHandoff.tokenHash.find(tokenHash);
  if (!handoff || handoff.expiresAt.microsSinceUnixEpoch <= ctx.timestamp.microsSinceUnixEpoch)
    fail('INVALID_HANDOFF', 'open the app again from admin');
  const account = validSession(ctx, handoff.sessionHash);
  if (account.mustChangePassword) fail('PASSWORD_CHANGE_REQUIRED', 'change your temporary password first');
  // Resolve an account to its existing traveller without moving or copying data.
  let participant = ctx.db.participantAccount.email.find(account.email);
  if (!participant) {
    const prior = [...ctx.db.appAccess.iter()].filter(a => a.invitationHash === `admin:${account.email}` &&
      !ctx.db.participantAccount.owner.find(a.identity));
    const populated = prior.filter(a => ctx.db.profile.owner.find(a.identity) || [...ctx.db.membership.byMember.filter(a.identity)].length);
    if (populated.length > 1) fail('ACCOUNT_LINK_REQUIRED', 'multiple existing profiles need owner-assisted reconciliation');
    const claimed = ctx.db.participantAccount.owner.find(ctx.sender) || ctx.db.participantDevice.identity.find(ctx.sender);
    const identity = populated[0]?.identity ?? prior[0]?.identity ?? (claimed
      ? Identity.fromString(digest(`tandem-participant:${account.email}:${tokenHash}`)) : ctx.sender);
    participant = ctx.db.participantAccount.insert({ email: account.email, owner: identity });
  }
  const current = ctx.db.appAccess.identity.find(participant.owner);
  if (current?.revoked) fail('ACCESS_SUSPENDED', 'restore this traveller in People before opening the app');
  if (!current) ctx.db.appAccess.insert({ identity: participant.owner, invitationHash: `admin:${account.email}`, grantedAt: ctx.timestamp, revoked: false });
  const previous = ctx.db.participantDevice.identity.find(ctx.sender);
  const device = { identity: ctx.sender, email: account.email, owner: participant.owner, sessionHash: handoff.sessionHash };
  if (previous) ctx.db.participantDevice.identity.update(device); else ctx.db.participantDevice.insert(device);
  ctx.db.adminAppHandoff.tokenHash.delete(tokenHash);
  const session = ctx.db.adminSession.tokenHash.find(handoff.sessionHash)!;
  // Schedule revocation so even idle subscriptions lose access when login expires.
  ctx.db.adminSessionExpiry.insert({ scheduledId: 0n, scheduledAt: ScheduleAt.time(session.expiresAt.microsSinceUnixEpoch), sessionHash: handoff.sessionHash });
  // A newly authenticated WebSocket was not admitted at connect time.
  if (ctx.connectionId) {
    const presence = ctx.db.presence.identity.find(ctx.sender);
    const row = { identity: ctx.sender, connections: Math.max(1, presence?.connections ?? 0), online: true, lastSeen: ctx.timestamp };
    if (presence) ctx.db.presence.identity.update(row); else ctx.db.presence.insert(row);
  }
  audit(ctx, account.email, 'APP_ACCOUNT_LINKED', ctx.sender.toHexString());
});

export const expireAdminAppSession = spacetimedb.reducer(
  { onSchedule: adminSessionExpiry }, { arg: adminSessionExpiry.rowType }, (ctx, { arg }) => {
    if (!ctx.sender.isEqual(ctx.identity)) fail('NOT_AUTHORIZED', 'scheduler required');
    const row = ctx.db.adminSession.tokenHash.find(arg.sessionHash);
    if (row && row.expiresAt.microsSinceUnixEpoch <= ctx.timestamp.microsSinceUnixEpoch)
      ctx.db.adminSession.tokenHash.update({ ...row, revoked: true });
  }
);
