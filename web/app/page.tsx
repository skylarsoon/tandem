"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { connect } from "../lib/client";
import { PlanningConversation, TripOverview } from "../components/planning-room";
import { DestinationSlideshow } from "../components/destination-slideshow";
import { AgentSetup } from "../components/agent-setup";
import { AgentWelcome } from "../components/agent-companions";
import { scoped } from "../lib/planning";
import type { Row } from "../lib/state";

type Client = ReturnType<typeof connect>;
export default function Page() {
  const [client, setClient] = useState<Client>();
  const [error, setError] = useState("");
  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_STDB_URL, db = process.env.NEXT_PUBLIC_STDB_DB;
    if (!url || !db) { setError("Set the public database connection values in web/.env.local, then restart the frontend."); return; }
    try {
      const next = connect(url, db); setClient(next); return () => next.close();
    } catch { setError("Could not initialize the database connection."); }
  }, []);
  return <main className="planning-app">
    <header className="app-header"><a className="wordmark" href="/">tandem<span aria-hidden="true">↗</span></a><a className="profile-link" href="/profile">Your agent profile ↗</a></header>
    {error ? <p role="alert" className="notice">{error}</p> : client ? <Dashboard client={client} /> : <p role="status">Connecting to your trips…</p>}
  </main>;
}

function Dashboard({ client }: { client: Client }) {
  const state = useSyncExternalStore(client.session.subscribe, client.session.getSnapshot, client.session.getSnapshot);
  const [roomId, setRoomId] = useState<string>();
  const [setupRoom, setSetupRoom] = useState<string>();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [name, setName] = useState("");
  const [tripName, setTripName] = useState("");
  const [invite, setInvite] = useState("");
  const awaitingRoom = useRef<Set<string> | undefined>(undefined);
  const [operation, setOperation] = useState(0);
  const rooms = state.tables.my_rooms || [];
  const ready = state.status === "ready";
  const current = rooms.find(r => String(r.id) === roomId);
  // Keep shared overview only through a disconnect; private subscriptions are cleared.
  const sharedRoom = useRef<Row | undefined>(undefined);
  useEffect(() => { if (ready && current) sharedRoom.current = current; }, [ready, current]);
  const room = current || (sharedRoom.current && String(sharedRoom.current.id) === roomId ? sharedRoom.current : undefined);
  const tables = room ? scoped(state.tables, room) : {};
  const disabled = !ready || pending;
  function select(id: string) {
    setRoomId(id); sharedRoom.current = undefined; client.session.select(id);
    setSetupRoom(sessionStorage.getItem(`tandem:setup:${id}`) === 'pending' ? id : undefined);
  }
  useEffect(() => {
    if (!ready || !awaitingRoom.current) return;
    const added = rooms.find(r => !awaitingRoom.current!.has(String(r.id)));
    if (added) {
      awaitingRoom.current = undefined;
      sessionStorage.setItem(`tandem:setup:${added.id}`, 'pending');
      select(String(added.id));
    }
  }, [rooms, ready, operation]);
  async function manage(fn: () => Promise<unknown>) {
    setPending(true); setError(""); awaitingRoom.current = new Set(rooms.map(r => String(r.id)));
    try { await fn(); setOperation(value => value + 1); }
    catch (e) { awaitingRoom.current = undefined; setError(e instanceof Error ? e.message : "Could not update your trips."); }
    finally { setPending(false); }
  }
  return <div className="planning-shell">
    <aside className="overview-panel" aria-label="Trip details">
      <div className="trip-navigation"><label htmlFor="trip-select">YOUR TRIPS</label><select id="trip-select" value={roomId || ""} disabled={disabled} onChange={e => select(e.target.value)}>
        <option value="">Home · your companion</option>{rooms.map(r => <option value={String(r.id)} key={String(r.id)}>{r.goal}</option>)}
      </select></div>
      <details className="manage-trips"><summary>Create or join a trip</summary>
        <label>Your display name<input value={name} onChange={e => setName(e.target.value)} disabled={disabled} maxLength={50} /></label>
        <form onSubmit={e => { e.preventDefault(); void manage(() => client.connection.reducers.createRoom({ goal: tripName.trim(), displayName: name.trim() })); }}>
          <label>Trip name<input value={tripName} onChange={e => setTripName(e.target.value)} required disabled={disabled} /></label>
          <button disabled={disabled || !name.trim() || !tripName.trim()}>Create trip</button>
        </form>
        <form onSubmit={e => { e.preventDefault(); void manage(() => client.connection.reducers.joinRoom({ inviteCode: invite.trim(), displayName: name.trim() })); }}>
          <label>Invite code<input value={invite} onChange={e => setInvite(e.target.value)} required disabled={disabled} /></label>
          <button disabled={disabled || !name.trim() || !invite.trim()}>Join trip</button>
        </form>
      </details>
      {(error || state.error) && <p role="alert" className="notice">{error || state.error}</p>}
      {room ? <TripOverview room={room} tables={tables} connected={ready} /> : <div className="trip-placeholder"><p className="eyebrow">ROOM TO EXPLORE</p><h1>A plan starts with a conversation.</h1><p>Select a trip, or create one and tell your agent what you have in mind.</p></div>}
      <p className="connection"><span className={`dot ${ready ? "online" : ""}`} />{ready ? "Connected" : "Synchronizing your trip…"}</p>
    </aside>
    {room ? setupRoom === String(room.id) ? <AgentSetup key={String(room.id)} room={room} tables={tables} connected={ready} appearanceReady={state.appearanceStatus === "ready"}
      save={variant => client.connection.reducers.setAgentAppearance({ variant })}
      onComplete={() => { sessionStorage.setItem(`tandem:setup:${room.id}`, 'done'); setSetupRoom(undefined); }} />
      : <PlanningConversation key={String(room.id)} room={room} tables={tables} reducers={client.connection.reducers} connected={ready} /> : <DestinationSlideshow welcome={<AgentWelcome tables={state.tables} connected={ready} appearanceReady={state.appearanceStatus === "ready"} save={variant => client.connection.reducers.setAgentAppearance({ variant })} />} />}
  </div>;
}
