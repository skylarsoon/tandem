import type { Identity } from 'spacetimedb';
import { type Ctx, fail } from './guards';
import { LEGACY_KEYS, RULE_ATTRIBUTES } from './rule_catalog';
import { type Catalog, type LegacyTable, type Rule, RuleError, attributeType, evaluateRule, fromLegacy, validateRule } from './rules';

// A room's rules, needs and search facts: how they're stored, and how a picked
// candidate is checked against them. Nothing here knows any particular limit;
// meanings live in the catalogs. Not exported from the module entry.

export const CATALOG = RULE_ATTRIBUTES as unknown as Catalog & {
  categories: Record<string, Record<string, { type: string; shared_by_setting?: string; trip_bound?: string; unknown_ok?: boolean }>>;
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
  // Only a firm need is stored as one; "if possible" is a preference the planner weighs.
  for (const need of converted.needs.filter(x => x.kind === 'hard')) {
    ctx.db.participantNeed.insert({ id: 0n, roomId, owner, category: need.category, attribute: need.attribute, needs: need.needs,
      legacyKey: key, updatedAt: ctx.timestamp });
  }
  for (const fact of converted.facts) {
    ctx.db.searchFact.insert({ id: 0n, roomId, owner, fact: fact.fact, valueJson: JSON.stringify(fact.value), legacyKey: key, updatedAt: ctx.timestamp });
  }
}

// A proposed patch can be checked before anyone confirms it: a generic rule must
// fit the catalog; an old named limit must parse.
export function checkRule(raw: unknown, ctx?: Ctx, roomId?: bigint, owner?: Identity): void {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) fail('INVALID_RULE', 'a rule is an object');
  try {
    validateRule(raw as Rule, CATALOG);
  } catch (error) {
    if (error instanceof RuleError) fail('INVALID_RULE', error.message);
    throw error;
  }
  if (ctx && roomId !== undefined && owner) checkRelation(ctx, roomId, owner, raw as Rule);
}

// "The same flight as Alex" must name another active member of this room, by their name here.
function checkRelation(ctx: Ctx, roomId: bigint, owner: Identity, rule: Rule): void {
  if (rule.operator !== 'same_as') return;
  const named = [...ctx.db.membership.byRoomMember.filter(roomId)].some(
    m => m.active && !m.member.isEqual(owner) && m.displayName === rule.value);
  if (!named) fail('INVALID_RULE', 'same_as names another member of this room');
}

// An owner's rule in the generic language (entered directly, or a confirmed agent
// suggestion), checked against the attribute catalog. Returns the stored row id.
export function addOwnedRule(ctx: Ctx, roomId: bigint, raw: unknown, quote: string): bigint {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) fail('INVALID_RULE', 'a rule is an object');
  const rule = raw as Rule;
  checkRule(rule, ctx, roomId, ctx.sender);
  const kind = rule.kind ?? 'hard';
  // A later statement about the same thing (same attribute or total, same comparison, must or wish) replaces it.
  for (const r of [...ctx.db.participantRule.byRoom.filter(roomId)]) {
    if (!r.owner.isEqual(ctx.sender) || r.legacyKey !== undefined) continue;
    const before = JSON.parse(r.ruleJson) as Rule;
    if (before.category === rule.category && before.attribute === rule.attribute && before.aggregate === rule.aggregate &&
        before.operator === rule.operator && (before.kind ?? 'hard') === kind) {
      ctx.db.participantRule.id.delete(r.id);
    }
  }
  return ctx.db.participantRule.insert({
    id: 0n,
    roomId,
    owner: ctx.sender,
    ruleJson: JSON.stringify({ category: rule.category, attribute: rule.attribute, aggregate: rule.aggregate,
      operator: rule.operator, value: rule.value, kind, importance: rule.importance, tolerance: rule.tolerance }),
    kind,
    state: 'active',
    sourceQuote: quote,
    legacyKey: undefined,
    updatedAt: ctx.timestamp,
  }).id;
}

// Does this person have a money limit (any rule on a money attribute or total)?
export function hasMoneyRule(ctx: Ctx, roomId: bigint, owner: Identity): boolean {
  return [...ctx.db.participantRule.byRoom.filter(roomId)].some(r => {
    if (!r.owner.isEqual(owner) || !inForce(r.state)) return false;
    try {
      return attributeType(JSON.parse(r.ruleJson) as Rule, CATALOG) === 'money';
    } catch {
      return false;
    }
  });
}

