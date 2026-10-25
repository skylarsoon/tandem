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
};
export type Catalog = {
  categories: Record<string, Record<string, { type: string }>>;
  aggregates: Record<string, { type: string }>;
};
export type Verdict = 'SATISFIED' | 'VIOLATED' | 'UNKNOWN' | 'NOT_APPLICABLE';

const OPERATORS = ['eq', 'neq', 'lt', 'lte', 'gt', 'gte', 'in', 'not_in', 'between'];
const ORDERED = ['money', 'int', 'number', 'epoch', 'time'];
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

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

// A value of an attribute's type, in comparable form (times as minutes).
function scalar(value: unknown, kind: string): number | boolean | string {
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
}

// SATISFIED / VIOLATED / UNKNOWN / NOT_APPLICABLE for one candidate's facts.
// A missing fact is UNKNOWN, never fine.
export function evaluateRule(rule: Rule, facts: Record<string, unknown>, catalog: Catalog): Verdict {
  if (rule.aggregate !== undefined) bad('aggregate rules are evaluated on a whole plan, not one candidate');
  if ((facts.category ?? rule.category) !== rule.category) return 'NOT_APPLICABLE';
  validateRule(rule, catalog);
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
