import { describe, expect, it, vi } from "vitest";
import {
  accessor,
  baseTables,
  personalTables,
  roomTables,
  RoomSession,
  json,
  proposalAction,
  queries,
  questionAction,
  questionAnswer,
} from "../lib/state";
function fake() {
  const subscriptions: any[] = [];
  const db = Object.fromEntries(
    [...baseTables, ...roomTables, ...personalTables].map((name) => {
      const callbacks = new Set<() => void>();
      const table = {
        rows: [] as any[],
        iter() {
          return this.rows.values();
        },
        onInsert: (f: () => void) => callbacks.add(f),
        onDelete: (f: () => void) => callbacks.add(f),
        onUpdate: (f: () => void) => callbacks.add(f),
        removeOnInsert: (f: () => void) => callbacks.delete(f),
        removeOnDelete: (f: () => void) => callbacks.delete(f),
        removeOnUpdate: (f: () => void) => callbacks.delete(f),
        callbacks,
      };
      return [accessor(name), table];
    }),
  );
  const connection = {
    db,
    subscriptionBuilder() {
      const sub: any = {
        applied: () => {},
        unsubscribe: vi.fn(),
        onApplied(fn: any) {
          this.applied = fn;
          return this;
        },
        onError() {
          return this;
        },
        subscribe(sql: any) {
          this.sql = sql;
          subscriptions.push(this);
          return this;
        },
      };
      return sub;
    },
  };
  return { connection, subscriptions };
}
describe("room subscriptions", () => {
  it("subscribes only to approved views and scopes room rows", () => {
    expect(
      queries("42").every(
        (q) =>
          q.includes("WHERE room_id = 42") ||
          q.endsWith("my_participation_preferences"),
      ),
    ).toBe(true);
    expect(queries("42").join(" ")).not.toContain("worker_pending_jobs");
    expect(() => queries("1; DROP")).toThrow();
  });
  it("waits for both subscriptions, filters rooms, discards stale callbacks, cleans listeners", () => {
    const { connection, subscriptions } = fake();
    const session = new RoomSession(connection);
    session.connected();
    subscriptions[0].applied();
    expect(session.snapshot.status).toBe("ready");
    session.select("1");
    const [base, room] = subscriptions.slice(-2);
    connection.db.roomMessages.rows = [
      { roomId: 1n, id: 1n },
      { roomId: 2n, id: 2n },
    ];
    base.applied();
    expect(session.snapshot.status).toBe("synchronizing");
    room.applied();
    expect(session.snapshot.tables.room_messages).toEqual([
      { roomId: 1n, id: 1n },
    ]);
    session.select("2");
    expect(room.unsubscribe).toHaveBeenCalledOnce();
    room.applied();
    expect(session.snapshot.status).toBe("synchronizing");
    subscriptions.at(-2).applied();
    subscriptions.at(-1).applied();
    expect(session.snapshot.tables.room_messages).toEqual([
      { roomId: 2n, id: 2n },
    ]);
    session.dispose();
    expect([...connection.db.roomMessages.callbacks]).toHaveLength(0);
  });
  it("clears private data during outages and restores from committed cache without duplicates", async () => {
    const { connection, subscriptions } = fake();
    const session = new RoomSession(connection);
    session.select("1");
    connection.db.myQuestions.rows = [
      { id: 1n, roomId: 1n, prompt: "PRIVATE" },
    ];
    subscriptions[0].applied();
    subscriptions[1].applied();
    expect(session.snapshot.tables.my_questions).toHaveLength(1);
    session.disconnected();
    expect(session.snapshot.tables).toEqual({});
    session.connected();
    subscriptions.at(-2).applied();
    subscriptions.at(-1).applied();
    connection.db.myQuestions.callbacks.forEach((f: any) => f());
    await Promise.resolve();
    expect(session.snapshot.tables.my_questions).toHaveLength(1);
  });
});
describe("review payloads", () => {
  it("parses invalid JSON without crashing", () => {
    for (const raw of ["broken", "null", "1", '"text"'])
      expect(json(raw, {})).toEqual({});
  });
  it("uses kind-specific owner answers", () => {
    expect(questionAnswer("CONCESSION_CONSULTATION", true, {})).toEqual({
      granted: true,
    });
    expect(questionAnswer("OBJECTION_DISCLOSURE", false, {})).toEqual({
      granted: false,
    });
    expect(questionAnswer("MISSING_FACT", true, {})).toEqual({ answer: true });
    expect(questionAnswer("PARTICIPATION_CONFIRMATION", false, {})).toEqual({
      confirmed: false,
    });
    expect(questionAnswer("PARTICIPATION_CONFIRMATION", true, {})).toEqual({
      speak_up_for_me: false,
      ask_me_first: true,
      give_me_time: true,
      reminders_opted_in: false,
    });
    expect(() => questionAnswer("UNKNOWN", true, {})).toThrow();
  });
  it("binds approval to the displayed version and digest and refuses stale proposals", () => {
    const p = {
      id: 3n,
      version: 2,
      termsDigest: "digest",
      state: "AWAITING_REVIEW",
      isReviewer: true,
    };
    expect(proposalAction(p, "ACCEPT")).toEqual({
      proposalId: 3n,
      version: 2,
      termsDigest: "digest",
      response: "ACCEPT",
      note: undefined,
    });
    expect(() =>
      proposalAction({ ...p, state: "SUPERSEDED" }, "ACCEPT"),
    ).toThrow();
    expect(() =>
      proposalAction({ ...p, isReviewer: false }, "ACCEPT"),
    ).toThrow();
  });
});

it("rejects stale question actions and preserves the displayed version", () => {
  const q = { id: 5n, version: 7, kind: "MISSING_FACT", state: "OPEN" };
  expect(questionAction(q, true, {})).toEqual({
    questionId: 5n,
    version: 7,
    answerJson: '{"answer":true}',
  });
  expect(() =>
    questionAction({ ...q, state: "SUPERSEDED" }, true, {}),
  ).toThrow();
});