// In force unless set aside: 'kept' is a rule its owner was asked to relax and chose to keep.
function inForce(state: string): boolean {
  return state === 'active' || state === 'kept';
}

// The rules that bind right now: hard, and not set aside.
export function bindingRules(ctx: Ctx, roomId: bigint): OwnedRule[] {
  return [...ctx.db.participantRule.byRoom.filter(roomId)]
    .filter(r => r.kind === 'hard' && inForce(r.state))
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
    // Unknown fails, unless the catalog says this fact is rarely reported (a venue's menu).
    if (verdict === 'UNKNOWN' && !CATALOG.categories[category]?.[rule.attribute!]?.unknown_ok) {
      fail('INVALID_PROPOSAL', `candidate ${ref} doesn't confirm ${rule.attribute}, which a traveller requires`);
    }
  }
}

// The trip's own dates bind every pick: attributes the catalog marks trip_bound stay
// on or before the end (by_end, e.g. arriving) or on or after the start (from_start,
// e.g. leaving for home). A pick without that fact (one-way: no way home) isn't held to it.
export function checkTripBounds(settings: Record<string, unknown>, category: string, facts: Record<string, unknown>, ref: string): void {
  for (const [attribute, spec] of Object.entries(CATALOG.categories[category] ?? {})) {
    const value = spec.trip_bound === 'by_end' ? settings.end_date : spec.trip_bound === 'from_start' ? settings.start_date : undefined;
    if (typeof value !== 'string') continue;
    const rule: Rule = { category, attribute, operator: spec.trip_bound === 'by_end' ? 'lte' : 'gte', value };
    if (evaluateRule(rule, { ...facts, category }, CATALOG) === 'VIOLATED') {
      fail('INVALID_PROPOSAL', `candidate ${ref} falls outside the trip's dates (${attribute})`);
    }
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

type Agreed = { proposal: { id: bigint }; scope: string; selectedRefs: string[] };

// Agreed decisions a confirmed change now breaks: a pick against its people's rules or
// the trip's dates, or someone's whole-trip limit with everything agreed. Budget breaks
// are resolved newest first, so older agreements stand where they still fit.
export function brokenCommitments<C extends Agreed>(ctx: Ctx, roomId: bigint, agreed: C[]): C[] {
  const room = ctx.db.room.id.find(roomId);
  if (!room) return [];
  const settings = JSON.parse(room.settingsJson) as Record<string, unknown>;
  const active = [...ctx.db.membership.byRoomMember.filter(roomId)].filter(m => m.active);
  const rules = bindingRules(ctx, roomId);
  const counted = (c: C) => c.scope !== 'COMPLETE_PLAN' && c.scope !== 'GENERAL_AGREEMENT' && c.scope !== 'LEGACY';
  const picks = (c: C) =>
    c.selectedRefs.flatMap(ref => {
      let row;
      for (const x of ctx.db.candidate.byRoomRef.filter([roomId, ref])) row = x;
      return row ? [{ ref, category: row.category, facts: JSON.parse(row.factsJson) as Record<string, unknown> }] : [];
    });
  const newest = [...agreed].sort((a, b) => (a.proposal.id < b.proposal.id ? 1 : -1));
  const broken: C[] = [];
  for (const c of newest) {
    for (const { ref, category, facts } of picks(c)) {
      const owners = category === 'FLIGHT'
        ? active.filter(m => Number(m.id) === facts.owner_member_id).map(m => m.member)
        : active.map(m => m.member);
      try {
        checkCandidateRules(rules, owners, category, facts, ref);
        checkTripBounds(settings, category, facts, ref);
      } catch {
        broken.push(c);
        break;
      }
    }
  }
  let kept = newest.filter(c => !broken.includes(c));
  const over = () =>
    active.some(m => {
      let own = 0, shared = 0;
      for (const c of kept.filter(counted)) {
        for (const { category, facts } of picks(c)) {
          const total = typeof facts.total_cents === 'number' ? facts.total_cents : 0;
          if (category === 'FLIGHT') own += facts.owner_member_id === Number(m.id) ? total : 0;
          else if (category === 'STAY' || category === 'TRANSFER') shared += total;
          else own += total; // per head
        }
      }
      return !costLimitsHold(rules, m.member, active.length, own, shared);
    });
  while (kept.length > 0 && over()) broken.push(kept.shift()!);
  return broken;
}
