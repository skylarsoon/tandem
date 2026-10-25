"use client";

import { useRef, useState } from 'react';
import { AgentAvatar } from './agent-avatar';
import { activityLabels, agentActivity, characters, memberCharacter, myCharacter, type Character } from '../lib/agents';
import type { Tables } from '../lib/planning';

export function CharacterPicker({ value, connected, save }: { value: Character; connected: boolean; save: (variant: Character) => Promise<unknown> }) {
  const [pending, setPending] = useState<Character>();
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');
  const inflight = useRef(false);
  async function choose(variant: Character) {
    if (!connected || inflight.current || variant === value) return;
    inflight.current = true; setPending(variant); setError(''); setSaved('');
    try { await save(variant); setSaved('Character saved. Say hello to your agent.'); }
    catch { setError('Your character could not be saved. Please try again.'); }
    finally { inflight.current = false; setPending(undefined); }
  }
  return <div className="character-picker">
    <p className="character-picker-label">Choose your companion</p>
    <div className="character-options" role="group" aria-label="Agent character">
      {characters.map(c => <button type="button" key={c.id} aria-pressed={value === c.id} aria-label={`Choose ${c.name}`}
        disabled={!connected || !!pending} onClick={() => void choose(c.id)}>
        <AgentAvatar variant={c.id} size="picker" animated={false} />
        <span>{pending === c.id ? 'Saving…' : c.name}</span>
      </button>)}
    </div>
    {error && <p role="alert" className="notice">{error}</p>}
    <p className="character-save-status" role="status">{saved}</p>
  </div>;
}

export function AgentWelcome({ tables, connected, appearanceReady = true, save }: { tables: Tables; connected: boolean; appearanceReady?: boolean; save: (variant: Character) => Promise<unknown> }) {
  const variant = myCharacter(tables), name = tables.my_profile?.[0]?.displayName?.trim();
  const [hello, setHello] = useState(0);
  return <section className="agent-welcome" aria-label="Your personal agent">
    <button type="button" className="agent-hello-button" aria-label="Say hello to your agent" onClick={() => setHello(n => n + 1)}>
      <AgentAvatar key={`${variant}:${hello}`} variant={variant} size="hero" greeting={connected} animated={connected} />
    </button>
    <div className="agent-welcome-copy"><p className="eyebrow">YOUR LITTLE TRAVEL COMPANION</p>
      <h2>{name ? `Hey ${name},` : 'Hey there,'}<br />where are we going?</h2>
      <p>I’m your personal agent. Bring an idea, and I’ll help you and your people find a plan that fits.</p>
      <details className="character-customize"><summary>Make this agent yours</summary>
        {appearanceReady ? <CharacterPicker value={variant} connected={connected} save={save} /> : <p>Character customization is temporarily unavailable.</p>}
      </details>
    </div>
  </section>;
}

export function RoomAgents({ tables, connected }: { tables: Tables; connected: boolean }) {
  const members = (tables.room_members || []).filter(m => m.active);
  const mediator = tables.room_agents?.find(a => a.kind === 'MEDIATOR');
  return <section className="room-companions" aria-label="Agents in this trip">
    <div className="room-companions-heading"><span>YOUR PLANNING CREW</span><small>{connected ? 'A little help for everyone' : 'Reconnecting…'}</small></div>
    <ul className="room-companions-list">
      {members.map(m => {
        const activity = agentActivity(tables, m.agentId, connected);
        const online = connected && tables.room_presence?.find(p => String(p.membershipId) === String(m.id))?.online;
        return <li key={String(m.id)} className={m.isMe ? 'is-your-agent' : ''}>
          <AgentAvatar variant={m.isMe ? myCharacter(tables) : memberCharacter(tables, m)} activity={activity} />
          <strong>{m.isMe ? 'Your agent' : `${m.displayName}’s agent`}</strong>
          <span className="agent-status">{activityLabels[activity]}</span>
          <small className="agent-owner-presence"><i className={`dot ${online ? 'online' : ''}`} />{m.displayName}{m.isMe ? ' (you)' : ''} · {connected ? online ? 'online' : 'away' : 'unknown'}</small>
        </li>;
      })}
      {mediator && <li className="mediator-companion"><AgentAvatar variant="mint" mediator activity={agentActivity(tables, mediator.id, connected)} />
        <strong>Mediator</strong><span className="agent-status">{activityLabels[agentActivity(tables, mediator.id, connected)]}</span><small>Here for the whole group</small>
      </li>}
    </ul>
    {!members.length && <p className="room-companions-empty">{connected ? 'Your companions will appear when you join a trip.' : 'Your crew will return when the connection is restored.'}</p>}
  </section>;
}
