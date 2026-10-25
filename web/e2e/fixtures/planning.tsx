// Browser-only fixture for the real planning components. Never imported by the app.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { PlanningConversation, TripOverview, type Reducers } from "../../components/planning-room";
import type { Tables } from "../../lib/planning";

declare global { interface Window { planningFixture: any } }
const room = { id: 1n, goal: "A weekend under the stars", inputRevision: 2n, membershipRevision: 1n, permissionRevision: 1n,
  inviteCode: "CAMP2027", settingsJson: '{"destination":"Big Sur","start_date":"2027-06-04","end_date":"2027-06-06","components":["DRIVE","CAMPING"]}' };
const initial: Tables = {
  room_members: [{ id: 1n, roomId: 1n, displayName: "Sam", active: true, isMe: true, role: "ORGANIZER" }],
  my_private_messages: [{ id: 1n, roomId: 1n, sender: "HUMAN", body: "We could drive down the coast and camp for the weekend.", createdAt: 1n },
    { id: 2n, roomId: 1n, sender: "AGENT", body: "That sounds like a good starting point. I’ll keep the trip flexible and check with you before confirming any changes.", createdAt: 2n }],
  room_messages: [{ id: 1n, roomId: 1n, utterance: "We’re exploring a weekend in Big Sur. Everyone can share what matters to them with their own agent.", createdAt: 3n }],
  room_trip_brief: [{ roomId: 1n, state: "CONFIRMED", revision: 1n, briefJson: room.settingsJson }],
  my_intake: [{ id: 1n, roomId: 1n, state: "CONFIRMED", updatedAt: 4n }],
  room_events: [{ id: 1n, roomId: 1n, audience: "ROOM", type: "TRIP_BRIEF_CONFIRMED", createdAt: 4n }],
};
function Fixture() {
  const [tables, setTables] = useState<Tables>(initial), [connected, setConnected] = useState(true), [roomId, setRoomId] = useState(1n);
  const activeRoom = { ...room, id: roomId };
  const calls = window.planningFixture?.calls || [];
  const reducers = new Proxy({}, { get: (_, method) => async (args: unknown) => {
    calls.push({ method, args });
    if (window.planningFixture.failNext) { window.planningFixture.failNext = false; throw new Error("Could not send. Please retry."); }
    if (method === "submitPrivateMessage") setTables(current => ({ ...current, my_private_messages: [...current.my_private_messages || [],
      { id: BigInt(100 + calls.length), roomId, sender: "HUMAN", body: (args as any).body, createdAt: 100n }] }));
  } }) as Reducers;
  window.planningFixture = { calls, failNext: window.planningFixture?.failNext, setConnected, switchRoom: setRoomId,
    setTables: (patch: Tables) => setTables(current => ({ ...current, ...patch })) };
  return <main className="planning-app"><header className="app-header"><a className="wordmark" href="#">tandem↗</a><a href="#">Your agent profile ↗</a></header>
    <div className="planning-shell"><aside className="overview-panel" aria-label="Trip details"><TripOverview room={activeRoom} tables={tables} connected={connected} /></aside>
      <PlanningConversation key={String(roomId)} room={activeRoom} tables={tables} connected={connected} reducers={reducers} />
    </div></main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
