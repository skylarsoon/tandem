// Rules: what a person needs, in one generic language. The twin of
// worker/src/tandem_worker/rules.py: same operators, same answers, both checked
// against contracts/rule_cases.json. Self-contained (the attribute catalog is
// passed in) so it runs as-is under Node for that check. Not a module export.

export type Rule = {
  category?: string;
  attribute?: string;
  aggregate?: string;
  operator: string;
  value: unknown;
  kind?: string;
  importance?: number; // soft rules only: 1-5, how much it matters to them
  tolerance?: { limit: unknown }; // soft rules only: partial credit up to the limit
};
export type Catalog = {
  categories: Record<string, Record<string, { type: string }>>;
  aggregates: Record<string, { type: string }>;
};
export type Verdict = 'SATISFIED' | 'VIOLATED' | 'UNKNOWN' | 'NOT_APPLICABLE';

const OPERATORS = ['eq', 'neq', 'lt', 'lte', 'gt', 'gte', 'in', 'not_in', 'between', 'lowest', 'highest', 'same_as'];
// A preference for the cheapest, shortest, earliest...: a ranking among the options found,
// never a check on one option, so only ever soft.
const RANKINGS = ['lowest', 'highest'];
// Wanting the same pick as another member (value: their name): about two people's choices,
// so never a check on one option, and only ever a wish (no one can require another's choice).
const RELATIONS = ['same_as'];
const ORDERED = ['money', 'int', 'number', 'epoch', 'time', 'date'];
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export class RuleError extends Error {
  code = 'INVALID_RULE';
}

function bad(detail: string): never {
  throw new RuleError(detail);
}

export function attributeType(rule: Rule, catalog: Catalog): string {
  const found =
    rule.aggregate !== undefined
      ? catalog.aggregates[rule.aggregate]
      : catalog.categories[rule.category ?? '']?.[rule.attribute ?? ''];
  return found?.type ?? bad(`nothing carries ${rule.category}.${rule.attribute ?? rule.aggregate}`);
}

// A value of an attribute's type, in comparable form (times as minutes, dates as days).
function scalar(value: unknown, kind: string): number | boolean | string {
  if (kind === 'date') {
    if (typeof value === 'string' && DATE.test(value)) {
      const [y, m, d] = value.split('-').map(Number);
      const ms = Date.UTC(y, m - 1, d);
      if (new Date(ms).toISOString().slice(0, 10) === value) return ms / 86_400_000;
    }
    bad(`${JSON.stringify(value)} isn't a date (YYYY-MM-DD)`);
  }
  if (kind === 'time') {
    if (typeof value !== 'string' || !TIME.test(value)) bad(`${JSON.stringify(value)} isn't a time (HH:MM)`);
    const [h, m] = value.split(':');
    return Number(h) * 60 + Number(m);
  }
  if (kind === 'bool') return typeof value === 'boolean' ? value : bad(`${JSON.stringify(value)} isn't true or false`);
  if (kind === 'text') return typeof value === 'string' ? value : bad(`${JSON.stringify(value)} isn't text`);
  if (typeof value !== 'number' || !Number.isFinite(value)) bad(`${JSON.stringify(value)} isn't a number`);
  if ((kind === 'money' || kind === 'int' || kind === 'epoch') && !Number.isInteger(value)) {
    bad(`${value} isn't a whole number`);
  }
  return value;
}

// The rule can be checked: its attribute exists and its value fits the operator.
export function validateRule(rule: Rule, catalog: Catalog): void {
  if ((rule.aggregate === undefined) === (rule.attribute === undefined)) bad('a rule names one attribute or one aggregate');
  if (!OPERATORS.includes(rule.operator)) bad(`unknown operator ${rule.operator}`);
  if (rule.kind !== undefined && rule.kind !== 'hard' && rule.kind !== 'soft') bad(`unknown kind ${rule.kind}`);
  const kind = attributeType(rule, catalog);
  const op = rule.operator;
  if (RANKINGS.includes(op)) {
    if (rule.kind !== 'soft' || !ORDERED.includes(kind) || (rule.value !== undefined && rule.value !== null) || rule.tolerance !== undefined) {
      bad(`${op} is a preference on an ordered attribute, with no value`);
    }
    checkWeighting(rule, kind);
    return;
  }
  if (RELATIONS.includes(op)) {
    if (rule.kind !== 'soft' || rule.aggregate !== undefined || typeof rule.value !== 'string' || !rule.value.trim() ||
        rule.tolerance !== undefined) {
      bad(`${op} is a wish about another member's pick: their name, soft`);
    }
    checkWeighting(rule, kind);
    return;
  }
  if ((kind === 'bool' || kind === 'text') && !['eq', 'neq', 'in', 'not_in'].includes(op)) bad(`${op} doesn't apply to ${kind}`);
  if (op === 'in' || op === 'not_in') {
    if (!Array.isArray(rule.value) || rule.value.length === 0) bad(`${op} needs a non-empty list`);
    for (const v of rule.value) scalar(v, kind);
  } else if (op === 'between') {
    if (!ORDERED.includes(kind) || !Array.isArray(rule.value) || rule.value.length !== 2) {
      bad('between needs [low, high] of an ordered attribute');
    }
    const [low, high] = rule.value.map(v => scalar(v, kind)) as number[];
    if (kind !== 'time' && low > high) bad('between needs low ≤ high (only times may wrap past midnight)');
  } else {
    scalar(rule.value, kind);
  }
  checkWeighting(rule, kind);
}

