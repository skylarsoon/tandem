"use client";
import { InvitationGate } from "../../components/invitation-gate";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { connect } from "../../lib/client";
import { PlanningConversation, TripOverview } from "../../components/planning-room";
import { AgentSetup } from "../../components/agent-setup";
import { AgentAvatar } from "../../components/agent-avatar";
import { ProductHeader, Icon } from "../../components/product-shell";
import { readTripLocation, tripLocation, type TripView } from "../../lib/trip-location";
import { scoped, readable } from "../../lib/planning";
import { myCharacter } from "../../lib/agents";
import type { Row } from "../../lib/state";

type Client = ReturnType<typeof connect>;
export default function Page() {
  const [client, setClient] = useState<Client>();
  const [error, setError] = useState("");
  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_STDB_URL, db = process.env.NEXT_PUBLIC_STDB_DB;
    if (!url || !db) { setError("We couldn’t connect to Tandem. Please try again shortly."); return; }
    try { const next = connect(url, db); setClient(next); return () => next.close(); }
    catch { setError("Could not initialize the connection. Please refresh to try again."); }
  }, []);
  return <main className="product-app planning-app">
    <a className="product-skip" href="#trip-workspace">Skip to workspace</a>
    <ProductHeader active="trips" />
    {error ? <p role="alert" className="notice">{error}</p> : client ? <InvitationGate client={client}><Dashboard client={client} /></InvitationGate> : <div className="connection-empty" role="status">Connecting to your trips…</div>}
  </main>;
}
export function Dashboard({ client }: { client: Client }) {
  const state = useSyncExternalStore(client.session.subscribe, client.session.getSnapshot, client.session.getSnapshot);
  const [roomId, setRoomId] = useState<string>();
  const [roomView, setRoomView] = useState<TripView>("personal");
  const [locationError, setLocationError] = useState("");
  const [setupRoom, setSetupRoom] = useState<string>();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [name, setName] = useState("");
  const [tripName, setTripName] = useState("");
  const [invite, setInvite] = useState("");
  const [mode, setMode] = useState<'create' | 'join'>('create');
  const dialog = useRef<HTMLDialogElement>(null);
  const awaitingRoom = useRef<Set<string> | undefined>(undefined);
  const [operation, setOperation] = useState(0);
  const rooms = state.tables.my_rooms || [];
  const ready = state.status === "ready";
  const current = rooms.find(r => String(r.id) === roomId);
  const sharedRoom = useRef<Row | undefined>(undefined);
  useEffect(() => { if (ready && current) sharedRoom.current = current; }, [ready, current]);
  const room = current || (!ready && sharedRoom.current && String(sharedRoom.current.id) === roomId ? sharedRoom.current : undefined);
  const tables = room ? scoped(state.tables, room) : {};
  const disabled = !ready || pending;
  const displayName = state.tables.my_profile?.[0]?.displayName || '';
  useEffect(() => { if (displayName) setName(current => current || displayName); }, [displayName]);
  function select(id: string, view: TripView = 'personal', updateHistory = true) {
    if (updateHistory) window.history.pushState(null, '', tripLocation(window.location.pathname, id, view));
    setRoomView(view); setLocationError('');
    setRoomId(id || undefined); sharedRoom.current = undefined; client.session.select(id);
    setSetupRoom(id && sessionStorage.getItem(`tandem:setup:${id}`) === 'pending' ? id : undefined);
  }
  useEffect(() => {
    if (!ready) return;
    const restore = () => {
      const location = readTripLocation(window.location.search);
      if (location.trip && !rooms.some(r => String(r.id) === location.trip)) {
        select('', 'personal', false);
        window.history.replaceState(null, '', window.location.pathname);
        setLocationError('This trip isn’t available to this account. Choose one of your trips or ask the organizer for an invitation.');
      } else if (location.trip !== roomId) select(location.trip || '', location.view, false);
      else setRoomView(location.view);
    };
    restore(); window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, [ready, rooms, roomId]);
  function changeView(view: TripView) {
    setRoomView(view);
    if (view !== roomView) window.history.pushState(null, '', tripLocation(window.location.pathname, roomId, view));
  }
  useEffect(() => {
    if (!ready || !awaitingRoom.current) return;
    const added = rooms.find(r => !awaitingRoom.current!.has(String(r.id)));
    if (added) {
      awaitingRoom.current = undefined;
      sessionStorage.setItem(`tandem:setup:${added.id}`, 'pending');
      select(String(added.id)); dialog.current?.close(); setTripName(''); setInvite('');
    }
  }, [rooms, ready, operation]);
  async function manage() {
    if (disabled) return;
    setPending(true); setError(""); awaitingRoom.current = new Set(rooms.map(r => String(r.id)));
    try {
      if (mode === 'create') await client.connection.reducers.createRoom({ goal: tripName.trim(), displayName: name.trim() });
      else await client.connection.reducers.joinRoom({ inviteCode: invite.trim(), displayName: name.trim() });
      setOperation(value => value + 1);
    } catch (e) { awaitingRoom.current = undefined; setError(e instanceof Error ? e.message : "Could not update your trips."); }
    finally { setPending(false); }
  }
  function open(mode: 'create' | 'join') { setMode(mode); setError(''); dialog.current?.showModal(); }
  return <>
    {locationError && <p role="alert" className="notice route-notice">{locationError}</p>}
    <div id="trip-workspace" data-has-trips={rooms.length > 0} className={room ? 'planning-shell' : 'trips-home'}>
      {room ? <>
        <aside className="overview-panel" aria-label="Trip details">
          <button className="back-to-trips" onClick={() => select('')}>← All your trips</button>
          <div className="trip-navigation"><label htmlFor="trip-select">YOUR TRIPS</label><select id="trip-select" value={roomId || ""} disabled={disabled} onChange={e => select(e.target.value)}><option value="">Home · your companion</option>{rooms.map(r => <option value={String(r.id)} key={String(r.id)}>{r.goal}</option>)}</select></div>
          <TripOverview room={room} tables={tables} connected={ready} />
          <button className="new-trip-rail" onClick={() => open('create')}><Icon name="plus" size={16}/>Create or join a trip</button>
          <p className="connection"><span className={`dot ${ready ? 'online' : ''}`}/>{ready ? 'All changes synced' : 'Reconnecting…'}</p>
        </aside>
        {setupRoom === String(room.id) ? <AgentSetup key={String(room.id)} room={room} tables={tables} connected={ready} appearanceReady={state.appearanceStatus === 'ready'} save={variant => client.connection.reducers.setAgentAppearance({ variant })} onComplete={() => { sessionStorage.setItem(`tandem:setup:${room.id}`, 'done'); setSetupRoom(undefined); }} /> : <PlanningConversation key={String(room.id)} room={room} tables={tables} reducers={client.connection.reducers} connected={ready} selectedView={roomView} onViewChange={changeView} />}
      </> : <>
        <section className="home-heading"><div><p className="eyebrow">A LITTLE COMMON GROUND</p><h1>{displayName ? `Good to see you, ${displayName}.` : 'Your next chapter starts here.'}</h1><p>Different people. Shared adventures. A plan that feels right for everyone.</p></div><span className="sync-status"><span className={`dot ${ready ? 'online' : ''}`}/>{ready ? 'Synced to your account' : 'Connecting…'}</span></section>
        <section className="home-invitation"><div className="home-invitation-copy"><span className="eyebrow">MAKE ROOM FOR A LITTLE ADVENTURE</span><h2>Somewhere new.<br/>Together.</h2><p>Bring your people and a rough idea.<br/>Your companions will help with the rest.</p><div className="home-actions"><button className="primary-action" onClick={() => open('create')}><Icon name="plus" size={18}/>Start a trip</button><button className="text-action" onClick={() => open('join')}>Join with a code <Icon name="arrow" size={18}/></button></div></div>
          <div className="home-crew-art" aria-hidden="true"><div className="home-orbit"/><span className="art-label">YOUR PEOPLE. YOUR POSSIBILITIES.</span><AgentAvatar variant="mint" size="hero"/><AgentAvatar variant={myCharacter(state.tables)} size="hero"/><AgentAvatar variant="peach" size="hero"/><span className="art-spark">✳</span></div>
        </section>
        <section className="trip-collection"><div className="section-title"><div><p className="eyebrow">YOUR SHARED ADVENTURES</p><h2>Your trips <span>{rooms.length}</span></h2></div><button className="text-action" onClick={() => open('create')}>Create or join a trip <Icon name="plus" size={18}/></button></div>
          {rooms.length ? <div className="trip-card-grid">{rooms.map((r, i) => <button className="trip-home-card" key={String(r.id)} onClick={() => select(String(r.id))}><div className={`trip-card-art trip-card-art--${i % 3}`}><span className="trip-route-line"/><Icon name="trips" size={35}/><span className="trip-card-number">TRIP / {String(i + 1).padStart(2, '0')}</span></div><div className="trip-card-copy"><span className="trip-card-status">{readable(r.status || 'Ready to explore')}</span><h3>{r.goal}</h3><span className="trip-card-open">Open trip <Icon name="arrow" size={18}/></span></div></button>)}</div> : <div className="first-trip"><Icon name="trips" size={28}/><div><h3>A little space for your next big idea.</h3><p>Your trips will live here, from the first “what if” to the final yes.</p></div><button onClick={() => open('create')}>Create your first trip →</button></div>}
        </section>
        <a className="home-profile-link" href="/profile"><AgentAvatar variant={myCharacter(state.tables)} size="message" animated={false}/><div><strong>Your companion gets better at being yours.</strong><span>Update your travel style, preferences, and the little things that matter.</span></div><Icon name="arrow"/></a>
      </>}
    </div>
    {state.error && <p role="alert" className="notice">{state.error}</p>}
    <dialog ref={dialog} className="trip-dialog" aria-labelledby="trip-dialog-title" onCancel={e => { if (pending) e.preventDefault(); }} onClick={e => { if (e.target === e.currentTarget && !pending) dialog.current?.close(); }}>
      <button className="dialog-close" aria-label="Close trip form" disabled={pending} onClick={() => dialog.current?.close()}><Icon name="close"/></button>
      <div className="dialog-art"><AgentAvatar variant={myCharacter(state.tables)} size="hero" animated={false}/><span className="eyebrow">GOOD PLANS START WITH GOOD COMPANY</span></div>
      <div className="dialog-content"><div className="segmented" aria-label="Create or join"><button aria-pressed={mode === 'create'} disabled={pending} onClick={() => { setMode('create'); setError(''); }}>New trip</button><button aria-pressed={mode === 'join'} disabled={pending} onClick={() => { setMode('join'); setError(''); }}>Join a trip</button></div>
        <h2 id="trip-dialog-title">{mode === 'create' ? 'What shall we call it?' : 'Your people are waiting.'}</h2><p>{mode === 'create' ? 'Just a name for now. You’ll work out the details with your companion.' : 'Use the trip code shared by your organizer.'}</p>
        <form onSubmit={e => { e.preventDefault(); void manage(); }}><label>Your display name<input autoComplete="nickname" value={name} onChange={e => setName(e.target.value)} required disabled={disabled} maxLength={50}/></label>
          {mode === 'create' ? <label>Trip name<input placeholder="A weekend by the coast" value={tripName} onChange={e => setTripName(e.target.value)} required disabled={disabled} maxLength={200}/></label> : <label>Invite code<input autoCapitalize="none" spellCheck={false} placeholder="Paste your trip code" value={invite} onChange={e => setInvite(e.target.value)} required disabled={disabled}/></label>}
          {error && <p role="alert" className="notice">{error}</p>}
          <button className="primary-action" disabled={disabled || !name.trim() || !(mode === 'create' ? tripName.trim() : invite.trim())}>{pending ? 'One moment…' : mode === 'create' ? 'Create trip' : 'Join trip'}<Icon name="arrow" size={18}/></button>
        </form><small className="dialog-note"><Icon name="lock" size={14}/>Your preferences stay private. Your trip is shared.</small>
      </div>
    </dialog>
  </>;
}
