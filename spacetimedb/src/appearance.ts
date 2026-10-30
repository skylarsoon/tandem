import { hasAppAccess, participantIdentity } from './access';
import { t } from 'spacetimedb/server';
import type { Identity } from 'spacetimedb';
import spacetimedb, { agentAppearance } from './schema';
import { oneOf, requireHuman } from './guards';

export const CHARACTER_VARIANTS = ['lavender', 'peach', 'mint', 'yellow'] as const;

export function defaultAgentVariant(owner: Identity): string {
  return CHARACTER_VARIANTS[Number.parseInt(owner.toHexString().slice(-2), 16) % CHARACTER_VARIANTS.length];
}

export const setAgentAppearance = spacetimedb.reducer({ variant: t.string() }, (ctx, { variant }) => {
  requireHuman(ctx);
  oneOf(variant, CHARACTER_VARIANTS, 'variant');
  const row = { owner: participantIdentity(ctx.db, ctx.sender), variant };
  if (ctx.db.agentAppearance.owner.find(participantIdentity(ctx.db, ctx.sender))) ctx.db.agentAppearance.owner.update(row);
  else ctx.db.agentAppearance.insert(row);
});

export const myAgentAppearance = spacetimedb.view(
  { name: 'my_agent_appearance', public: true }, t.option(agentAppearance.rowType),
  ctx => !hasAppAccess(ctx.db, ctx.sender) ? undefined : ctx.db.agentAppearance.owner.find(participantIdentity(ctx.db, ctx.sender)) ?? { owner: participantIdentity(ctx.db, ctx.sender), variant: defaultAgentVariant(participantIdentity(ctx.db, ctx.sender)) }
);
