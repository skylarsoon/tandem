"use client";
import {
  useEffect,
  useState,
  useRef,
  useSyncExternalStore,
  type FormEvent,
} from "react";
import { Planning } from "../components/planning";
import { Workflow } from "../components/workflow";
import { workflow as projectWorkflow } from "../lib/workflow";
import { connect } from "../lib/client";
import {
  json,
  money,
  questionAction,
  proposalAction,
  type Row,
} from "../lib/state";

type Client = ReturnType<typeof connect>;
const defaults = {
  speakUpForMe: false,
  askMeFirst: true,
  giveMeTime: true,
  remindersOptedIn: false,
};
function date(value: any) {
  try {
    return value?.toDate ? value.toDate().toLocaleString() : "";
  } catch {
    return "";
  }
}
function Button({
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...props}>{children}</button>;
}
export default function Page() {
  const [client, setClient] = useState<Client>();
  const [error, setError] = useState("");
  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_STDB_URL,
      db = process.env.NEXT_PUBLIC_STDB_DB;
    if (!url || !db) {
      setError(
        "Set NEXT_PUBLIC_STDB_URL and NEXT_PUBLIC_STDB_DB in web/.env.local, then restart the frontend.",
      );
      return;
    }
    try {
      const next = connect(url, db);
      setClient(next);
      return () => next.close();
    } catch {
      setError("Could not initialize the database connection.");
    }
  }, []);
  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">TANDEM / LIVE DEMO</p>
          <h1>A plan everyone can choose.</h1>
          <p>Personal agents explore. People decide.</p>
        </div>
        <span className="badge">Prototype</span>
      </header>
      {error ? (
        <p role="alert" className="notice">
          {error}
        </p>
      ) : client ? (
        <Dashboard client={client} />
      ) : (
        <p>Connecting…</p>
      )}
    </main>
  );
}
function Dashboard({ client }: { client: Client }) {
  const state = useSyncExternalStore(
    client.session.subscribe,
    client.session.getSnapshot,
    client.session.getSnapshot,
  );
  const [roomId, setRoomId] = useState<string>();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("Our group trip");
  const [invite, setInvite] = useState("");
  const [prefs, setPrefs] = useState(defaults);
  const tables = state.tables;
  const rows = (name: string) => tables[name] || [];
  const room = rows("my_rooms").find((r) => String(r.id) === roomId);
  const ready = state.status === "ready";
  const disabled = !ready || pending;
  const authoritativePrefs = rows("my_participation_preferences")[0];
  useEffect(() => {
    if (authoritativePrefs)
      setPrefs({
        speakUpForMe: authoritativePrefs.speakUpForMe,
        askMeFirst: authoritativePrefs.askMeFirst,
        giveMeTime: authoritativePrefs.giveMeTime,
        remindersOptedIn: authoritativePrefs.remindersOptedIn,
      });
  }, [authoritativePrefs]);
  async function act(fn: () => Promise<unknown>) {
    setError("");
    setPending(true);
    try {
      await fn();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "The action failed. Please reconnect and try again.",
      );
    } finally {
      setPending(false);
    }
  }
  function select(id?: string) {
    setRoomId(id);
    client.session.select(id);
  }
  const lastWorkflow = useRef<
    { room: Row; tables: Record<string, Row[]> } | undefined
  >(undefined);
  useEffect(() => {
    if (ready && room) {
      const shared = Object.fromEntries(
        [
          "room_members",
          "room_agents",
          "room_events",
          "room_messages",
          "room_threads",
          "room_agent_activity",
          "room_proposals",
        ].map((key) => [
          key,
          (tables[key] || []).filter(
            (r) => key !== "room_events" || r.audience === "ROOM",
          ),
        ]),
      );
      lastWorkflow.current = { room, tables: shared };
    }
  }, [ready, room, tables]);
  const reducers = client.connection.reducers;
  const members = rows("room_members");
  const agentName = (id: any) => {
    const agent = rows("room_agents").find((a) => a.id === id);
    return agent?.kind === "MEDIATOR"
      ? "Mediator"
      : members.find((m) => m.agentId === id)?.displayName || "Agent";
  };
  return (
    <>
      <div className="connection">
        <span className={`dot ${ready ? "online" : ""}`} />
        {state.status} · {process.env.NEXT_PUBLIC_STDB_DB}
        <small>Claude + Apify · subscription-triggered worker.</small>
      </div>
      {(error || state.error) && (
        <p role="alert" className="notice">
          {error || state.error}
        </p>
      )}
      <section className="card">
        <h2>Your rooms</h2>
        <div className="actions">
          {rows("my_rooms").map((r) => (
            <Button
              key={String(r.id)}
              disabled={disabled}
              aria-pressed={String(r.id) === roomId}
              onClick={() => select(String(r.id))}
            >
              {r.goal}
            </Button>
          ))}
        </div>
        <div className="entry">
          <label>
            Your display name
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              placeholder="Use the name in the operator config"
            />
          </label>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(() =>
                reducers.createRoom({ goal, displayName: name.trim() }),
              );
            }}
          >
            <label>
              New room goal
              <input
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                required
              />
            </label>
            <Button disabled={disabled || !name.trim() || !goal.trim()}>
              Create room
            </Button>
          </form>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(() =>
                reducers.joinRoom({
                  inviteCode: invite.trim(),
                  displayName: name.trim(),
                }),
              );
            }}
          >
            <label>
              Invite code
              <input
                value={invite}
                onChange={(e) => setInvite(e.target.value)}
                required
              />
            </label>
            <Button disabled={disabled || !name.trim() || !invite.trim()}>
              Join room
            </Button>
          </form>
        </div>
      </section>
      {roomId && !ready && <p>Waiting for synchronized room data…</p>}
      {!ready &&
        roomId &&
        lastWorkflow.current &&
        String(lastWorkflow.current.room.id) === roomId && (
          <Workflow
            room={lastWorkflow.current.room}
            tables={lastWorkflow.current.tables}
            connected={false}
          />
        )}
      {room && ready && (
        <>
          <section className="card room-head">
            <div>
              <p className="eyebrow">ROOM {roomId}</p>
              <h2>{room.goal}</h2>
              <p>
                Recorded status: <strong>{room.status}</strong>
              </p>
              <p>
                Invite: <code>{room.inviteCode}</code>{" "}
                <Button
                  disabled={disabled}
                  onClick={() =>
                    void act(() =>
                      navigator.clipboard.writeText(room.inviteCode),
                    )
                  }
                >
                  Copy invite
                </Button>
              </p>
            </div>
            <div>
              <h3>Travellers</h3>
              {members
                .filter((m) => m.active)
                .map((m) => (
                  <p key={String(m.id)}>
                    {m.displayName}
                    {m.isMe ? " (you)" : ""} <small>{m.role}</small>
                  </p>
                ))}
            </div>
          </section>
          <Planning progress={projectWorkflow(room, tables)} />
          <Workflow
            key={`workflow-${room.id}`}
            room={room}
            tables={tables}
            connected={ready}
          />
          <div className="columns">
            <div>
              <section className="card">
                <h2>My settings</h2>
                <p className="muted">
                  Private constraints. Preferences grant authority only after
                  confirmation.
                </p>
                <fieldset disabled={disabled}>
                  <legend>How your agent participates</legend>
                  {(
                    [
                      ["speakUpForMe", "Speak up for me"],
                      ["askMeFirst", "Ask me first"],
                      ["giveMeTime", "Give me time"],
                      ["remindersOptedIn", "Opt in to reminders"],
                    ] as const
                  ).map(([key, label]) => (
                    <label className="check" key={key}>
                      <input
                        type="checkbox"
                        checked={prefs[key]}
                        onChange={(e) =>
                          setPrefs({ ...prefs, [key]: e.target.checked })
                        }
                      />
                      {label}
                    </label>
                  ))}
                  <Button
                    onClick={() =>
                      void act(() =>
                        reducers.confirmParticipationPreferences(prefs),
                      )
                    }
                  >
                    Confirm participation preferences
                  </Button>
                  <p>
                    {authoritativePrefs?.confirmed
                      ? "Confirmed"
                      : "Not confirmed"}
                  </p>
                </fieldset>
                <Constraints
                  key={`${roomId}-${rows("my_constraints")
                    .map((c) => String(c.revision))
                    .join("-")}`}
                  constraints={rows("my_constraints")}
                  disabled={disabled}
                  save={(key, value, unit, hardness) =>
                    reducers.setConstraint({
                      roomId: room.id,
                      key,
                      valueJson: JSON.stringify(value),
                      unit,
                      hardness,
                      authorizedRangeJson: undefined,
                      privateRationale: undefined,
                    })
                  }
                  act={act}
                />
                {members.find((m) => m.isMe)?.role === "ORGANIZER" && (
                  <RoomSettings
                    room={room}
                    disabled={disabled}
                    act={act}
                    save={(settingsJson) =>
                      reducers.setRoomSettings({
                        roomId: room.id,
                        settingsJson,
                      })
                    }
                  />
                )}
              </section>
              <section className="card private">
                <p className="eyebrow">ONLY YOU</p>
                <h2>My Agent</h2>
                {rows("my_questions")
                  .filter((q) => q.state === "OPEN")
                  .map((q) => (
                    <article key={String(q.id)}>
                      <p className="tag">{q.kind.replaceAll("_", " ")}</p>
                      <p>{q.prompt}</p>
                      <small>
                        This answer applies to the requested action. It is not
                        final proposal approval.
                      </small>
                      <div className="actions">
                        {[true, false].map((yes) => (
                          <Button
                            key={String(yes)}
                            disabled={disabled}
                            onClick={() =>
                              void act(() =>
                                reducers.answerQuestion(
                                  questionAction(q, yes, prefs),
                                ),
                              )
                            }
                          >
                            {yes ? "Yes" : "No"}
                          </Button>
                        ))}
                      </div>
                    </article>
                  ))}
                {!rows("my_questions").some((q) => q.state === "OPEN") && (
                  <p className="muted">
                    No open questions. Unanswered questions stay pending.
                  </p>
                )}
                {rows("my_private_messages").map((m) => (
                  <article key={`private-${m.id}`}>
                    <small>
                      {m.sender} · {date(m.createdAt)}
                    </small>
                    <p>{m.body}</p>
                  </article>
                ))}
                {rows("room_events")
                  .filter((e) => e.audience !== "ROOM")
                  .map((e) => (
                    <article key={`event-${e.id}`}>
                      <p>
                        {json<Row>(e.safePayloadJson, {}).text ||
                          json<Row>(e.safePayloadJson, {}).message ||
                          e.type.replaceAll("_", " ")}
                      </p>
                    </article>
                  ))}
                {rows("my_agent_messages").map((m) => (
                  <article key={`agent-${m.id}`}>
                    <p>{m.utterance}</p>
                  </article>
                ))}
                {rows("my_proposal_impacts").map((i) => (
                  <article key={String(i.id)}>
                    <h3>Your proposal impact</h3>
                    <p>
                      {json<Row>(i.impactJson, {}).text ||
                        "Your allocation is available in the proposal."}
                    </p>
                  </article>
                ))}
              </section>
            </div>
            <div>
              <section className="card">
                <h2>Live negotiation</h2>
                <p className="muted">
                  Committed agent messages. No simulated progress.
                </p>
                {!rows("room_messages").length && (
                  <p>
                    No messages yet. The worker picks up queued planning jobs
                    automatically.
                  </p>
                )}
                {[...rows("room_messages")]
                  .sort((a, b) => (a.id < b.id ? -1 : 1))
                  .map((m) => (
                    <article key={String(m.id)}>
                      <small>
                        {agentName(m.senderAgentId)} →{" "}
                        {agentName(m.recipientAgentId)} · {m.type}
                      </small>
                      <p>{m.utterance}</p>
                    </article>
                  ))}
                {rows("room_agent_activity").map((a) => (
                  <p key={String(a.agentId)}>
                    <small>
                      {agentName(a.agentId)}: {a.activity}
                    </small>
                  </p>
                ))}
                {rows("room_milestones").map((m) => (
                  <span className="tag" key={String(m.id)}>
                    {m.stage}: {m.state}
                  </span>
                ))}
              </section>
              <section className="card">
                <h2>Proposals</h2>
                <p className="muted">
                  Review exact terms. Approval does not reserve or purchase
                  anything.
                </p>
                {!rows("room_proposals").length && (
                  <p>No proposal published yet.</p>
                )}
                {rows("room_proposals").map((p) => (
                  <Proposal
                    key={String(p.id)}
                    proposal={p}
                    counts={rows("room_consent_counts").find(
                      (c) => c.proposalId === p.id,
                    )}
                    consent={rows("my_consents").find(
                      (c) => c.proposalId === p.id,
                    )}
                    disabled={disabled}
                    act={act}
                    respond={(args) => reducers.respondToProposal(args)}
                    withdraw={() =>
                      reducers.withdrawConsent({
                        proposalId: p.id,
                        note: undefined,
                      })
                    }
                  />
                ))}
              </section>
              <section className="card">
                <h2>Research & progress</h2>
                {rows("room_events")
                  .filter(
                    (e) =>
                      e.audience === "ROOM" && e.type !== "WORKFLOW_PROGRESS",
                  )
                  .map((e) => (
                    <article key={String(e.id)}>
                      <small>{e.type}</small>
                      <p>
                        {json<Row>(e.safePayloadJson, {}).text ||
                          json<Row>(e.safePayloadJson, {}).message ||
                          e.type.replaceAll("_", " ")}
                      </p>
                    </article>
                  ))}
                <details>
                  <summary>
                    Sourced candidates ({rows("room_candidates").length})
                  </summary>
                  {rows("room_candidates").map((c) => (
                    <article key={String(c.id)}>
                      <p>
                        {c.category} · {c.mode}
                      </p>
                      <p>
                        {json<Row>(c.factsJson, {}).title || c.candidateRef} ·{" "}
                        {money(json<Row>(c.factsJson, {}).total_cents)}
                      </p>
                      <Source url={c.sourceUrl} />
                    </article>
                  ))}
                </details>
              </section>
            </div>
          </div>
        </>
      )}
    </>
  );
}
function Source({ url }: { url: unknown }) {
  try {
    const parsed = new URL(String(url));
    return ["https:", "http:"].includes(parsed.protocol) ? (
      <a href={parsed.href} target="_blank" rel="noreferrer">
        Source ↗
      </a>
    ) : null;
  } catch {
    return null;
  }
}
function Constraints({
  constraints,
  disabled,
  save,
  act,
}: {
  constraints: Row[];
  disabled: boolean;
  save: (
    key: string,
    value: any,
    unit: string,
    hardness: string,
  ) => Promise<unknown>;
  act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const value = (key: string, fallback: any) => {
    const c = constraints.find((c) => c.key === key);
    try {
      return c ? JSON.parse(c.valueJson) : fallback;
    } catch {
      return fallback;
    }
  };
  const [flight, setFlight] = useState(
    String(
      value("flight_budget_total", "") === ""
        ? ""
        : value("flight_budget_total", 0) / 100,
    ),
  );
  const [trip, setTrip] = useState(
    String(
      value("trip_budget_total", "") === ""
        ? ""
        : value("trip_budget_total", 0) / 100,
    ),
  );
  const [bag, setBag] = useState(!!value("baggage_required", false));
  const [bed, setBed] = useState(!!value("own_bedroom", false));
  const [rating, setRating] = useState(String(value("stay_min_rating", 0)));
  async function submit(e: FormEvent) {
    e.preventDefault();
    await act(async () => {
      await save(
        "flight_budget_total",
        Math.round(Number(flight) * 100),
        "USD_CENTS",
        "HARD",
      );
      if (trip !== "")
        await save(
          "trip_budget_total",
          Math.round(Number(trip) * 100),
          "USD_CENTS",
          "HARD",
        );
      await save("baggage_required", bag, "BOOLEAN", "HARD");
      await save("own_bedroom", bed, "BOOLEAN", "SOFT");
      await save("stay_min_rating", Number(rating), "RATING", "SOFT");
    });
  }
  return (
    <form onSubmit={submit}>
      <fieldset disabled={disabled}>
        <legend>Your confirmed constraints</legend>
        <label>
          Flight budget (USD)
          <input
            type="number"
            min="0"
            step="0.01"
            required
            value={flight}
            onChange={(e) => setFlight(e.target.value)}
          />
        </label>
        <label>
          Whole-trip budget (USD, optional)
          <input
            type="number"
            min="0"
            step="0.01"
            required={constraints.some((c) => c.key === "trip_budget_total")}
            value={trip}
            onChange={(e) => setTrip(e.target.value)}
          />
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={bag}
            onChange={(e) => setBag(e.target.checked)}
          />
          At least one checked bag required
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={bed}
            onChange={(e) => setBed(e.target.checked)}
          />
          Prefer my own bedroom
        </label>
        <label>
          Minimum stay rating (0–5)
          <input
            type="number"
            min="0"
            max="5"
            step="0.1"
            value={rating}
            onChange={(e) => setRating(e.target.value)}
            required
          />
        </label>
        <Button>Confirm constraints</Button>
      </fieldset>
    </form>
  );
}
function RoomSettings({
  room,
  disabled,
  act,
  save,
}: {
  room: Row;
  disabled: boolean;
  act: (fn: () => Promise<unknown>) => Promise<void>;
  save: (text: string) => Promise<unknown>;
}) {
  const settings = json<Row>(room.settingsJson, {});
  const [stay, setStay] = useState(!!settings.components?.includes("STAY"));
  const [transfer, setTransfer] = useState(
    !!settings.components?.includes("TRANSFER"),
  );
  return (
    <fieldset disabled={disabled}>
      <legend>Shared trip components · organizer</legend>
      <label className="check">
        <input
          type="checkbox"
          checked={stay}
          onChange={(e) => setStay(e.target.checked)}
        />
        Shared stay
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={transfer}
          onChange={(e) => setTransfer(e.target.checked)}
        />
        Airport transfer
      </label>
      <Button
        onClick={() =>
          void act(() =>
            save(
              JSON.stringify({
                ...settings,
                components: [
                  "FLIGHT",
                  ...(stay ? ["STAY"] : []),
                  ...(transfer ? ["TRANSFER"] : []),
                ],
              }),
            ),
          )
        }
      >
        Save trip components
      </Button>
    </fieldset>
  );
}
function Proposal({
  proposal: p,
  counts,
  consent,
  disabled,
  act,
  respond,
  withdraw,
}: {
  proposal: Row;
  counts?: Row;
  consent?: Row;
  disabled: boolean;
  act: (fn: () => Promise<unknown>) => Promise<void>;
  respond: (args: any) => Promise<unknown>;
  withdraw: () => Promise<unknown>;
}) {
  const summary = json<Row>(p.sharedSummaryJson, {}),
    terms = json<Row>(p.termsJson, {});
  const [note, setNote] = useState("");
  return (
    <article>
      <div className="actions">
        <h3>Version {p.version}</h3>
        <span className="badge">{p.state}</span>
      </div>
      <p>{summary.text || "Review the selected options below."}</p>
      {(Array.isArray(terms.selected) ? terms.selected : []).map((c: Row) => (
        <div className="option" key={c.id}>
          <strong>{c.category || "FLIGHT"}</strong>
          <p>
            {c.title || c.flight_identity || c.id} · {money(c.total_cents)}
          </p>
          <Source url={c.source_url} />
        </div>
      ))}
      {Array.isArray(summary.suggestions) && !!summary.suggestions.length && (
        <details>
          <summary>
            Activity & dinner ideas · estimates, outside approval
          </summary>
          {summary.suggestions.map((s: Row) => (
            <p key={s.candidate}>
              {s.title} · {money(s.estimate_per_person_cents)} per person{" "}
              <Source url={s.source_url} />
            </p>
          ))}
        </details>
      )}
      <p>
        {counts
          ? `${counts.accepted} accepted · ${counts.pending} pending · ${counts.declined} declined · ${counts.required} reviewers`
          : "Waiting for consent counts"}
      </p>
      <p>Your response: {consent?.response || "Not recorded"}</p>
      {p.state === "AWAITING_REVIEW" && p.isReviewer && (
        <>
          <label>
            Optional feedback
            <textarea value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <div className="actions">
            {[
              ["ACCEPT", "Accept exact terms"],
              ["NOT_COMFORTABLE", "Not comfortable"],
              ["ASK_MY_AGENT", "Ask my agent"],
              ["SUGGEST_ALTERNATIVE", "Suggest alternative"],
              ["DECIDE_LATER", "Decide later"],
            ].map(([response, label]) => (
              <Button
                key={response}
                disabled={disabled}
                onClick={() =>
                  void act(() => respond(proposalAction(p, response, note)))
                }
              >
                {label}
              </Button>
            ))}
          </div>
          <small>
            Agent feedback is recorded. Interpretation and automatic replanning
            from feedback are not connected yet.
          </small>
        </>
      )}
      {consent?.response === "ACCEPTED" &&
        ["AGREED", "AWAITING_REVIEW"].includes(p.state) && (
          <Button disabled={disabled} onClick={() => void act(withdraw)}>
            Withdraw my acceptance
          </Button>
        )}
    </article>
  );
}
