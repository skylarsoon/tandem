import { json, money, type Row } from "./state";
import { workflow } from "./workflow";

export type Tables = Record<string, Row[]>;
export type TimelineItem = {
  key: string;
  kind: "message" | "question" | "brief" | "patch" | "intake" | "proposal" | "event";
  audience: "private" | "shared";
  speaker: string;
  row: Row;
  text?: string;
  time: bigint;
};
export const preferenceDefaults = {
  speakUpForMe: false, askMeFirst: true, giveMeTime: true, remindersOptedIn: false,
};
export type Preferences = typeof preferenceDefaults;

export function scoped(tables: Tables, room: Row): Tables {
  return Object.fromEntries(Object.entries(tables).map(([name, rows]) => [name,
    rows.filter(row => row.roomId === undefined || String(row.roomId) === String(room.id)),
  ]));
}

export function timestamp(value: unknown): bigint {
  const stamp = value as { microsSinceUnixEpoch?: bigint; toDate?: () => Date } | undefined;
  try {
    if (stamp?.microsSinceUnixEpoch !== undefined) return BigInt(stamp.microsSinceUnixEpoch);
    if (stamp?.toDate) return BigInt(stamp.toDate().getTime()) * 1000n;
    if (typeof value === "bigint") return value;
  } catch { /* An unknown timestamp sorts deterministically at the beginning. */ }
  return 0n;
}

export function readable(value: unknown): string {
  return String(value ?? "").replaceAll("_", " ").toLowerCase();
}

const labels: Record<string, string> = {
  origin_airport: "Departure airport", destination_airport: "Destination airport",
  flight_budget_total: "Flight budget", trip_budget_total: "Whole-trip budget",
  no_budget_limit: "No budget limit", own_bedroom: "Your own bedroom",
  baggage_required: "Checked bag needed", stay_min_rating: "Minimum stay rating",
  arrival_window: "Arrival time", max_stops: "Maximum connections", dates_ok: "Dates work for you",
  trip_note: "Personal note", total_cents: "cost", my_cost: "your total share",
  arrival_local: "arrival time", stops: "connections", capacity: "capacity",
  FLIGHT: "Flights", STAY: "Accommodation", TRANSFER: "Transfers", ACTIVITY: "Activities", DINNER: "Dinner",
};
export const label = (key: unknown) => labels[String(key)] || readable(key);

export function constraintValue(text: unknown): unknown {
  try { return JSON.parse(String(text)); } catch { return undefined; }
}

function valueText(value: unknown, monetary = false): string {
  if (Array.isArray(value)) return value.map(v => valueText(v, monetary)).join(" – ");
  if (monetary && typeof value === "number") return money(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value && typeof value === "object") return Object.entries(value).map(([k, v]) => `${readable(k)} ${String(v)}`).join(", ");
  return String(value ?? "Not specified");
}

export function describeFact(patch: Row): string {
  if (patch.rule) {
    const rule = patch.rule;
    const operators: Record<string, string> = {
      eq: "is", neq: "is not", lt: "under", lte: "at most", gt: "over", gte: "at least",
      in: "one of", not_in: "excluding", between: "between",
    };
    const attribute = rule.attribute || rule.aggregate;
    return `${rule.category ? `${label(rule.category)} · ` : ""}${label(attribute)} ${operators[rule.operator] || readable(rule.operator)} ${valueText(rule.value, ["total_cents", "my_cost"].includes(attribute))}`;
  }
  const monetary = /cents/i.test(patch.unit || "") || ["flight_budget_total", "trip_budget_total"].includes(patch.key);
  return `${label(patch.key)}: ${valueText(patch.value, monetary)}`;
}

export function factStrength(patch: Row): string {
  const soft = patch.rule ? patch.rule.kind === "soft" : patch.hardness === "SOFT";
  const range = patch.authorized_range;
  return `${soft ? "Preference" : "Requirement"}${range?.max !== undefined ? ` · approved flexibility up to ${money(range.max)}` : ""}`;
}

const eventText: Record<string, string> = {
  ROOM_CREATED: "Trip created", MEMBER_JOINED: "A traveller joined",
  REQUIREMENTS_UPDATED: "A traveller updated their requirements",
  ROOM_SETTINGS_UPDATED: "Shared trip details changed", TRIP_BRIEF_CONFIRMED: "Trip brief confirmed",
  TRAVELLER_READY: "A traveller confirmed their details", PROPOSAL_SUPERSEDED: "An earlier proposal was superseded",
  PROPOSAL_AGREED: "Everyone approved the proposal", PROPOSAL_REOPENED: "The proposal is open for review again",
  NO_FEASIBLE_PLAN: "No plan meets the confirmed requirements among the options checked",
};
export function revisionEvents(tables: Tables): Row[] {
  return (tables.room_events || []).filter(e => e.audience === "ROOM" && eventText[e.type])
    .sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
}
export function revisionText(event: Row): string {
  const payload = json<Row>(event.safePayloadJson, {});
  return typeof payload.message === "string" ? payload.message : eventText[event.type] || "Trip details updated";
}

