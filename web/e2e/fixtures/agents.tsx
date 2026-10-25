import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AgentSetup } from '../../components/agent-setup';
import { AgentWelcome } from '../../components/agent-companions';
import { DestinationSlideshow } from '../../components/destination-slideshow';
import { PlanningConversation, type Reducers } from '../../components/planning-room';
import type { Tables } from '../../lib/planning';

declare global { interface Window { agentsFixture: any } }
const initial: Tables = {
  my_profile: [{ displayName: 'Skylar' }], my_agent_appearance: [{ variant: 'lavender' }],
  room_members: [
    { id: 1n, roomId: 1n, agentId: 11n, displayName: 'Skylar', active: true, isMe: true, agentVariant: 'lavender' },
    { id: 2n, roomId: 1n, agentId: 12n, displayName: 'Sam', active: true, agentVariant: 'peach' },
    { id: 3n, roomId: 1n, agentId: 13n, displayName: 'Alex', active: true, agentVariant: 'yellow' },
  ],
  room_agent_appearances: [{ roomId: 1n, membershipId: 1n, variant: 'lavender' }, { roomId: 1n, membershipId: 2n, variant: 'peach' }, { roomId: 1n, membershipId: 3n, variant: 'yellow' }],
  room_agents: [{ id: 10n, roomId: 1n, kind: 'MEDIATOR' }, { id: 11n, roomId: 1n, kind: 'PERSONAL' }, { id: 12n, roomId: 1n, kind: 'PERSONAL' }],
  room_presence: [{ membershipId: 1n, roomId: 1n, online: true }, { membershipId: 2n, roomId: 1n, online: false }],
  room_agent_activity: [{ agentId: 12n, roomId: 1n, activity: 'SEARCHING' }],
  my_private_messages: [{ id: 1n, roomId: 1n, sender: 'AGENT', body: 'Hi Skylar! Tell me what you have in mind.', createdAt: 1n }],
  room_messages: [{ id: 2n, roomId: 1n, senderAgentId: 12n, utterance: 'A quiet weekend by the coast sounds lovely.', createdAt: 2n }],
};
function Fixture() {
  const [tables, setTables] = useState(initial), [room, setRoom] = useState(false), [connected, setConnected] = useState(true);
  const [setup, setSetup] = useState(false);
  const calls = window.agentsFixture?.calls || [];
  async function save(variant: string) {
    calls.push(variant);
    if (window.agentsFixture.failNext) { window.agentsFixture.failNext = false; throw new Error('Failed'); }
    setTables(t => ({ ...t, my_agent_appearance: [{ variant }], room_agent_appearances: t.room_agent_appearances.map(a => a.membershipId === 1n ? { ...a, variant } : a), room_members: t.room_members.map(m => m.isMe ? { ...m, agentVariant: variant } : m) }));
  }
  window.agentsFixture = { calls, failNext: window.agentsFixture?.failNext, setConnected,
    setSetup, setTables: (patch: Tables) => setTables(t => ({ ...t, ...patch })), setRoom };
  return <main className="planning-app"><header className="app-header"><a className="wordmark" href="#">tandem↗</a><button onClick={() => setRoom(!room)}>{room ? 'Home' : 'Enter trip'}</button></header>
    <div className="planning-shell"><aside className="overview-panel"><p className="eyebrow">ROOM TO EXPLORE</p><h1>A plan starts with a conversation.</h1><p>Your companion is with you every step of the way.</p></aside>
      {setup ? <AgentSetup room={{ id: 1n, goal: 'San Diego with friends' }} tables={tables} connected={connected} appearanceReady save={save} onComplete={() => { setSetup(false); setRoom(true); }} /> : room ? <PlanningConversation room={{ id: 1n }} tables={tables} reducers={{} as Reducers} connected={connected} /> : <DestinationSlideshow welcome={<AgentWelcome tables={connected ? tables : {}} connected={connected} save={save} />} />}
    </div></main>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
