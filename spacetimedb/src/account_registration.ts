import { Identity, Timestamp } from 'spacetimedb';
import { t } from 'spacetimedb/server';
import spacetimedb from './schema';
import { digest, fail, runProcedure } from './guards';
import { hasAppAccess } from './access';
import { gateway, hash, email, credential, requireParticipant, validSession, startSession, bindDevice, minute } from './account_helpers';

export const prepareTravellerRegistration = spacetimedb.procedure({
  email: t.string(), passwordHash: t.string(), passwordSalt: t.string(), invitationHash: t.string(), tokenHash: t.string(), sessionHash: t.string(),
}, t.string(), (ctx, args) => runProcedure(() => ctx.withTx(tx => {
  gateway(tx); const address = email(args.email); credential(args.passwordHash, args.passwordSalt); hash(args.tokenHash); hash(args.sessionHash);
  if (tx.db.adminAccount.email.find(address) || tx.db.travellerCredential.email.find(address) || tx.db.participantAccount.email.find(address))
    fail('EMAIL_IN_USE', 'use the existing account sign-in');
  if (args.invitationHash) {
    hash(args.invitationHash);
    const invitation = tx.db.appInvitation.tokenHash.find(args.invitationHash);
    if (!invitation || invitation.revoked || (!invitation.redeemedBy && invitation.expiresAt.microsSinceUnixEpoch <= tx.timestamp.microsSinceUnixEpoch))
      fail('INVITATION_INVALID', 'invitation is unavailable');
  }
  for (const old of tx.db.travellerRegistration.iter()) if (old.expiresAt.microsSinceUnixEpoch <= tx.timestamp.microsSinceUnixEpoch) tx.db.travellerRegistration.tokenHash.delete(old.tokenHash);
  tx.db.travellerRegistration.insert({ ...args, email: address, expiresAt: new Timestamp(tx.timestamp.microsSinceUnixEpoch + 5n * minute), completedBy: undefined });
  return {};
})));

// All changes commit together. Failed or competing redemptions cannot consume
// the invite or leave a partial account. Retry is bound to the original device.
export const completeTravellerRegistration = spacetimedb.reducer({ token: t.string() }, (ctx, { token }) => {
  requireParticipant(ctx);
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) fail('INVALID_HANDOFF', 'start registration again');
  const pending = ctx.db.travellerRegistration.tokenHash.find(digest(token));
  if (!pending || pending.expiresAt.microsSinceUnixEpoch <= ctx.timestamp.microsSinceUnixEpoch) fail('INVALID_HANDOFF', 'start registration again');
  if (pending.completedBy) {
    if (!pending.completedBy.isEqual(ctx.sender)) fail('INVALID_HANDOFF', 'registration was already completed');
    validSession(ctx, pending.sessionHash); return;
  }
  if (ctx.db.adminAccount.email.find(pending.email) || ctx.db.travellerCredential.email.find(pending.email) || ctx.db.participantAccount.email.find(pending.email))
    fail('EMAIL_IN_USE', 'use the existing account sign-in');
  const claimed = ctx.db.participantAccount.owner.find(ctx.sender) || ctx.db.participantDevice.identity.find(ctx.sender);
  const legacy = !claimed && hasAppAccess(ctx.db, ctx.sender) ? ctx.db.appAccess.identity.find(ctx.sender) : undefined;
  if (legacy?.invitationHash.startsWith('admin:')) fail('NOT_AUTHORIZED', 'use your administrator account');
  const invitationHash = pending.invitationHash || legacy?.invitationHash;
  const invitation = invitationHash ? ctx.db.appInvitation.tokenHash.find(invitationHash) : undefined;
  const upgrading = legacy && invitation?.redeemedBy?.isEqual(ctx.sender) && legacy.invitationHash === invitation.tokenHash;
  if (!invitation || invitation.revoked || (!upgrading && (invitation.redeemedBy || invitation.expiresAt.microsSinceUnixEpoch <= ctx.timestamp.microsSinceUnixEpoch)))
    fail('INVITATION_INVALID', 'a valid unused invitation is required');
  const owner = claimed ? Identity.fromString(digest(`traveller:${pending.email}:${pending.tokenHash}`)) : ctx.sender;
  const account = ctx.db.travellerCredential.insert({ email: pending.email, enabled: true, version: 1, createdAt: ctx.timestamp,
    passwordHash: pending.passwordHash, passwordSalt: pending.passwordSalt, mustChangePassword: false });
  ctx.db.participantAccount.insert({ email: pending.email, owner });
  const access = { identity: owner, invitationHash: invitation.tokenHash, grantedAt: legacy?.grantedAt ?? ctx.timestamp, revoked: false };
  if (ctx.db.appAccess.identity.find(owner)) ctx.db.appAccess.identity.update(access); else ctx.db.appAccess.insert(access);
  ctx.db.appInvitation.tokenHash.update({ ...invitation, redeemedBy: owner, redeemedAt: invitation.redeemedAt ?? ctx.timestamp });
  startSession(ctx, account, pending.sessionHash);
  bindDevice(ctx, pending.email, owner, pending.sessionHash);
  ctx.db.travellerRegistration.tokenHash.update({ ...pending, passwordHash: '', passwordSalt: '', completedBy: ctx.sender });
});
