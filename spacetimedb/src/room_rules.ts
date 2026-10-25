import type { Identity } from 'spacetimedb';
import { type Ctx, fail } from './guards';
import { LEGACY_KEYS, RULE_ATTRIBUTES } from './rule_catalog';
import { type Catalog, type LegacyTable, type Rule, RuleError, evaluateRule, fromLegacy } from './rules';

// A room's rules, needs and search facts: how they're stored, and how a picked
// candidate is checked against them. Nothing here knows any particular limit;
// meanings live in the catalogs. Not exported from the module entry.

export const CATALOG = RULE_ATTRIBUTES as unknown as Catalog & {
  categories: Record<string, Record<string, { type: string; shared_by_setting?: string }>>;
};
const LEGACY = LEGACY_KEYS as unknown as LegacyTable;

export type OwnedRule = { owner: Identity; rule: Rule; quote: string };

// A confirmed old named limit, mirrored as rules/needs/facts. Confirming it
// again (or setting it aside) replaces what it produced.
export function mirrorLegacy(
  ctx: Ctx,
  roomId: bigint,
  owner: Identity,
  key: string,
  row: { valueJson: string; hardness: string; authorizedRangeJson?: string; sourceQuote?: string }
): void {
  for (const r of [...ctx.db.participantRule.byRoom.filter(roomId)]) {
    if (r.owner.isEqual(owner) && r.legacyKey === key) ctx.db.participantRule.id.delete(r.id);
  }
  for (const n of [...ctx.db.participantNeed.byRoom.filter(roomId)]) {
    if (n.owner.isEqual(owner) && n.legacyKey === key) ctx.db.participantNeed.id.delete(n.id);
  }
  for (const f of [...ctx.db.searchFact.byRoom.filter(roomId)]) {
    if (f.owner.isEqual(owner) && f.legacyKey === key) ctx.db.searchFact.id.delete(f.id);
  }
  if (key.startsWith(LEGACY.kept_prefix) || !(key in LEGACY.keys)) return;
  let converted;
  try {
    converted = fromLegacy(
      key,
      {
        value: JSON.parse(row.valueJson),
        hardness: row.hardness,
        authorized_range: row.authorizedRangeJson === undefined ? undefined : JSON.parse(row.authorizedRangeJson),
      },
      LEGACY,
      CATALOG
    );
  } catch (error) {
    if (error instanceof RuleError) fail('INVALID_ARGUMENT', `${key}: ${error.message}`);
    throw error;
  }
  for (const rule of converted.rules) {
    ctx.db.participantRule.insert({
      id: 0n,
      roomId,
      owner,
      ruleJson: JSON.stringify(rule),
      kind: rule.kind ?? 'hard',
      state: 'active',
      sourceQuote: row.sourceQuote ?? '',
      legacyKey: key,
      updatedAt: ctx.timestamp,
    });
  }
  for (const need of converted.needs) {
    ctx.db.participantNeed.insert({ id: 0n, roomId, owner, ...need, legacyKey: key, updatedAt: ctx.timestamp });
  }
  for (const fact of converted.facts) {
    ctx.db.searchFact.insert({ id: 0n, roomId, owner, fact: fact.fact, valueJson: JSON.stringify(fact.value), legacyKey: key, updatedAt: ctx.timestamp });
  }
}

// The rules that bind right now: hard, and not set aside.
export function bindingRules(ctx: Ctx, roomId: bigint): OwnedRule[] {
  return [...ctx.db.participantRule.byRoom.filter(roomId)]
    .filter(r => r.kind === 'hard' && r.state === 'active')
    .map(r => ({ owner: r.owner, rule: JSON.parse(r.ruleJson) as Rule, quote: r.sourceQuote }));
}

// A picked candidate against the rules of the people it's for: their own pick
// (a flight) or everyone's (a shared pick). Unknown is not OK at publication.
export function checkCandidateRules(rules: OwnedRule[], owners: Identity[], category: string, facts: Record<string, unknown>, ref: string): void {
  for (const { owner, rule } of rules) {
    if (rule.aggregate !== undefined || rule.category !== category || !owners.some(o => o.isEqual(owner))) continue;
    let verdict;
    try {
      verdict = evaluateRule(rule, { ...facts, category }, CATALOG);
    } catch (error) {
      if (error instanceof RuleError) fail('INVALID_PROPOSAL', `candidate ${ref}: ${error.message}`);
      throw error;
    }
    if (verdict === 'VIOLATED') fail('INVALID_PROPOSAL', `candidate ${ref} breaks a traveller's confirmed ${rule.attribute} limit`);
    if (verdict === 'UNKNOWN') fail('INVALID_PROPOSAL', `candidate ${ref} doesn't confirm ${rule.attribute}, which a traveller requires`);
  }
}

// A shared pick must hold everyone's needs: explicit ones (my own bedroom) plus,
// for attributes shared by a room setting, everyone else's share, rounded up.
export function checkNeeds(
  ctx: Ctx,
  roomId: bigint,
  members: Identity[],
  settings: Record<string, unknown>,
  category: string,
  facts: Record<string, unknown>,
  ref: string
): void {
  const needs = [...ctx.db.participantNeed.byRoom.filter(roomId)].filter(
    n => n.category === category && members.some(m => m.isEqual(n.owner))
  );
  // Explicit needs, plus anything the room shares by a setting (people per bedroom),
  // which everyone needs even if nobody asked for their own.
  const attributes = new Set(needs.map(n => n.attribute));
  for (const [attribute, spec] of Object.entries(CATALOG.categories[category] ?? {})) {
    if (spec.shared_by_setting) attributes.add(attribute);
  }
  for (const attribute of attributes) {
    const explicit = needs.filter(n => n.attribute === attribute);
    const setting = CATALOG.categories[category]?.[attribute]?.shared_by_setting;
    const per = setting && typeof settings[setting] === 'number' ? (settings[setting] as number) : setting ? 2 : 0;
    const others = members.filter(m => !explicit.some(n => n.owner.isEqual(m))).length;
    const required = Math.ceil(explicit.reduce((sum, n) => sum + n.needs, 0) + (per > 0 ? others / per : 0) - 1e-9);
    const have = facts[attribute];
    if (typeof have !== 'number') fail('INVALID_PROPOSAL', `candidate ${ref} doesn't confirm ${attribute}, which the group needs`);
    if (have < required) fail('INVALID_PROPOSAL', `candidate ${ref} has too few ${attribute} for the group's needs`);
  }
}

// Limits on a person's whole-trip cost (aggregate my_cost), compared in
// party-size-scaled cents: n × own + shared OP n × limit, exact.
export function costLimitsHold(rules: OwnedRule[], owner: Identity, n: number, own: number, shared: number): boolean {
  for (const { owner: o, rule } of rules) {
    if (rule.aggregate !== 'my_cost' || !o.isEqual(owner)) continue;
    const total = n * own + shared;
    const limit = n * (rule.value as number);
    const ok =
      rule.operator === 'lte' ? total <= limit
      : rule.operator === 'lt' ? total < limit
      : rule.operator === 'gte' ? total >= limit
      : rule.operator === 'gt' ? total > limit
      : rule.operator === 'eq' ? total === limit
      : true;
    if (!ok) return false;
  }
  return true;
}
