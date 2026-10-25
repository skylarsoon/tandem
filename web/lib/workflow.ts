import { json, type Row } from "./state";
export const stages = [
  ["LOAD", "Load inputs"],
  ["RESEARCH", "Research"],
  ["SOLVE", "Solve"],
  ["AGENTS", "Personal agents"],
  ["VALIDATE", "Validate"],
  ["PUBLISH", "Publish"],
  ["REVIEW", "Human review"],
  ["AGREED", "Agreed"],
] as const;
export function sameRevisions(event: Row, room: Row) {
  return (
    String(event.input_revision) === String(room.inputRevision) &&
    String(event.membership_revision) === String(room.membershipRevision) &&
    String(event.permission_revision) === String(room.permissionRevision)
  );
}
export function workflow(room: Row, tables: Record<string, Row[]>) {
  const rows = (name: string) =>
    (tables[name] || []).filter((r) => String(r.roomId) === String(room.id));
  const seen = new Set<string>();
  const history = rows("room_events")
    .filter((e) => e.audience === "ROOM" && e.type === "WORKFLOW_PROGRESS")
    .sort((a, b) => (a.id < b.id ? -1 : 1))
    .flatMap<Row>((e) => {
      const p = json<Row>(e.safePayloadJson, {});
      if (
        p.schema_version !== 1 ||
        ![
          "STARTED",
          "COMPLETED",
          "PAUSED",
          "RECOVERING",
          "FINISHED",
          "FAILED",
        ].includes(p.phase) ||
        ![...stages.map((s) => s[0]), "STOPPED"].includes(p.stage)
      )
        return [];
      const key = JSON.stringify([
        p.job_ref,
        p.generation,
        p.attempt,
        p.stage,
        p.phase,
        p.step_ref,
      ]);
      if (seen.has(key)) return [];
      seen.add(key);
      return [{ ...p, eventId: e.id }];
    });
  const current = history.filter((e) => sameRevisions(e, room));
  const last = current.at(-1);
  const execution = last
    ? current.filter(
        (e) => e.job_ref === last.job_ref && e.generation === last.generation,
      )
    : [];
  const states: Record<string, string> = Object.fromEntries(
    stages.map(([id]) => [id, "pending"]),
  );
  for (const e of execution) {
    if (e.stage === "STOPPED") continue;
    if (e.phase === "RECOVERING" && states[e.stage] === "completed") continue;
    if (e.phase === "STARTED") {
      const index = stages.findIndex(([id]) => id === e.stage);
      for (const [id] of stages.slice(index + 1)) states[id] = "pending";
    }
    states[e.stage] =
      e.phase === "COMPLETED"
        ? "completed"
        : e.phase === "PAUSED"
          ? "waiting"
          : e.phase === "RECOVERING"
            ? "last reported"
            : "last reported";
  }
  if (last?.stage === "STOPPED") {
    const previous = execution.filter((e) => e.stage !== "STOPPED").at(-1);
    if (previous) states[previous.stage] = "stopped";
  }
  const activity = rows("room_agent_activity");
  const live = activity.some(
    (a) => a.activity !== "IDLE" && a.activity !== "WAITING_FOR_OWNER",
  );
  if (last?.phase === "STARTED" && live) states[last.stage] = "active";
  const publishStart = execution
    .filter((e) => e.stage === "PUBLISH" && e.phase === "STARTED")
    .at(-1);
  const publication = publishStart
    ? rows("room_events")
        .filter(
          (e) =>
            e.audience === "ROOM" &&
            e.type === "PROPOSAL_PUBLISHED" &&
            e.id > publishStart.eventId,
        )
        .sort((a, b) => (a.id < b.id ? -1 : 1))
        .at(-1)
    : undefined;
  const publishedId = publication
    ? json<Row>(publication.safePayloadJson, {}).proposal_id
    : undefined;
  const published = rows("room_proposals").find(
    (p) => String(p.id) === String(publishedId) && p.state !== "SUPERSEDED",
  );
  if (published) {
    states.PUBLISH = "completed";
    states.REVIEW = published.state === "AGREED" ? "completed" : "waiting";
    states.AGREED = published.state === "AGREED" ? "completed" : "pending";
  }
  const threads = rows("room_threads").filter((t) =>
    last
      ? String(t.jobId) === String(last.job_ref)
      : String(t.inputRevision) === String(room.inputRevision),
  );
  const threadIds = new Set(threads.map((t) => String(t.id)));
  const currentDraft = execution.filter((e) => e.draft_ref).at(-1)?.draft_ref;
  const messages = rows("room_messages")
    .filter(
      (m) =>
        threadIds.has(String(m.threadId)) &&
        (!currentDraft || m.draftRef === currentDraft),
    )
    .sort((a, b) => (a.id < b.id ? -1 : 1));
  return {
    states,
    execution,
    history: history.filter((e) => !sameRevisions(e, room)),
    messages,
    activity,
    last,
    published,
    loop: execution
      .filter(
        (e) => e.phase === "STARTED" && ["RESEARCH", "SOLVE"].includes(e.stage),
      )
      .at(-1),
  };
}
