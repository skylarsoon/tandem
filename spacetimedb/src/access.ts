import type { Identity } from 'spacetimedb';
import type { InferSchema, ViewCtx } from 'spacetimedb/server';
import type spacetimedb from './schema';

type AccessDb = Pick<ViewCtx<InferSchema<typeof spacetimedb>>['db'], 'appAccess' | 'config' | 'adminAccount' | 'adminSession' | 'participantAccount' | 'participantDevice'>;

function deviceAccount(db: AccessDb, identity: Identity) {
  const device = db.participantDevice.identity.find(identity);
  if (!device) return undefined;
  const session = db.adminSession.tokenHash.find(device.sessionHash);
  const account = db.adminAccount.email.find(device.email);
  const participant = db.participantAccount.email.find(device.email);
  return session && !session.revoked && session.email === device.email && account?.enabled &&
    !account.mustChangePassword && account.version === session.version && participant?.owner.isEqual(device.owner)
    ? device : undefined;
}
/** A device may address only the traveller bound to its current authenticated session. */
export function participantIdentity(db: AccessDb, identity: Identity): Identity {
  return deviceAccount(db, identity)?.owner ?? identity;
}

export function hasAppAccess(db: AccessDb, identity: Identity, now?: bigint): boolean {
  if (db.config.id.find(0)?.owner.isEqual(identity)) return true;
  const linked = db.participantDevice.identity.find(identity);
  const valid = deviceAccount(db, identity);
  // No fallback to anonymous access after account sign-out or revocation.
  if ((linked || db.participantAccount.owner.find(identity)) && !valid) return false;
  if (valid && now !== undefined && db.adminSession.tokenHash.find(valid.sessionHash)!.expiresAt.microsSinceUnixEpoch <= now) return false;
  const access = db.appAccess.identity.find(valid?.owner ?? identity);
  if (!access || access.revoked) return false;
  // Admin app grants are ordinary participant access, never admin privileges.
  if (access.invitationHash.startsWith('admin:')) {
    const account = db.adminAccount.email.find(access.invitationHash.slice(6));
    return !!account?.enabled && !account.mustChangePassword;
  }
  return true;
}
