import { t } from 'spacetimedb/server';
import spacetimedb from './schema';
import { emitEvent, fail, invalidateRoom, nonEmpty, parseJson, requireHuman, requireMember } from './guards';
import { addOwnedRule } from './room_rules';
import { applyPatch, messageOwnAgent, upsertConstraint } from './effects';

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
  applyPatch(ctx, patch.roomId, patch.patchJson);
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

// A rule in the generic language, entered by its owner (a form, or a confirmed
// agent suggestion): {category, attribute | aggregate, operator, value, kind?}.
// Checked against the attribute catalog; a change replans the room.
export const addRule = spacetimedb.reducer(
  { roomId: t.u64(), ruleJson: t.string(), sourceQuote: t.option(t.string()) },
  (ctx, { roomId, ruleJson, sourceQuote }) => {
    requireHuman(ctx);
    requireMember(ctx, roomId);
    addOwnedRule(ctx, roomId, parseJson(ruleJson, 'ruleJson'), sourceQuote ?? '');
    invalidateRoom(ctx, roomId, { input: true }, 'rule added');
    emitEvent(ctx, roomId, 'REQUIREMENTS_UPDATED', { message: 'A participant updated their requirements' });
  }
);

export const removeRule = spacetimedb.reducer({ ruleId: t.u64() }, (ctx, { ruleId }) => {
  requireHuman(ctx);
  const rule = ctx.db.participantRule.id.find(ruleId);
  if (!rule || !rule.owner.isEqual(ctx.sender)) fail('NOT_FOUND', `rule ${ruleId}`);
  if (rule.legacyKey !== undefined) fail('INVALID_STATE', 'change this one through the limit it came from');
  ctx.db.participantRule.id.delete(rule.id);
  invalidateRoom(ctx, rule.roomId, { input: true }, 'rule removed');
  emitEvent(ctx, rule.roomId, 'REQUIREMENTS_UPDATED', { message: 'A participant updated their requirements' });
});

// Set one of your rules aside for this trip, keep it ("I need it", never asked
// again), or bring it back. Rules mirrored from a named limit change through it.
export const setRuleState = spacetimedb.reducer({ ruleId: t.u64(), state: t.string() }, (ctx, { ruleId, state }) => {
  requireHuman(ctx);
  if (!['active', 'set_aside', 'kept'].includes(state)) fail('INVALID_ARGUMENT', 'state is active, set_aside or kept');
  const rule = ctx.db.participantRule.id.find(ruleId);
  if (!rule || !rule.owner.isEqual(ctx.sender)) fail('NOT_FOUND', `rule ${ruleId}`);
  if (rule.legacyKey !== undefined) fail('INVALID_STATE', 'change this one through the limit it came from');
  if (rule.state === state) return;
  ctx.db.participantRule.id.update({ ...rule, state, updatedAt: ctx.timestamp });
  // Only binding changes replan: keeping an active rule changes nothing for planning.
  if (state === 'set_aside' || rule.state === 'set_aside') invalidateRoom(ctx, rule.roomId, { input: true }, 'rule state changed');
});
