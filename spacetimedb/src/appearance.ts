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
  const row = { owner: ctx.sender, variant };
  if (ctx.db.agentAppearance.owner.find(ctx.sender)) ctx.db.agentAppearance.owner.update(row);
  else ctx.db.agentAppearance.insert(row);
});

export const myAgentAppearance = spacetimedb.view(
  { name: 'my_agent_appearance', public: true }, t.option(agentAppearance.rowType),
  ctx => ctx.db.agentAppearance.owner.find(ctx.sender) ?? { owner: ctx.sender, variant: defaultAgentVariant(ctx.sender) }
);
