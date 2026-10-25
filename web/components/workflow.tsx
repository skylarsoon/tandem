"use client";
import { useEffect, useRef, useState } from "react";
import { stages, workflow } from "../lib/workflow";
import type { Row } from "../lib/state";
export function Workflow({
  room,
  tables,
  connected,
}: {
  room: Row;
  tables: Record<string, Row[]>;
  connected: boolean;
}) {
  const graph = workflow(room, tables);
  const [selected, setSelected] = useState("AGENTS");
  const [inspecting, setInspecting] = useState(false);
  function selectNode(id: string) {
    setSelected(id);
    setInspecting(true);
  }
  const [flash, setFlash] = useState<Row>();
  const lastId = useRef<string | undefined>(undefined);
  const initialized = useRef(false);
  const members = (tables.room_members || []).filter((m) => m.active);
  const agents = tables.room_agents || [];
  const latest = graph.messages.at(-1);
  const name = (id: unknown) =>
    agents.find((a) => a.id === id)?.kind === "MEDIATOR"
      ? "Mediator"
      : members.find((m) => m.agentId === id)?.displayName || "Agent";
  useEffect(() => {
    const id = latest ? String(latest.id) : undefined;
    if (!connected) {
      setFlash(undefined);
      return;
    }
    if (initialized.current && id && id !== lastId.current) setFlash(latest);
    lastId.current = id;
    initialized.current = true;
    const timer = setTimeout(() => setFlash(undefined), 1200);
    return () => clearTimeout(timer);
  }, [latest?.id, connected]);
  const selectedMessages = selected.startsWith("agent:")
    ? graph.messages.filter(
        (m) =>
          String(m.senderAgentId) === selected.slice(6) ||
          String(m.recipientAgentId) === selected.slice(6),
      )
    : graph.messages;
  return (
    <section className="card workflow" aria-label="Live workflow">
      <div className="actions">
        <h2>Workflow</h2>
        <span className="badge">
          {connected ? "Live records" : "Disconnected · last recorded"}
        </span>
      </div>
      <p className="muted">
        Typed execution events show progress. Agent responses are not human
        approval.
      </p>
      <div className="workflow-scroll">
        <div className="workflow-track">
          <svg
            className="stage-links"
            viewBox="0 0 1000 80"
            aria-hidden="true"
            preserveAspectRatio="none"
          >
            {stages.slice(0, -1).map(([id], i) => (
              <path
                key={id}
                d={`M ${i * 125 + 62} 40 L ${(i + 1) * 125 + 62} 40`}
                className={
                  graph.states[id] === "active" && connected ? "live-link" : ""
                }
              />
            ))}
          </svg>
          <div className="stage-grid">
            {stages.map(([id, label]) => {
              const status =
                !connected && graph.states[id] === "active"
                  ? "last reported"
                  : graph.states[id];
              return (
                <button
                  key={id}
                  className={`stage ${status.replaceAll(" ", "-")}`}
                  aria-pressed={selected === id}
                  onClick={() => selectNode(id)}
                >
                  <strong>{label}</strong>
                  <small>{status}</small>
                </button>
              );
            })}
          </div>
        </div>
      </div>
      <div className="agent-scroll">
        <div
          className="agent-workflow"
          style={{ minWidth: Math.max(300, members.length * 150) }}
        >
          <button
            className="mediator-node"
            onClick={() => selectNode("AGENTS")}
          >
            Mediator / coordinator
          </button>
          <svg
            className="agent-links"
            viewBox="0 0 1000 40"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            {members.map((m, i) => {
              const x = ((i + 0.5) * 1000) / members.length;
              const lit =
                connected &&
                flash &&
                (flash.senderAgentId === m.agentId ||
                  flash.recipientAgentId === m.agentId);
              return (
                <path
                  key={String(m.id)}
                  className={lit ? "live-link" : ""}
                  d={`M 500 0 V 15 H ${x} V 40`}
                />
              );
            })}
          </svg>
          <div
            className="agent-grid"
            style={{
              display: "grid",
              gridTemplateColumns: `repeat(${Math.max(1, members.length)},minmax(130px,1fr))`,
            }}
          >
            {members.map((m) => {
              const status = graph.activity.find(
                (a) => a.agentId === m.agentId,
              )?.activity;
              const active =
                connected &&
                graph.last?.stage === "AGENTS" &&
                ["THINKING", "WRITING"].includes(status);
              const responded = graph.messages.some(
                (msg) => msg.senderAgentId === m.agentId,
              );
              return (
                <button
                  key={String(m.id)}
                  onClick={() => selectNode(`agent:${m.agentId}`)}
                  aria-pressed={selected === `agent:${m.agentId}`}
                  className={`agent-node ${active ? "active" : ""}`}
                >
                  <span>{m.displayName}’s agent</span>
                  <small>
                    {active
                      ? status.toLowerCase()
                      : responded
                        ? "Responded"
                        : "No response recorded"}
                  </small>
                </button>
              );
            })}
          </div>
        </div>
      </div>
      {flash && connected && (
        <div className="message-link" role="status">
          <svg viewBox="0 0 120 14" aria-hidden="true">
            <path d="M 0 7 L 114 7 M 108 2 L 115 7 L 108 12" />
          </svg>
          {name(flash.senderAgentId)} → {name(flash.recipientAgentId)} ·{" "}
          {flash.type}
        </div>
      )}
      {graph.loop && (
        <p className="muted">
          Latest research/solve pass: round {Number(graph.loop.round) + 1},
          cycle {Number(graph.loop.cycle) + 1}.
        </p>
      )}
      {!graph.execution.length && (
        <p>
          No execution reported for these inputs yet. The configured worker will
          pick up confirmed inputs automatically.
        </p>
      )}
      <details
        open={inspecting}
        onToggle={(e) => setInspecting(e.currentTarget.open)}
      >
        <summary>
          Inspect{" "}
          {selected.startsWith("agent:")
            ? "agent"
            : stages.find((s) => s[0] === selected)?.[1] || "progress"}
        </summary>
        {graph.execution
          .filter((e) => e.stage === selected)
          .slice(-6)
          .map((e) => (
            <p key={String(e.eventId)}>
              <small>
                {e.stage} · {e.phase} · round {Number(e.round) + 1}
              </small>
            </p>
          ))}
        {(["AGENTS", "REVIEW"].includes(selected) ||
          selected.startsWith("agent:")) &&
          selectedMessages.slice(-6).map((m) => (
            <article key={String(m.id)}>
              <small>
                {name(m.senderAgentId)} → {name(m.recipientAgentId)} · {m.type}
              </small>
              <p>{m.utterance}</p>
            </article>
          ))}
      </details>
      {!!graph.history.length && (
        <details>
          <summary>
            Earlier input generations ({graph.history.length} progress records)
          </summary>
          {graph.history.slice(-12).map((e) => (
            <p key={String(e.eventId)}>
              <small>
                Generation {e.generation} · {e.stage} · {e.phase}
              </small>
            </p>
          ))}
        </details>
      )}
    </section>
  );
}
