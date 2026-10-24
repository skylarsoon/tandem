import { expect, it } from "vitest";
import { workflow } from "../lib/workflow";
const room = {
  id: 1n,
  inputRevision: 1n,
  membershipRevision: 2n,
  permissionRevision: 3n,
};
function event(id: number, stage: string, phase: string, extra = {}) {
  return {
    id: BigInt(id),
    roomId: 1n,
    type: "WORKFLOW_PROGRESS",
    audience: "ROOM",
    safePayloadJson: JSON.stringify({
      schema_version: 1,
      job_ref: "j1",
      generation: "g1",
      attempt: "1",
      stage,
      phase,
      step_ref: String(id),
      round: 0,
      cycle: 0,
      input_revision: 1,
      membership_revision: 2,
      permission_revision: 3,
      ...extra,
    }),
  };
}
it("orders and deduplicates typed events while ignoring private and unsupported data", () => {
  const start = event(1, "RESEARCH", "STARTED");
  const end = event(2, "RESEARCH", "COMPLETED");
  const result = workflow(room, {
    room_events: [
      end,
      start,
      start,
      { ...event(3, "SOLVE", "STARTED"), audience: "OWNER_PRIVATE" },
      event(4, "SOLVE", "STARTED", { schema_version: 8 }),
    ],
  });
  expect(result.execution).toHaveLength(2);
  expect(result.states.RESEARCH).toBe("completed");
  expect(result.states.SOLVE).toBe("pending");
});
it("does not infer execution from utterances or mark uncorroborated steps active", () => {
  expect(
    workflow(room, { room_events: [event(1, "SOLVE", "STARTED")] }).states
      .SOLVE,
  ).toBe("last reported");
  expect(
    workflow(room, {
      room_events: [event(1, "SOLVE", "STARTED")],
      room_agent_activity: [{ roomId: 1n, activity: "CHECKING" }],
    }).states.SOLVE,
  ).toBe("active");
});
it("handles loops, pauses, generations and stopped execution", () => {
  const events = [
    event(1, "RESEARCH", "COMPLETED"),
    event(2, "SOLVE", "COMPLETED"),
    event(3, "AGENTS", "PAUSED"),
    event(4, "RESEARCH", "STARTED", { round: 1 }),
  ];
  const graph = workflow(room, { room_events: events });
  expect(
    workflow(room, { room_events: events.slice(0, 3) }).states.AGENTS,
  ).toBe("waiting");
  expect(graph.states.AGENTS).toBe("pending");
  expect(graph.loop?.round).toBe(1);
  const newer = workflow(
    { ...room, permissionRevision: 4n },
    { room_events: events },
  );
  expect(newer.execution).toHaveLength(0);
  expect(newer.history).toHaveLength(4);
  expect(
    workflow(room, { room_events: [...events, event(5, "STOPPED", "FAILED")] })
      .states.RESEARCH,
  ).toBe("stopped");
});
it("uses correlated proposal state for publication, agreement and withdrawal", () => {
  const rows = {
    room_events: [
      event(1, "PUBLISH", "STARTED"),
      {
        id: 2n,
        roomId: 1n,
        audience: "ROOM",
        type: "PROPOSAL_PUBLISHED",
        safePayloadJson: '{"proposal_id":9}',
      },
    ],
    room_proposals: [{ id: 9n, roomId: 1n, state: "AGREED" }],
  };
  expect(workflow(room, rows).states.AGREED).toBe("completed");
  expect(
    workflow(room, {
      ...rows,
      room_proposals: [{ id: 9n, roomId: 1n, state: "AWAITING_REVIEW" }],
    }).states.REVIEW,
  ).toBe("waiting");
  expect(workflow({ ...room, inputRevision: 2n }, rows).states.AGREED).toBe(
    "pending",
  );
});
it("preserves completed load on recovery and separates evaluations for a new draft", () => {
  const events = [
    event(1, "LOAD", "COMPLETED"),
    event(2, "LOAD", "RECOVERING", { attempt: "2" }),
    event(3, "AGENTS", "STARTED", { draft_ref: "draft-new" }),
  ];
  const result = workflow(room, {
    room_events: events,
    room_threads: [{ id: 8n, roomId: 1n, jobId: "j1" }],
    room_messages: [
      { id: 9n, roomId: 1n, threadId: 8n, draftRef: "draft-old" },
      { id: 10n, roomId: 1n, threadId: 8n, draftRef: "draft-new" },
    ],
  });
  expect(result.states.LOAD).toBe("completed");
  expect(result.messages.map((m) => m.id)).toEqual([10n]);
});
