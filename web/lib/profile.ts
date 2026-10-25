import { PROFILE_CATALOG } from "./profile_catalog";

// Profile onboarding, rendered from the shared question catalog
// (contracts/profile_questions.json). Pure helpers; the screens live in
// components/profile.tsx. The database re-checks every answer.

export type Option = {
  id: string;
  label: string;
  hint?: string;
  exclusive?: boolean;
  other_text?: boolean;
  needs_strength?: boolean;
  expands?: string;
};
export type Question = {
  id: string;
  key: string;
  section: string;
  prompt: string;
  input: string;
  helper?: string;
  required?: boolean;
  max_length?: number;
  max?: number;
  options?: Option[];
  groups?: { id: string; label: string; options: Option[] }[];
  text_field?: { id: string; label: string };
  avoid_list?: { label: string };
  checks?: { id: string; label: string }[];
  ask_privately?: boolean;
  private?: boolean;
  choose_for_me?: boolean;
};
export type Answer = { status: "ANSWERED" | "SKIPPED"; value: any };

export const catalog = PROFILE_CATALOG as unknown as {
  version: number;
  intro: string;
  outro: string;
  sections: { id: string; title: string; optional: boolean; private_note?: string }[];
  review_groups: { id: string; title: string; questions: string[] }[];
  questions: Question[];
};
export const questions = new Map(catalog.questions.map((q) => [q.id, q]));
export const core = catalog.questions.filter((q) => q.section === "core");
export const AVATARS = ["otter", "fox", "owl", "turtle", "whale", "panda"];

export function question(id: string): Question {
  const q = questions.get(id);
  if (!q) throw new Error(`Unknown profile question ${id}`);
  return q;
}

/** Answers keyed by question id, from the my_profile_answers rows. */
export function answersFrom(rows: Record<string, any>[]): Record<string, Answer> {
  const out: Record<string, Answer> = {};
  for (const r of rows) {
    try {
      out[r.questionId] = { status: r.status, value: JSON.parse(r.valueJson) };
    } catch {
      out[r.questionId] = { status: r.status, value: null };
    }
  }
  return out;
}

/** Toggle a chip: exclusive choices ("None", "No preference") stand alone; a limit is never exceeded. */
export function toggle(options: Option[], picked: string[], id: string, max?: number): string[] {
  if (picked.includes(id)) return picked.filter((p) => p !== id);
  const option = options.find((o) => o.id === id);
  if (option?.exclusive) return [id];
  const rest = picked.filter((p) => !options.find((o) => o.id === p)?.exclusive);
  if (max !== undefined && rest.length >= max) return rest;
  return [...rest, id];
}

/** Move an item in a ranking one place up (-1) or down (+1). */
export function move(ranked: string[], id: string, by: -1 | 1): string[] {
  const i = ranked.indexOf(id), j = i + by;
  if (i < 0 || j < 0 || j >= ranked.length) return ranked;
  const next = [...ranked];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

const label = (options: Option[] | undefined, id: string) =>
  options?.find((o) => o.id === id)?.label ?? id;
const strength = (s?: string) =>
  s === "REQUIRED" ? "a must" : s === "PREFERRED" ? "a preference" : "";

/** An answer in plain words for the review page ("Not shared yet" when unanswered). */
export function describe(q: Question, answer?: Answer): string {
  if (!answer) return "Not shared yet";
  if (answer.status === "SKIPPED") return "Skipped for now";
  const v = answer.value;
  switch (q.input) {
    case "text":
    case "long_text":
      return String(v);
    case "single": {
      const extra = [strength(v.strength), v.details].filter(Boolean).join("; ");
      return label(q.options, v.selected) + (extra ? ` (${extra})` : "");
    }
    case "multi": {
      const parts = [
        v.selected.map((id: string) => label(q.options, id)).join(", ") +
          (v.other ? ` (${v.other})` : ""),
      ];
      if (v.avoid?.length) parts.push("Avoid: " + v.avoid.map((id: string) => label(q.options, id)).join(", "));
      if (v.text) parts.push(v.text);
      return parts.join(" · ");
    }
    case "rank":
      return v.ranked.map((id: string, i: number) => `${i + 1}. ${label(q.options, id)}`).join("  ");
    case "groups":
      return (q.groups ?? [])
        .filter((g) => v[g.id])
        .map((g) => `${g.label}: ${label(g.options, v[g.id])}`)
        .join(" · ");
    case "avatar":
      return v.initials ? "Initials" : v.avatar;
    case "home_base":
      return [v.city, v.region, v.country].filter(Boolean).join(", ") +
        (v.airports?.length ? ` (${v.airports.join(", ")})` : "");
    case "needs":
      return v.items.map((i: any) => `${label(q.options, i.id)} (${strength(i.strength)})`).join(", ");
    case "allergies":
      return v.items.map((i: any) => i.food).join(", ") + " — always avoided";
    case "boundaries":
      return v.items.map((i: any) => `${i.text} (${strength(i.strength)})`).join(", ");
    default:
      return "Answered";
  }
}

/** Is a draft complete enough to save (the database still checks everything)? */
export function ready(q: Question, v: any): boolean {
  if (v === undefined || v === null) return false;
  switch (q.input) {
    case "text":
    case "long_text":
      return typeof v === "string" && v.trim().length > 0;
    case "single":
      return !!v.selected && (!q.options?.find((o) => o.id === v.selected)?.needs_strength || !!v.strength);
    case "multi":
      return Array.isArray(v.selected) && v.selected.length > 0;
    case "rank":
      return !!q.options && Array.isArray(v.ranked) && v.ranked.length > 0 && v.ranked.length <= (q.max ?? q.options.length)
        && new Set(v.ranked).size === v.ranked.length
        && v.ranked.every((id: string) => q.options!.some(option => option.id === id)) && v.confirmed === true;
    case "groups":
      return Object.keys(v).length > 0;
    case "avatar":
      return !!v.avatar || v.initials === true;
    case "home_base":
      return !!v.city?.trim() && !!v.country?.trim();
    case "needs":
      return Array.isArray(v.items) && v.items.length > 0 && v.items.every((i: any) => i.strength);
    case "allergies":
      return Array.isArray(v.items) && v.items.length > 0 && v.items.every((i: any) => i.food?.trim() && i.checks?.length);
    case "boundaries":
      return Array.isArray(v.items) && v.items.length > 0 && v.items.every((i: any) => i.text?.trim() && i.strength);
    default:
      return false;
  }
}
