import { Timestamp } from 'spacetimedb';
import { t } from 'spacetimedb/server';
import spacetimedb, { travellerSessionExpiry } from './schema';
import { digest, fail, runProcedure } from './guards';
import { gateway, hash, email, credential, requireParticipant, validSession, startSession, bindDevice, minute } from './account_helpers';

// Separate rate-limit keys keep traveller traffic from consuming admin quotas.
export const travellerLoginCredential = spacetimedb.procedure({ email: t.string(), clientHash: t.string() }, t.string(),
  (ctx, args) => runProcedure(() => ctx.withTx(tx => {
    gateway(tx); hash(args.clientHash); const address = email(args.email), now = tx.timestamp.microsSinceUnixEpoch;
    for (const old of tx.db.adminLoginLimit.iter()) if (now - old.windowStart.microsSinceUnixEpoch >= 15n * minute) tx.db.adminLoginLimit.key.delete(old.key);
    const limits: [string, number][] = [['traveller:global', 120], [`traveller:ip:${args.clientHash}`, 20], [`traveller:email:${digest(address)}`, 10]];
    for (const [key, cap] of limits) if ((tx.db.adminLoginLimit.key.find(key)?.count ?? 0) >= cap) return { limited: true };
    for (const [key] of limits) {
      const row = tx.db.adminLoginLimit.key.find(key);
      if (row) tx.db.adminLoginLimit.key.update({ ...row, count: row.count + 1 });
      else tx.db.adminLoginLimit.insert({ key, windowStart: tx.timestamp, count: 1 });
    }
    const a = tx.db.travellerCredential.email.find(address);
    return { limited: false, occupied: !!a || !!tx.db.adminAccount.email.find(address), credential: a?.enabled ? {
      email: address, version: a.version, passwordHash: a.passwordHash, passwordSalt: a.passwordSalt,
    } : null };
  }))
);
export const createTravellerSession = spacetimedb.procedure({ email: t.string(), version: t.u32(), sessionHash: t.string() }, t.string(),
  (ctx, args) => runProcedure(() => ctx.withTx(tx => {
    gateway(tx); const account = tx.db.travellerCredential.email.find(email(args.email));
    if (!account?.enabled || account.version !== args.version) fail('NOT_AUTHORIZED', 'sign in again');
    for (const old of tx.db.travellerSession.iter()) if (old.expiresAt.microsSinceUnixEpoch <= tx.timestamp.microsSinceUnixEpoch) tx.db.travellerSession.tokenHash.delete(old.tokenHash);
    startSession(tx, account, args.sessionHash);
    return { email: account.email, mustChangePassword: account.mustChangePassword };
  }))
);
export const travellerAccountStatus = spacetimedb.procedure({ sessionHash: t.string() }, t.string(),
  (ctx, args) => runProcedure(() => ctx.withTx(tx => {
    gateway(tx); const { account } = validSession(tx, args.sessionHash);
    return { email: account.email, mustChangePassword: account.mustChangePassword };
  }))
);
export const travellerPasswordCredential = spacetimedb.procedure({ sessionHash: t.string() }, t.string(),
  (ctx, args) => runProcedure(() => ctx.withTx(tx => {
    gateway(tx); const { account } = validSession(tx, args.sessionHash);
    return { email: account.email, passwordHash: account.passwordHash, passwordSalt: account.passwordSalt, mustChangePassword: account.mustChangePassword };
  }))
);
export const changeTravellerPassword = spacetimedb.procedure({ sessionHash: t.string(), passwordHash: t.string(), passwordSalt: t.string() }, t.string(),
  (ctx, args) => runProcedure(() => ctx.withTx(tx => {
    gateway(tx); credential(args.passwordHash, args.passwordSalt); const { account, session } = validSession(tx, args.sessionHash);
    const version = account.version + 1;
    tx.db.travellerCredential.email.update({ ...account, version, passwordHash: args.passwordHash, passwordSalt: args.passwordSalt, mustChangePassword: false });
    tx.db.travellerSession.tokenHash.update({ ...session, version });
    return {};
  }))
);
export const travellerAccountAction = spacetimedb.procedure({ sessionHash: t.string(), action: t.string(), tokenHash: t.string() }, t.string(),
  (ctx, args) => runProcedure(() => ctx.withTx(tx => {
    gateway(tx); const { account, session } = validSession(tx, args.sessionHash);
    if (args.action === 'logout') { tx.db.travellerSession.tokenHash.update({ ...session, revoked: true }); return {}; }
    if (args.action !== 'open_app') fail('NOT_AUTHORIZED', 'unsupported traveller action');
    if (account.mustChangePassword) fail('PASSWORD_CHANGE_REQUIRED', 'change the temporary password first');
    const participant = tx.db.participantAccount.email.find(account.email);
    if (!participant || tx.db.appAccess.identity.find(participant.owner)?.revoked !== false) fail('ACCESS_SUSPENDED', 'contact an administrator');
    hash(args.tokenHash);
    for (const old of tx.db.travellerHandoff.iter()) if (old.expiresAt.microsSinceUnixEpoch <= tx.timestamp.microsSinceUnixEpoch) tx.db.travellerHandoff.tokenHash.delete(old.tokenHash);
    tx.db.travellerHandoff.insert({ tokenHash: args.tokenHash, sessionHash: args.sessionHash, expiresAt: new Timestamp(tx.timestamp.microsSinceUnixEpoch + minute) });
    return {};
  }))
);
export const redeemTravellerAccess = spacetimedb.reducer({ token: t.string() }, (ctx, { token }) => {
  requireParticipant(ctx);
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) fail('INVALID_HANDOFF', 'open the app again');
  const key = digest(token), handoff = ctx.db.travellerHandoff.tokenHash.find(key);
  if (!handoff || handoff.expiresAt.microsSinceUnixEpoch <= ctx.timestamp.microsSinceUnixEpoch) fail('INVALID_HANDOFF', 'open the app again');
  const { account } = validSession(ctx, handoff.sessionHash);
  if (account.mustChangePassword) fail('PASSWORD_CHANGE_REQUIRED', 'change your password first');
  const participant = ctx.db.participantAccount.email.find(account.email);
  if (!participant || ctx.db.appAccess.identity.find(participant.owner)?.revoked !== false) fail('ACCESS_SUSPENDED', 'contact an administrator');
  bindDevice(ctx, account.email, participant.owner, handoff.sessionHash);
  ctx.db.travellerHandoff.tokenHash.delete(key);
});
export const expireTravellerSession = spacetimedb.reducer({ onSchedule: travellerSessionExpiry }, { arg: travellerSessionExpiry.rowType }, (ctx, { arg }) => {
  if (!ctx.sender.isEqual(ctx.identity)) fail('NOT_AUTHORIZED', 'scheduler required');
  const session = ctx.db.travellerSession.tokenHash.find(arg.sessionHash);
  if (session && session.expiresAt.microsSinceUnixEpoch <= ctx.timestamp.microsSinceUnixEpoch)
    ctx.db.travellerSession.tokenHash.update({ ...session, revoked: true });
});
export const resetTravellerPassword = spacetimedb.reducer({ email: t.string(), passwordHash: t.string(), passwordSalt: t.string() }, (ctx, args) => {
  if (!ctx.db.config.id.find(0)?.owner.isEqual(ctx.sender)) fail('NOT_AUTHORIZED', 'database owner required');
  credential(args.passwordHash, args.passwordSalt);
  const account = ctx.db.travellerCredential.email.find(email(args.email));
  if (!account) fail('NOT_FOUND', 'account not found');
  ctx.db.travellerCredential.email.update({ ...account, passwordHash: args.passwordHash, passwordSalt: args.passwordSalt, version: account.version + 1, mustChangePassword: true });
});