export function timeline(room: Row, input: Tables): TimelineItem[] {
  const tables = scoped(input, room), result: TimelineItem[] = [];
  const add = (kind: TimelineItem["kind"], prefix: string, rows: Row[], audience: TimelineItem["audience"], speaker: string, text?: (row: Row) => string) => {
    for (const row of rows) result.push({ kind, key: `${prefix}:${row.id ?? row.roomId}:${kind === "brief" ? row.revision : ""}`,
      audience, speaker, row, text: text?.(row), time: timestamp(row.createdAt ?? row.updatedAt) });
  };
  add("message", "private", tables.my_private_messages || [], "private", "Your agent", r => r.body);
  add("message", "agent", tables.my_agent_messages || [], "private", "Your agent", r => r.utterance);
  add("message", "shared", tables.room_messages || [], "shared", "Agent", r => r.utterance);
  const brief = tables.room_trip_brief?.[0];
  if (brief?.description) result.push({ kind: "message", key: `description:${brief.roomId}:${brief.revision}`,
    audience: "shared", speaker: "Organizer", row: brief, text: brief.description, time: timestamp(brief.updatedAt) });
  add("brief", "brief", (tables.room_trip_brief || []).filter(b => b.state !== "DRAFT"), "shared", "Trip brief");
  add("question", "question", tables.my_questions || [], "private", "Your agent");
  add("patch", "patch", tables.my_constraint_patches || [], "private", "Suggested detail");
  add("intake", "intake", (tables.my_intake || []).filter(i => ["SUMMARY", "CONFIRMED"].includes(i.state)), "private", "Your details");
  add("proposal", "proposal", tables.room_proposals || [], "shared", "Plan to review");
  add("event", "event", revisionEvents(tables), "shared", "Trip update", revisionText);
  // Same source records can arrive through overlapping subscription updates.
  const unique = [...new Map(result.map(item => [item.key, item])).values()];
  return unique.sort((a, b) => a.time < b.time ? -1 : a.time > b.time ? 1 : a.key.localeCompare(b.key));
}

export function established(room: Row, tables: Tables): [string, string][] {
  const settings = json<Row>(room.settingsJson, {});
  const confirmed = tables.room_trip_brief?.find(b => b.state === "CONFIRMED");
  const facts = confirmed ? { ...json<Row>(confirmed.briefJson, {}), ...settings } : settings;
  const rows: [string, string][] = [];
  if (facts.destination) rows.push(["Destination", facts.destination]);
  if (facts.start_date) rows.push(["Dates", `${facts.start_date}${facts.end_date ? ` → ${facts.end_date}` : ""}`]);
  if (facts.destination_airport) rows.push(["Arrival airport", facts.destination_airport]);
  // Default room components are not evidence that the user chose to fly.
  if (confirmed && Array.isArray(facts.components) && facts.components.length)
    rows.push(["Planning", facts.components.map(label).join(", ")]);
  return rows;
}

export function planningStatus(room: Row, tables: Tables, connected: boolean): string {
  if (!connected) return "Reconnecting · waiting for synchronized details";
  const progress = workflow(room, tables);
  if (progress.published?.state === "AGREED") return "Agreed · everyone approved";
  if (progress.published) return "Ready for your review";
  if (tables.my_questions?.some(q => q.state === "OPEN")) return "Your agent has a question";
  if (tables.my_intake?.some(i => i.state === "SUMMARY")) return "Review your suggested details";
  if (!tables.room_trip_brief?.some(b => b.state === "CONFIRMED")) return "Getting to know the trip";
  const intake = tables.room_intake_progress?.[0];
  if (intake && intake.ready < intake.total) return `Waiting for traveller details · ${intake.ready} of ${intake.total} ready`;
  if (progress.last?.phase === "PAUSED") return "Waiting for participant input";
  if (progress.last?.stage === "STOPPED") return "Planning paused · see the conversation";
  if (Object.values(progress.states).includes("active")) return "Your agents are working";
  return "Waiting for the next planning update";
}

export function outgoingMessage(room: Row, tables: Tables, body: string, confirmation?: { prefs: Preferences; excluded: string[] }) {
  const trimmed = body.trim();
  if (!trimmed) throw new Error("Write a message first");
  const organizer = tables.room_members?.some(m => m.isMe && m.role === "ORGANIZER");
  if (organizer && !tables.room_trip_brief?.length)
    return { kind: "description" as const, args: { roomId: room.id, description: trimmed } };
  const intake = tables.my_intake?.[0];
  const coverage = json<Row>(intake?.coverageJson, {});
  // An explicit confirmation uses the same owner action and selected details
  // as the review button. Incomplete answers still go to the agent to clarify.
  if (confirmation && /^confirm[.!]?$/i.test(trimmed) && intake?.state === "SUMMARY"
      && coverage.origin === "ANSWERED" && coverage.money === "ANSWERED"
      && !(tables.my_private_messages || []).some(m => m.sender === "HUMAN"
        && timestamp(m.createdAt) > timestamp(intake.updatedAt))) {
    return { kind: "confirmation" as const, args: { roomId: room.id,
      acceptedPatchIds: (tables.my_constraint_patches || [])
        .filter(p => p.state === "PENDING" && !confirmation.excluded.includes(String(p.id))).map(p => p.id),
      ...confirmation.prefs,
    } };
  }
  return { kind: "private" as const, args: { roomId: room.id, body: trimmed } };
}