// How much a preference matters, and how far from it still earns partial credit.
function checkWeighting(rule: Rule, kind: string): void {
  if ((rule.importance !== undefined || rule.tolerance !== undefined) && rule.kind !== 'soft') {
    bad('only a preference (soft) has an importance or a tolerance');
  }
  if (rule.importance !== undefined && (!Number.isInteger(rule.importance) || rule.importance < 1 || rule.importance > 5)) {
    bad('importance is a whole number from 1 to 5');
  }
  if (rule.tolerance !== undefined) {
    const t = rule.tolerance as unknown;
    if (!['lt', 'lte', 'gt', 'gte'].includes(rule.operator) || !t || typeof t !== 'object' || Array.isArray(t) ||
        Object.keys(t).join() !== 'limit') {
      bad('a tolerance is {limit} on lt, lte, gt or gte');
    }
    const limit = scalar(rule.tolerance.limit, kind) as number;
    const value = scalar(rule.value, kind) as number;
    if (['lt', 'lte'].includes(rule.operator) ? limit <= value : limit >= value) bad('a tolerance limit lies past the preferred value');
  }
}

// SATISFIED / VIOLATED / UNKNOWN / NOT_APPLICABLE for one candidate's facts.
// A missing fact is UNKNOWN, never fine.
export function evaluateRule(rule: Rule, facts: Record<string, unknown>, catalog: Catalog): Verdict {
  if (rule.aggregate !== undefined) bad('aggregate rules are evaluated on a whole plan, not one candidate');
  if ((facts.category ?? rule.category) !== rule.category) return 'NOT_APPLICABLE';
  validateRule(rule, catalog);
  // A ranking among options, or about two people's picks: not a check on one.
  if (RANKINGS.includes(rule.operator) || RELATIONS.includes(rule.operator)) return 'NOT_APPLICABLE';
  const actual = facts[rule.attribute!];
  if (actual === undefined || actual === null) return 'UNKNOWN';
  const kind = attributeType(rule, catalog);
  const a = scalar(actual, kind);
  const op = rule.operator;
  let ok: boolean;
  if (op === 'in' || op === 'not_in') {
    const inList = (rule.value as unknown[]).some(v => scalar(v, kind) === a);
    ok = op === 'in' ? inList : !inList;
  } else if (op === 'between') {
    const [low, high] = (rule.value as unknown[]).map(v => scalar(v, kind)) as number[];
    const n = a as number;
    ok = low <= high ? low <= n && n <= high : n >= low || n <= high; // a window past midnight
  } else {
    const b = scalar(rule.value, kind);
    ok = op === 'eq' ? a === b : op === 'neq' ? a !== b : op === 'lt' ? a < b : op === 'lte' ? a <= b : op === 'gt' ? a > b : a >= b;
  }
  return ok ? 'SATISFIED' : 'VIOLATED';
}

// ---------- The old named limits, as rules (contracts/legacy_keys.json) ----------

export type Need = { category: string; attribute: string; needs: number; kind: 'hard' | 'soft' };
export type SearchFact = { fact: string; value: unknown };
export type LegacyTable = { keys: Record<string, Record<string, any>>; kept_prefix: string };
export type LegacyRow = { value: unknown; hardness?: string; authorized_range?: unknown };

// An old named limit as rules, needs or search facts. Coverage markers and notes
// aren't planning inputs; a cleared value (set aside) makes nothing. The twin of
// rules.py from_legacy.
export function fromLegacy(key: string, row: LegacyRow, table: LegacyTable, catalog: Catalog) {
  const out = { rules: [] as Rule[], needs: [] as Need[], facts: [] as SearchFact[] };
  if (key.startsWith(table.kept_prefix)) return out;
  const spec = table.keys[key] ?? bad(`unknown named limit ${key}`);
  let value = row.value;
  if (value === null || value === undefined || ('only_when' in spec && value !== spec.only_when)) return out;
  if (spec.as === 'search_fact') out.facts.push({ fact: spec.fact, value });
  // A need someone hedged ("own room if possible") is a preference, never a must.
  if (spec.as === 'need') {
    out.needs.push({ category: spec.category, attribute: spec.attribute, needs: spec.needs,
      kind: (row.hardness ?? 'HARD') === 'HARD' ? 'hard' : 'soft' });
  }
  if (spec.as !== 'rule') return out;
  const target = 'aggregate' in spec ? { aggregate: spec.aggregate as string } : { category: spec.category as string, attribute: spec.attribute as string };
  if ('value_from' in spec) {
    const window = value as Record<string, unknown>;
    value = (spec.value_from as string[]).map((k, i) => window[k] || spec.defaults[i]);
  } else if ('value' in spec) {
    value = spec.value;
  }
  if (row.hardness === 'SOFT' && 'flexible_limit' in spec) {
    // Flexible: what they'd like is a preference with partial credit up to their stated top,
    // or up to the catalog's tolerance if they named none. Only a stated top is also firm.
    const top = ((row.authorized_range ?? {}) as { max?: number }).max;
    if (top !== undefined) out.rules.push({ ...target, operator: spec.operator, value: top, kind: 'hard' });
    const limit = top ?? ('soft_tolerance' in spec ? Math.round((value as number) * (spec.soft_tolerance as number)) : undefined);
    const soft: Rule = { ...target, operator: spec.operator, value, kind: 'soft' };
    if (limit !== undefined && limit > (value as number)) soft.tolerance = { limit };
    out.rules.push(soft);
  } else {
    const firm = (row.hardness ?? 'HARD') === 'HARD' || spec.enforce_soft === true;
    out.rules.push({ ...target, operator: spec.operator, value, kind: firm ? 'hard' : 'soft' });
  }
  for (const rule of out.rules) validateRule(rule, catalog);
  return out;
}
