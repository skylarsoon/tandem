import { fail } from './guards';
import { PROFILE_CATALOG } from './profile_catalog';

// Checks a profile answer against its question in the catalog, by input type,
// so no question needs code of its own. Not exported from the module entry.

type Option = { id: string; exclusive?: boolean; other_text?: boolean; needs_strength?: boolean };
type Question = {
  id: string;
  key: string;
  section: string;
  input: string;
  required?: boolean;
  min_length?: number;
  max_length?: number;
  max?: number;
  options?: readonly Option[];
  groups?: readonly { id: string; options: readonly Option[] }[];
  text_field?: { id: string };
  avoid_list?: unknown;
  checks?: readonly { id: string }[];
  ask_privately?: boolean;
  strength?: boolean;
};

export const CATALOG_VERSION: number = PROFILE_CATALOG.version;
const QUESTIONS = new Map((PROFILE_CATALOG.questions as unknown as Question[]).map(q => [q.id, q]));
const STRENGTHS = ['REQUIRED', 'PREFERRED'];
const MAX_ITEMS = 20;

export function profileQuestion(id: string): Question {
  return QUESTIONS.get(id) ?? fail('INVALID_ARGUMENT', `unknown profile question ${id}`);
}

function object(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('INVALID_ANSWER', `${field} must be an object`);
  return value as Record<string, unknown>;
}

function text(value: unknown, field: string, max: number, min = 0): string {
  if (typeof value !== 'string') fail('INVALID_ANSWER', `${field} must be text`);
  const trimmed = value.trim();
  if (trimmed.length < min || trimmed.length > max) fail('INVALID_ANSWER', `${field} must be ${min}-${max} characters`);
  return trimmed;
}

function optional(value: unknown, field: string, max: number): void {
  if (value !== undefined && value !== null) text(value, field, max);
}

function choices(value: unknown, field: string, options: readonly Option[], max?: number): string[] {
  if (!Array.isArray(value) || value.length === 0) fail('INVALID_ANSWER', `${field} needs at least one choice`);
  const known = new Map(options.map(o => [o.id, o]));
  const picked = value as unknown[];
  if (new Set(picked).size !== picked.length) fail('INVALID_ANSWER', `${field} has repeated choices`);
  for (const id of picked) if (typeof id !== 'string' || !known.has(id)) fail('INVALID_ANSWER', `${field}: unknown choice ${String(id)}`);
  if (max !== undefined && picked.length > max) fail('INVALID_ANSWER', `${field}: choose at most ${max}`);
  // "None", "No preference", "Still exploring" stand alone.
  if (picked.length > 1 && picked.some(id => known.get(id as string)!.exclusive)) {
    fail('INVALID_ANSWER', `${field}: that choice can't be combined with others`);
  }
  return picked as string[];
}

function strength(value: unknown, field: string): void {
  if (typeof value !== 'string' || !STRENGTHS.includes(value)) fail('INVALID_ANSWER', `${field} must be REQUIRED or PREFERRED`);
}

