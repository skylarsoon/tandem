import type { Identity } from 'spacetimedb';
import type { InferSchema, ViewCtx } from 'spacetimedb/server';
import type spacetimedb from './schema';

type AccessDb = Pick<ViewCtx<InferSchema<typeof spacetimedb>>['db'], 'appAccess' | 'config' | 'adminAccount'>;
export function hasAppAccess(db: AccessDb, identity: Identity): boolean {
  if (db.config.id.find(0)?.owner.isEqual(identity)) return true;
  const access = db.appAccess.identity.find(identity);
  if (!access || access.revoked) return false;
  // Admin app grants are ordinary participant access, never admin privileges.
  if (access.invitationHash.startsWith('admin:')) {
    const account = db.adminAccount.email.find(access.invitationHash.slice(6));
    return !!account?.enabled && !account.mustChangePassword;
  }
  return true;
}
