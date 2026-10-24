import { t } from 'spacetimedb/server';
import spacetimedb from './schema';
import { fail, nonEmpty, requireHuman, requireMember } from './guards';
import { constraintFromPatch, messageOwnAgent, upsertConstraint } from './effects';

// Direct form entry counts as owner confirmation.
export const setConstraint = spacetimedb.reducer(
  {
    roomId: t.u64(),
    key: t.string(),
    valueJson: t.string(),
    unit: t.string(),
    hardness: t.string(),
    authorizedRangeJson: t.option(t.string()),
    privateRationale: t.option(t.string()),
  },
  (ctx, args) => {
    requireHuman(ctx);
    requireMember(ctx, args.roomId);
    upsertConstraint(ctx, args.roomId, args);
  }
);

export const confirmConstraintPatch = spacetimedb.reducer({ patchId: t.u64() }, (ctx, { patchId }) => {
  requireHuman(ctx);
  const patch = ctx.db.constraintPatch.id.find(patchId);
  if (!patch || !patch.owner.isEqual(ctx.sender)) fail('NOT_FOUND', `patch ${patchId}`);
  if (patch.state !== 'PENDING') fail('INVALID_STATE', `patch ${patchId} is ${patch.state}`);
  requireMember(ctx, patch.roomId);
  ctx.db.constraintPatch.id.update({ ...patch, state: 'CONFIRMED' });
  upsertConstraint(ctx, patch.roomId, constraintFromPatch(patch.patchJson));
});

export const rejectConstraintPatch = spacetimedb.reducer({ patchId: t.u64() }, (ctx, { patchId }) => {
  requireHuman(ctx);
  const patch = ctx.db.constraintPatch.id.find(patchId);
  if (!patch || !patch.owner.isEqual(ctx.sender)) fail('NOT_FOUND', `patch ${patchId}`);
  if (patch.state !== 'PENDING') fail('INVALID_STATE', `patch ${patchId} is ${patch.state}`);
  ctx.db.constraintPatch.id.update({ ...patch, state: 'REJECTED' });
});

export const submitPrivateMessage = spacetimedb.reducer(
  { roomId: t.u64(), body: t.string() },
  (ctx, { roomId, body }) => {
    requireHuman(ctx);
    requireMember(ctx, roomId);
    messageOwnAgent(ctx, roomId, nonEmpty(body, 'body'));
  }
);
