'use client';
import { useState } from 'react';
import type { Reducers } from './planning-room';
import type { Tables } from '../lib/planning';
import { describeFact, constraintValue, readable } from '../lib/planning';
import { json, type Row } from '../lib/state';
import { EmptyState, Icon } from './product-shell';

export function MyLimits({ tables, disabled, reducers, edit }: { tables: Tables; disabled: boolean; reducers: Reducers; edit: () => void }) {
  const [pending, setPending] = useState<string>();
  const [error, setError] = useState('');
  const [remove, setRemove] = useState<string>();
  const rules = tables.my_rules || [];
  async function change(id: string, action: () => Promise<unknown>) {
    if (pending || disabled) return;
    setPending(id); setError('');
    try { await action(); setRemove(undefined); } catch (e) { setError(e instanceof Error ? e.message : 'Your change could not be saved. Please try again.'); }
    finally { setPending(undefined); }
  }
  return <section className="limits-view" aria-label="My private limits">
    <div className="view-intro"><span className="privacy-label"><Icon name="lock" size={14}/>ONLY YOU & YOUR COMPANION</span><h2>A little more you.</h2><p>What matters to you, in your own words. Your agent uses these confirmed details to look out for you.</p><button className="primary-action" onClick={edit}>Update with your agent <Icon name="arrow" size={17}/></button></div>
    {error && <p role="alert" className="notice">{error}</p>}
    {!rules.length && !tables.my_constraints?.length && <EmptyState title="Your needs have a home here." icon="lock">Tell your agent about your budget, must-haves, and preferences. The details you confirm will appear here.</EmptyState>}
    <div className="limit-grid">{rules.map(rule => <article className="limit-card" key={String(rule.id)} data-state={rule.state}>
      <div className="limit-heading"><span className="tag">{rule.kind === 'soft' ? 'Preference' : 'Requirement'}</span><span>{readable(rule.state)}</span></div>
      <h3>{describeFact({ rule: json<Row>(rule.ruleJson, {}) })}</h3>
      {rule.sourceQuote && <blockquote>“{rule.sourceQuote}”</blockquote>}
      {rule.legacyKey ? <><p className="muted">Confirmed in your conversation. Ask your agent to change this detail.</p><button onClick={edit}>Edit in chat →</button></> : <div className="actions">
        <button disabled={disabled || !!pending} onClick={() => void change(String(rule.id), () => reducers.setRuleState({ ruleId: rule.id, state: rule.state === 'set_aside' ? 'active' : 'set_aside' }))}>{rule.state === 'set_aside' ? 'Use for this trip' : 'Set aside for this trip'}</button>
        {rule.state !== 'kept' && <button disabled={disabled || !!pending} onClick={() => void change(String(rule.id), () => reducers.setRuleState({ ruleId: rule.id, state: 'kept' }))}>Keep this limit</button>}
        {remove === String(rule.id) ? <><span>Remove this rule?</span><button disabled={disabled || !!pending} onClick={() => void change(String(rule.id), () => reducers.removeRule({ ruleId: rule.id }))}>Confirm removal</button><button onClick={() => setRemove(undefined)}>Cancel</button></> : <button disabled={disabled || !!pending} onClick={() => setRemove(String(rule.id))}>Remove</button>}
      </div>}
    </article>)}</div>
    {!rules.length && (tables.my_constraints || []).map(c => <article className="limit-card" key={String(c.id)}><span className="tag">Confirmed detail</span><h3>{describeFact({ key: c.key, value: constraintValue(c.valueJson), unit: c.unit })}</h3><button onClick={edit}>Edit in chat →</button></article>)}
    {!!tables.my_compromises?.length && <section className="compromise-history"><h3>Your recorded decisions</h3><p>Only your own decisions are shown here.</p>{tables.my_compromises.map(c => <div key={String(c.id)}><strong>{readable(c.stage || 'Trip plan')}</strong><span>Version {c.proposalVersion} · {readable(c.state)}</span></div>)}</section>}
    <p className="limits-footnote">Changing a confirmed limit may start a new planning attempt. Existing agreed decisions remain visible in Your plan.</p>
  </section>;
}
