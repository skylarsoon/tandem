"use client";

import { useRef, useState } from 'react';
import { CompanionPortrait } from './companion-portrait';
import { AgentAvatar } from './agent-avatar';
import { activityLabels, agentActivity, characters, memberCharacter, myCharacter, type Character } from '../lib/agents';
import type { Row } from '../lib/state';
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
  return <section className="agent-welcome" aria-label="Your personal agent">
    <CompanionPortrait variant={variant} connected={connected} />
    <div className="agent-welcome-copy"><p className="eyebrow">YOUR LITTLE TRAVEL COMPANION</p>
      <h2>{name ? `Hey ${name},` : 'Hey there,'}<br />where are we going?</h2>
      <p>I’m your personal agent. Bring an idea, and I’ll help you and your people find a plan that fits.</p>
      <details className="character-customize"><summary>Make this agent yours</summary>
        {appearanceReady ? <CharacterPicker value={variant} connected={connected} save={save} /> : <p>Character customization is temporarily unavailable.</p>}
      </details>
    </div>
  </section>;
}

export function RoomAgents({ tables, connected, room, activeTarget, speaking }: { tables: Tables; connected: boolean; room?: Row; activeTarget?: string; speaking?: boolean }) {
  const members = (tables.room_members || []).filter(m => m.active);
  const mediator = tables.room_agents?.find(a => a.kind === 'MEDIATOR');
  const count = Math.max(4, members.length);
  const progress = tables.room_intake_progress?.[0];
  const [copied, setCopied] = useState(false);
  return <section className="room-companions" aria-label="Agents in this trip">
    <div className="room-companions-heading"><span>YOUR AGENTS, TOGETHER</span><small>{connected ? progress ? `${progress.ready} of ${progress.total} travellers ready` : 'A personal voice for everyone' : 'Reconnecting…'}</small></div>
    <div className="crew-scroll"><div className="crew-scene" style={{ minWidth: count * 95 }}>
      <svg className="crew-connections" viewBox="0 0 1000 200" preserveAspectRatio="none" aria-hidden="true">
        {members.map((m, i) => <path key={String(m.id)} d={`M ${(i + .5) / count * 1000} 45 C ${(i + .5) / count * 1000} 145, 500 125, 500 190`}
          className={connected && !['IDLE', 'DISCONNECTED', 'WAITING_FOR_OWNER'].includes(agentActivity(tables, m.agentId, connected)) ? 'is-working' : ''} />)}
      </svg>
      <ul className="room-companions-list" style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` }}>
        {members.map(m => {
          const activity = agentActivity(tables, m.agentId, connected);
          const online = connected && tables.room_presence?.find(p => String(p.membershipId) === String(m.id))?.online;
          return <li key={String(m.id)} className={m.isMe ? 'is-your-agent' : ''} data-color={m.isMe ? myCharacter(tables) : memberCharacter(tables, m)} data-speaking={speaking && activeTarget === String(m.agentId)} data-activity={activity}>
            <AgentAvatar speechTarget={String(m.agentId)} variant={m.isMe ? myCharacter(tables) : memberCharacter(tables, m)} activity={activity} />
            <strong>{m.isMe ? 'Your agent' : `${m.displayName}’s agent`}</strong>
            <span className="agent-status">{activeTarget === String(m.agentId) ? speaking ? 'Speaking' : 'Getting ready to speak' : activityLabels[activity]}</span>
            <small className="agent-owner-presence"><i className={`dot ${online ? 'online' : ''}`} />{m.displayName}{m.isMe ? ' (you)' : ''} · {connected ? online ? 'online' : 'away' : 'unknown'}</small>
          </li>;
        })}
        {connected && Array.from({ length: count - members.length }, (_, i) => <li className="crew-empty-seat" key={`empty-${i}`}><span aria-hidden="true">+</span><strong>Someone’s missing</strong><small>Invite a traveller</small></li>)}
      </ul>
      {mediator && <div className="mediator-companion crew-hub" data-speaking={speaking && activeTarget === String(mediator.id)}><AgentAvatar speechTarget={String(mediator.id)} variant="mint" size="message" animated={false} mediator activity={agentActivity(tables, mediator.id, connected)} /><div><strong>Mediator</strong><small>Bringing everyone’s needs together</small></div></div>}
    </div></div>
    {room?.inviteCode && connected && members.length < 4 && <div className="crew-invite"><span>Bring your people <code>{room.inviteCode}</code></span><button type="button" onClick={() => {
      void navigator.clipboard?.writeText(String(room.inviteCode)).then(() => setCopied(true)).catch(() => setCopied(false));
    }}>{copied ? 'Copied' : 'Copy invite code'}</button></div>}
    {connected && progress && progress.ready < progress.total && <p className="crew-readiness">Once everyone confirms their needs, your agents begin comparing options.</p>}
    {!connected && <p className="room-companions-empty">Your crew will return when the connection is restored.</p>}
  </section>;
}