// Answer shapes by input type (see contracts/profile.md):
//   text, long_text  "Sam"
//   single           {selected, details?, strength?}
//   multi            {selected: [...], other?, text?, avoid?: [...]}
//   rank             {ranked: [...], confirmed: true}
//   groups           {<group id>: <option id>, ...}
//   avatar           {avatar} | {initials: true}
//   home_base        {city, region?, country, airports?: [IATA]}
//   needs            {items: [{id, strength, details?, other?}], ask_privately?}
//   allergies        {items: [{food, checks: [...], instructions?}]}
//   boundaries       {items: [{text, strength}]}
export function validateAnswer(q: Question, value: unknown): void {
  const options = q.options ?? [];
  switch (q.input) {
    case 'text':
    case 'long_text':
      text(value, q.key, q.max_length ?? 1000, q.required ? (q.min_length ?? 1) : 0);
      return;
    case 'single': {
      const v = object(value, q.key);
      const [picked] = choices([v.selected], q.key, options);
      if (q.text_field) optional(v.details, `${q.key}.details`, 300);
      else if (v.details !== undefined) fail('INVALID_ANSWER', `${q.key} takes no details`);
      const needsStrength = options.find(o => o.id === picked)!.needs_strength;
      if (needsStrength) strength(v.strength, `${q.key}.strength`);
      else if (v.strength !== undefined) fail('INVALID_ANSWER', `${q.key}: strength applies only to a must-or-preference choice`);
      return;
    }
    case 'multi': {
      const v = object(value, q.key);
      const picked = choices(v.selected, q.key, options, q.max);
      const withOther = picked.some(id => options.find(o => o.id === id)!.other_text);
      if (withOther) optional(v.other, `${q.key}.other`, 100);
      else if (v.other !== undefined) fail('INVALID_ANSWER', `${q.key}: "other" text needs the Other choice`);
      if (q.text_field) optional(v.text, `${q.key}.text`, 300);
      if (v.avoid !== undefined) {
        if (!q.avoid_list) fail('INVALID_ANSWER', `${q.key} has no avoid list`);
        const avoided = choices(v.avoid, `${q.key}.avoid`, options.filter(o => !o.exclusive));
        if (avoided.some(id => picked.includes(id))) fail('INVALID_ANSWER', `${q.key}: a choice can't be both liked and avoided`);
      }
      return;
    }
    case 'rank': {
      const v = object(value, q.key);
      choices(v.ranked, q.key, options, q.max);
      // The display order is never mistaken for a preference: ranking is confirmed explicitly.
      if (v.confirmed !== true) fail('INVALID_ANSWER', `${q.key}: confirm the order first`);
      return;
    }
    case 'groups': {
      const v = object(value, q.key);
      const groups = q.groups ?? [];
      const keys = Object.keys(v);
      if (keys.length === 0) fail('INVALID_ANSWER', `${q.key} needs at least one choice`);
      for (const key of keys) {
        const group = groups.find(g => g.id === key) ?? fail('INVALID_ANSWER', `${q.key}: unknown group ${key}`);
        choices([v[key]], `${q.key}.${key}`, group.options);
      }
      return;
    }
    case 'avatar': {
      const v = object(value, q.key);
      if (v.initials === true && v.avatar === undefined) return;
      text(v.avatar, `${q.key}.avatar`, 40, 1);
      return;
    }
    case 'home_base': {
      const v = object(value, q.key);
      text(v.city, `${q.key}.city`, 80, 1);
      text(v.country, `${q.key}.country`, 60, 1);
      optional(v.region, `${q.key}.region`, 80);
      if (v.airports !== undefined) {
        if (!Array.isArray(v.airports) || v.airports.length > 5) fail('INVALID_ANSWER', `${q.key}.airports: at most 5`);
        for (const a of v.airports) if (typeof a !== 'string' || !/^[A-Z]{3}$/.test(a)) fail('INVALID_ANSWER', `${q.key}: airports are 3-letter codes`);
      }
      return;
    }
    case 'needs': {
      const v = object(value, q.key);
      const items = list(v.items, q.key);
      const ids = choices(items.map(i => object(i, `${q.key} item`).id), q.key, options);
      items.forEach((raw, i) => {
        const item = raw as Record<string, unknown>;
        strength(item.strength, `${q.key}.${ids[i]}.strength`);
        optional(item.details, `${q.key}.${ids[i]}.details`, 300);
        if (options.find(o => o.id === ids[i])!.other_text) optional(item.other, `${q.key}.other`, 100);
      });
      if (v.ask_privately !== undefined && (!q.ask_privately || typeof v.ask_privately !== 'boolean')) {
        fail('INVALID_ANSWER', `${q.key}: ask_privately isn't available here`);
      }
      return;
    }
    case 'allergies': {
      const items = list(object(value, q.key).items, q.key);
      for (const raw of items) {
        const item = object(raw, `${q.key} item`);
        text(item.food, `${q.key}.food`, 80, 1);
        choices(item.checks, `${q.key}.checks`, (q.checks ?? []) as Option[]);
        optional(item.instructions, `${q.key}.instructions`, 300);
      }
      return;
    }
    case 'boundaries': {
      const items = list(object(value, q.key).items, q.key);
      for (const raw of items) {
        const item = object(raw, `${q.key} item`);
        text(item.text, `${q.key}.text`, 200, 1);
        strength(item.strength, `${q.key}.strength`);
      }
      return;
    }
    default:
      fail('INVALID_ARGUMENT', `unsupported input ${q.input}`);
  }
}

function list(value: unknown, field: string): unknown[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_ITEMS) {
    fail('INVALID_ANSWER', `${field} needs 1-${MAX_ITEMS} entries`);
  }
  return value;
}
