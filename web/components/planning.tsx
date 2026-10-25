import type { workflow } from "../lib/workflow";
export function Planning({
  progress,
}: {
  progress: ReturnType<typeof workflow>;
}) {
  const agreed = progress.published?.state === "AGREED";
  const title = agreed
    ? "Agreed · everyone approved these exact terms"
    : progress.published
      ? "Planning complete · review the proposal"
      : progress.last?.phase === "PAUSED"
        ? "Waiting for participant input"
        : progress.last?.stage === "STOPPED"
          ? "Planning stopped · see progress and private notes"
          : Object.values(progress.states).includes("active")
            ? "Worker running"
            : progress.last
              ? "Last recorded planning progress"
              : "Waiting for the planning worker";
  return (
    <section className="card planning" aria-label="Planning status">
      <p className="eyebrow">PLANNING</p>
      <h2>{title}</h2>
      <p>
        Confirmed inputs queue planning in SpacetimeDB. A running worker picks
        up this room through subscription notifications or polling.
      </p>
      <small>
        Answer questions in My Agent. Once all requested answers arrive, the
        worker resumes automatically. Final proposal approval is always your own
        choice.
      </small>
      {!progress.last && (
        <p className="muted">
          If nothing starts, check that the worker service is running for this
          room and that participant names and trip searches match its
          configuration.
        </p>
      )}
    </section>
  );
}
