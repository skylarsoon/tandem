import type { Identity } from 'spacetimedb';
import type { InferSchema, ViewCtx } from 'spacetimedb/server';
import type spacetimedb from './schema';

type AccessDb = Pick<ViewCtx<InferSchema<typeof spacetimedb>>['db'], 'appAccess' | 'config'>;
export function hasAppAccess(db: AccessDb, identity: Identity): boolean {
  if (db.config.id.find(0)?.owner.isEqual(identity)) return true;
  const access = db.appAccess.identity.find(identity);
  return !!access && !access.revoked;
}
