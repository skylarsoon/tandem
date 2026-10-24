// A sandbox room on the shared database, run by three simulated people. Their
// tokens stay on this machine (.sandbox/, git-ignored). Actions call the real
// reducers as them, and "what X sees" queries the real views with X's token.
import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { STDB_DB, STDB_URL, decode } from "./stdb";

const DIR = path.resolve(process.cwd(), ".sandbox");
const STATE = path.join(DIR, "state.json");
const PEOPLE = ["ava", "ben", "cleo"] as const;
export type Person = (typeof PEOPLE)[number];
type State = { roomId: number; inviteCode: string; people: Record<Person, { identity: string; token: string }> };

export const PERSON_VIEWS = ["my_rooms", "room_members", "room_trip_brief", "room_intake_progress", "my_intake",
  "my_private_messages", "my_constraint_patches", "my_constraints", "my_questions", "my_participation_preferences",
  "room_events", "room_messages", "room_proposals", "my_proposal_impacts", "my_consents", "worker_pending_jobs"];

export function load(): State | null {
  try { return JSON.parse(fs.readFileSync(STATE, "utf8")); } catch { return null; }
}

function save(state: State) {
  fs.mkdirSync(DIR, { recursive: true });
  fs.writeFileSync(STATE, JSON.stringify(state, null, 2), { mode: 0o600 });
}

const camel = (name: string) => name.replace(/_([a-z0-9])/g, (_, c) => c.toUpperCase());

