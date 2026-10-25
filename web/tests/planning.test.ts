import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PlanningConversation, TripOverview, type Reducers } from "../components/planning-room";
import { constraintValue, describeFact, established, factStrength, outgoingMessage, planningStatus, revisionEvents, scoped, timeline, type Tables } from "../lib/planning";
import { queries, questionAction } from "../lib/state";

const room = { id: 1n, goal: "Camping weekend", inputRevision: 3n, membershipRevision: 1n, permissionRevision: 1n, settingsJson: '{}' };
const members = [{ id: 1n, roomId: 1n, role: "ORGANIZER", isMe: true, active: true, displayName: "Sam" }];
function chat(tables: Tables = {}, connected = true) {
  return renderToStaticMarkup(createElement(PlanningConversation, { room, tables: { room_members: members, ...tables }, connected, reducers: {} as Reducers }));
}
const pendingBrief = { roomId: 1n, state: "PROPOSED", revision: 3n, briefJson: '{"destination":"Big Sur","components":["CAMPING"]}' };

describe("conversation-driven planning", () => {
  it("has no fixed budget, flight, airport, or hotel inputs and keeps chat after onboarding", () => {
    const examples: Tables[] = [{}, { my_intake: [{ roomId: 1n, state: "CONFIRMED" }] }];
    for (const tables of examples) {
      const html = chat(tables);
      expect(html).toContain("Message your agent");
      expect(html).toContain("Send message");
      for (const old of ["Flight budget (USD)", "Minimum stay rating", "Confirm constraints", "Shared stay"])
        expect(html).not.toContain(old);
    }
  });
  it("renders a non-flight proposed brief without an airport placeholder", () => {
    const html = chat({ room_trip_brief: [pendingBrief] });
    expect(html).toContain("Big Sur");
    expect(html).toContain("camping");
    expect(html).toContain("Confirm trip brief");
    expect(html).not.toContain("undefined");
    expect(html).not.toContain("()");
  });
  it("disables actions when disconnected and does not show other rooms' private data", () => {
    const html = chat({ room_trip_brief: [pendingBrief], my_private_messages: [{ id: 1n, roomId: 2n, body: "OTHER_ROOM_PRIVATE" }] }, false);
    expect(html).not.toContain("OTHER_ROOM_PRIVATE");
    expect(html).not.toContain("Confirm trip brief");
    expect(html).toContain('disabled=""');
    expect(html).toContain("Reconnecting");
  });
  it("keeps declined and superseded questions read-only", () => {
    const html = chat({ my_questions: [{ id: 1n, roomId: 1n, kind: "MISSING_FACT", prompt: "Old question", state: "SUPERSEDED" }] });
    expect(html).toContain("Earlier question");
    expect(html).not.toContain(">Yes</button>");
  });
  it("uses the description API only for the organizer's initial message", () => {
    expect(outgoingMessage(room, { room_members: members }, "  Go camping  ")).toEqual({ kind: "description", args: { roomId: 1n, description: "Go camping" } });
    expect(outgoingMessage(room, { room_members: members, room_trip_brief: [pendingBrief] }, "Change the dates").kind).toBe("private");
    expect(outgoingMessage(room, {}, "I need a quiet place").kind).toBe("private");
    expect(() => outgoingMessage(room, {}, " ")).toThrow();
  });
});

describe("planning projections", () => {
  it("orders and deduplicates mixed timeline sources using microsecond timestamps", () => {
    const privateMessage = { id: 1n, roomId: 1n, body: "Private", sender: "HUMAN", createdAt: { microsSinceUnixEpoch: 200n } };
    const result = timeline(room, {
      my_private_messages: [privateMessage, privateMessage],
      room_messages: [{ id: 1n, roomId: 1n, utterance: "Shared", createdAt: { microsSinceUnixEpoch: 100n } }],
    });
    expect(result.map(i => [i.text, i.audience])).toEqual([["Shared", "shared"], ["Private", "private"]]);
    expect(new Set(result.map(i => i.key)).size).toBe(2);
    expect(scoped({ my_private_messages: [{ roomId: 2n }] }, room).my_private_messages).toEqual([]);
  });
  it("formats money, range, and rule strength without exposing raw cents or operators", () => {
    expect(describeFact({ rule: { category: "FLIGHT", attribute: "total_cents", operator: "lte", value: 50000 } })).toBe("Flights · cost at most $500.00");
    expect(describeFact({ key: "trip_budget_total", value: 100000, unit: "USD_CENTS" })).toContain("$1,000.00");
    expect(factStrength({ hardness: "SOFT", authorized_range: { max: 120000 } })).toBe("Preference · approved flexibility up to $1,200.00");
    expect(constraintValue("broken")).toBeUndefined();
  });
  it("does not infer chosen flights from default room settings", () => {
    expect(established({ ...room, settingsJson: '{"components":["FLIGHT"]}' }, {})).toEqual([]);
    expect(established(room, { room_trip_brief: [pendingBrief] })).toEqual([]);
  });
  it("only uses public change events for revision summaries", () => {
    const tables = { room_events: [
      { id: 1n, audience: "OWNER_PRIVATE", type: "REQUIREMENTS_UPDATED", safePayloadJson: '{"message":"PRIVATE_CAP"}' },
      { id: 2n, audience: "ROOM", type: "REQUIREMENTS_UPDATED" },
    ] };
    expect(revisionEvents(tables)).toHaveLength(1);
    const html = renderToStaticMarkup(createElement(TripOverview, { room, tables, connected: true }));
    expect(html).not.toContain("PRIVATE_CAP");
    expect(html).toContain("updated their requirements");
  });
  it("distinguishes waiting on travellers from worker failure", () => {
    expect(planningStatus(room, { room_trip_brief: [{ state: "CONFIRMED" }], room_intake_progress: [{ ready: 1, total: 3 }] }, true)).toContain("1 of 3 ready");
    expect(planningStatus(room, {}, false)).toContain("Reconnecting");
  });
  it("subscribes to existing rules and intake progress views", () => {
    expect(queries("1")).toContain("SELECT * FROM my_rules WHERE room_id = 1");
    expect(queries("1")).toContain("SELECT * FROM room_intake_progress WHERE room_id = 1");
  });
});

describe("inline question payloads", () => {
  const yes = { key: "own_bedroom", value: false, hardness: "HARD", unit: "" };
  const no = { ...yes, value: true };
  const question = { id: 2n, version: 4, kind: "MISSING_FACT", state: "OPEN", actionJson: JSON.stringify({ nudge: { yes, no } }) };
  it.each([true, false])("persists the offered constraint for answer %s", answer => {
    const action = questionAction(question, answer, {});
    expect(action.version).toBe(4);
    expect(JSON.parse(action.answerJson)).toEqual({ answer, constraint: answer ? yes : no });
  });
  it("rejects malformed offered answers and stale questions", () => {
    expect(() => questionAction({ ...question, actionJson: '{"nudge":{}}' }, true, {})).toThrow();
    expect(() => questionAction({ ...question, state: "SUPERSEDED" }, true, {})).toThrow();
  });
});
