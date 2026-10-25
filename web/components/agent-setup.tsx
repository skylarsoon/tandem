"use client";
import { useEffect, useState } from 'react';
import { CompanionPortrait } from './companion-portrait';
import { AgentAvatar } from './agent-avatar';
import { characters, myCharacter, type Character } from '../lib/agents';
import type { Tables } from '../lib/planning';
import type { Row } from '../lib/state';

export function AgentSetup({ room, tables, connected, appearanceReady, save, onComplete }: {
  room: Row; tables: Tables; connected: boolean; appearanceReady: boolean;
  save: (variant: Character) => Promise<unknown>; onComplete: () => void;
}) {
  const [selected, setSelected] = useState<Character>(() => myCharacter(tables));
  const [chosen, setChosen] = useState(false);
  useEffect(() => { if (!chosen && appearanceReady) setSelected(myCharacter(tables)); }, [chosen, appearanceReady, tables]);
  const [pending, setPending] = useState(false), [error, setError] = useState('');
  async function finish() {
    if (pending || !connected) return;
    setPending(true); setError('');
    try { if (appearanceReady) await save(selected); onComplete(); }
    catch { setError('Your companion could not be saved. Please try again.'); }
    finally { setPending(false); }
  }
  return <section className="agent-setup" aria-label="Set up your companion">
    <ol className="setup-steps"><li>01 · Trip created</li><li aria-current="step">02 · Your companion</li><li>03 · Your people</li></ol>
    <div className="setup-intro"><p className="eyebrow">A LITTLE HELP, JUST FOR YOU</p><h2>Meet your travel companion.</h2><p>One little agent, looking out for what matters to you.</p></div>
    <div className="setup-main">
      <div className="setup-character"><CompanionPortrait variant={selected} connected={connected} /></div>
      <div className="setup-copy"><p className="eyebrow">YOUR VOICE IN EVERY PLAN</p><h3>We’ll find your kind<br />of common ground.</h3>
        <ul><li><strong>Tell me privately.</strong> Your budget, preferences, and reasons stay in your space.</li><li><strong>I’ll work with the others.</strong> Each traveller’s companion brings their needs to the group.</li><li><strong>You get the final say.</strong> I suggest a plan. You decide whether it works for you.</li></ul>
        {appearanceReady && <fieldset className="setup-choices" disabled={!connected || pending}><legend>Choose your companion</legend>
          {characters.map(c => <button type="button" key={c.id} aria-label={`Choose ${c.name}`} aria-pressed={selected === c.id} onClick={() => { setChosen(true); setSelected(c.id); }}><AgentAvatar variant={c.id} size="picker" animated={false} /><span>{c.name}</span></button>)}
        </fieldset>}
        {!appearanceReady && <p className="muted">Your companion is ready.</p>}
        {error && <p role="alert" className="notice">{error}</p>}
        <button type="button" className="setup-continue" disabled={!connected || pending} onClick={() => void finish()}>{pending ? 'Getting ready…' : 'Enter the group room →'}</button>
        <p className="setup-trip-name">Joining <strong>{room.goal}</strong></p>
      </div>
    </div>
  </section>;
}
