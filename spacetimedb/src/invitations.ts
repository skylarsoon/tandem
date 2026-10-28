import { Timestamp } from 'spacetimedb';
import { t } from 'spacetimedb/server';
import spacetimedb from './schema';
import { type Ctx, digest, fail, isWorker } from './guards';
import { hasAppAccess } from './access';

function requireAdmin(ctx: Ctx) {
  if (!ctx.db.config.id.find(0)?.owner.isEqual(ctx.sender)) fail('NOT_AUTHORIZED', 'only the database owner can manage app invitations');
}

// The admin generates 32 random bytes outside the module and submits only SHA-256.
export const createAppInvitation = spacetimedb.reducer(
  { tokenHash: t.string(), label: t.string(), expiresInSeconds: t.u32() }, (ctx, args) => {
    requireAdmin(ctx);
    if (!/^[a-f0-9]{64}$/.test(args.tokenHash) || args.label.length > 100 || args.expiresInSeconds < 60 || args.expiresInSeconds > 604800)
      fail('INVALID_ARGUMENT', 'use a SHA-256 digest and an expiry between one minute and seven days');
    if (ctx.db.appInvitation.tokenHash.find(args.tokenHash)) fail('INVALID_ARGUMENT', 'invitation already exists');
    ctx.db.appInvitation.insert({ tokenHash: args.tokenHash, label: args.label,
      expiresAt: new Timestamp(ctx.timestamp.microsSinceUnixEpoch + BigInt(args.expiresInSeconds) * 1_000_000n),
      redeemedBy: undefined, redeemedAt: undefined, revoked: false });
  }
);

export const redeemAppInvitation = spacetimedb.reducer({ token: t.string() }, (ctx, { token }) => {
  if (isWorker(ctx)) fail('NOT_AUTHORIZED', 'workers cannot redeem invitations');
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) fail('INVITATION_INVALID', 'this invitation is invalid, expired, or already used');
  const invitation = ctx.db.appInvitation.tokenHash.find(digest(token));
  if (!invitation || invitation.revoked) fail('INVITATION_INVALID', 'this invitation is invalid, expired, or already used');
  // A response lost in transit can be retried by the same admitted browser.
  if (invitation.redeemedBy?.isEqual(ctx.sender) && hasAppAccess(ctx.db, ctx.sender)) return;
  if (invitation.redeemedBy || invitation.expiresAt.microsSinceUnixEpoch <= ctx.timestamp.microsSinceUnixEpoch)
    fail('INVITATION_INVALID', 'this invitation is invalid, expired, or already used');
  if (hasAppAccess(ctx.db, ctx.sender)) return; // Do not consume an invitation for someone already admitted.
  const row = { identity: ctx.sender, invitationHash: invitation.tokenHash, grantedAt: ctx.timestamp, revoked: false };
  if (ctx.db.appAccess.identity.find(ctx.sender)) ctx.db.appAccess.identity.update(row);
  else ctx.db.appAccess.insert(row);
  ctx.db.appInvitation.tokenHash.update({ ...invitation, redeemedBy: ctx.sender, redeemedAt: ctx.timestamp });
});

// Revoking a redeemed invitation also removes its recipient's current access.
export const revokeAppInvitation = spacetimedb.reducer({ tokenHash: t.string() }, (ctx, { tokenHash }) => {
  requireAdmin(ctx);
  const invitation = ctx.db.appInvitation.tokenHash.find(tokenHash);
  if (!invitation) fail('NOT_FOUND', 'invitation not found');
  ctx.db.appInvitation.tokenHash.update({ ...invitation, revoked: true });
  if (invitation.redeemedBy) {
    const access = ctx.db.appAccess.identity.find(invitation.redeemedBy);
    if (access?.invitationHash === tokenHash) ctx.db.appAccess.identity.update({ ...access, revoked: true });
  }
});

const AppAccessStatus = t.row('AppAccessStatus', { allowed: t.bool() });
export const myAppAccess = spacetimedb.view({ name: 'my_app_access', public: true }, t.array(AppAccessStatus), ctx =>
  [{ allowed: hasAppAccess(ctx.db, ctx.sender) }]
);