async function http(pathname: string, body: string | undefined, token?: string, type = "application/json") {
  const res = await fetch(`${STDB_URL}${pathname}`, {
    method: "POST", body, cache: "no-store",
    headers: { "Content-Type": type, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  return { status: res.status, text: await res.text() };
}

export async function call(token: string, reducer: string, args: Record<string, unknown>) {
  const body = JSON.stringify(Object.fromEntries(Object.entries(args).map(([k, v]) => [camel(k), v])));
  const { status, text } = await http(`/v1/database/${STDB_DB}/call/${reducer}`, body, token);
  if (status !== 200) throw new Error(text.slice(0, 300) || `HTTP ${status}`);
  return text;
}

// A view as that person's own browser would get it.
export async function viewAs(token: string, view: string) {
  const { status, text } = await http(`/v1/database/${STDB_DB}/sql`, `SELECT * FROM ${view}`, token, "text/plain");
  if (status !== 200) return { error: text.slice(0, 200) };
  const [result] = JSON.parse(text) as { schema: { elements: { name: { some?: string }; algebraic_type: Record<string, unknown> }[] }; rows: unknown[][] }[];
  const elements = result.schema.elements;
  return { columns: elements.map(e => e.name.some ?? ""), rows: result.rows.map(r => r.map((v, i) => decode(v, elements[i].algebraic_type))) };
}

export async function create() {
  const people = {} as State["people"];
  for (const p of PEOPLE) {
    const { text } = await http("/v1/identity", undefined);
    const minted = JSON.parse(text);
    people[p] = { identity: `0x${minted.identity}`, token: minted.token };
  }
  await call(people.ava.token, "create_room", { goal: "Sandbox: learning SpacetimeDB", display_name: "ava" });
  const rooms = await viewAs(people.ava.token, "my_rooms");
  if ("error" in rooms) throw new Error(rooms.error);
  const id = rooms.columns.indexOf("id"), code = rooms.columns.indexOf("invite_code");
  const room = rooms.rows.reduce((a, b) => (Number(b[id]) > Number(a[id]) ? b : a));
  const state: State = { roomId: Number(room[id]), inviteCode: String(room[code]), people };
  save(state);
  return state;
}

function rows(view: { columns: string[]; rows: unknown[][] } | { error: string }) {
  if ("error" in view) return [];
  return view.rows.map(r => Object.fromEntries(view.columns.map((c, i) => [c, r[i]])));
}

const some = (v: unknown) => ({ some: v });

export async function act(action: string, person: Person, args: Record<string, unknown>) {
  const state = load();
  if (!state) throw new Error("Create the sandbox room first");
  const me = state.people[person];
  const room = state.roomId;
  switch (action) {
    case "join":
      return call(me.token, "join_room", { invite_code: state.inviteCode, display_name: person });
    case "confirm_settings":
      return call(me.token, "confirm_participation_preferences", { speak_up_for_me: Boolean(args.speakUp), ask_me_first: true, give_me_time: true, reminders_opted_in: false });
    case "set_budget":
      return call(me.token, "set_constraint", { room_id: room, key: "trip_budget_total", value_json: String(Math.round(Number(args.dollars) * 100)),
        unit: "USD_cents", hardness: args.firm ? "HARD" : "SOFT", authorized_range_json: null, private_rationale: args.reason ? some(String(args.reason)) : null });
    case "say":
      return call(me.token, "submit_private_message", { room_id: room, body: String(args.text) });
    case "describe_trip":
      return call(me.token, "set_trip_description", { room_id: room, description: String(args.text) });
    case "confirm_brief": {
      const [brief] = rows(await viewAs(me.token, "room_trip_brief")).filter(b => Number(b.room_id) === room);
      if (!brief) throw new Error("No trip brief yet");
      return call(me.token, "confirm_trip_brief", { room_id: room, revision: brief.revision });
    }
    case "confirm_origin": {
      const patch = rows(await viewAs(me.token, "my_constraint_patches"))
        .find(p => Number(p.room_id) === room && p.state === "PENDING" && JSON.parse(String(p.patch_json)).key === "origin_airport");
      if (!patch) throw new Error("No airport waiting for confirmation");
      return call(me.token, "confirm_origin", { room_id: room, patch_id: patch.id });
    }
    case "confirm_summary": {
      const pending = rows(await viewAs(me.token, "my_constraint_patches")).filter(p => Number(p.room_id) === room && p.state === "PENDING").map(p => p.id);
      const [prefs] = rows(await viewAs(me.token, "my_participation_preferences"));
      return call(me.token, "confirm_intake", { room_id: room, accepted_patch_ids: pending, speak_up_for_me: some(prefs?.speak_up_for_me ?? false),
        ask_me_first: some(prefs?.ask_me_first ?? true), give_me_time: some(prefs?.give_me_time ?? true), reminders_opted_in: null });
    }
    case "arrival_window":
      return call(me.token, "set_room_settings", { room_id: room, settings_json: JSON.stringify({ arrival_overlap_seconds: Number(args.hours) * 3600 }) });
    case "answer": {
      const question = rows(await viewAs(me.token, "my_questions")).find(q => Number(q.room_id) === room && q.state === "OPEN");
      if (!question) throw new Error("No open question");
      const action = JSON.parse(String(question.action_json));
      const yes = Boolean(args.yes);
      const answer = action.nudge ? { answer: yes, constraint: action.nudge[yes ? "yes" : "no"] }
        : ["CONCESSION_CONSULTATION", "OBJECTION_DISCLOSURE", "BROADER_EXPLORATION"].includes(String(question.kind)) ? { granted: yes } : { answer: yes };
      return call(me.token, "answer_question", { question_id: question.id, version: question.version, answer_json: JSON.stringify(answer) });
    }
    case "read_private_table": {
      // Privacy demo: read the raw table holding everyone's limits, as this person.
      const result = await viewAs(me.token, "participant_constraint");
      return "error" in result ? `Refused by SpacetimeDB: ${result.error}` : `Returned ${result.rows.length} rows`;
    }
    default:
      throw new Error(`unknown action ${action}`);
  }
}

// One sweep of the real AI worker for this room: onboarding jobs only (no paid searches).
export function runWorker(roomId: number): Promise<string> {
  const worker = path.resolve(process.cwd(), "..", "worker");
  const config = path.join(DIR, "worker-config.json");
  fs.mkdirSync(DIR, { recursive: true });
  // Enough model calls for a few onboarding conversations; no paid searches run (onboarding jobs only).
  fs.writeFileSync(config, JSON.stringify({ limits: { model_calls: 200, max_output_tokens: 4096 } }));
  return new Promise((resolve, reject) => {
    execFile("uv", ["run", "--directory", worker, "tandem-worker", "serve", "--env-file", path.join(worker, ".env"),
      "--config", config, "--state-dir", path.join(DIR, "worker"), "--room", String(roomId), "--kinds", "INTAKE,INTERPRET", "--once"],
      { timeout: 300_000, maxBuffer: 10 * 1024 * 1024 },
      (error, stdout, stderr) => (error ? reject(new Error((stderr || String(error)).slice(-400))) : resolve(stdout)));
  });
}
