'use client';
import { useEffect, useState } from 'react';
import { ageLabel, categories, liveActivity, researchPhases, stageLabels } from '../lib/activity';
import type { Row } from '../lib/state';
import styles from './live-activity.module.css';

export function LiveActivity({ room, tables, connected }: { room: Row; tables: Record<string, Row[]>; connected: boolean }) {
  const [now, setNow] = useState(Date.now), [open, setOpen] = useState(false);
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 5000); return () => clearInterval(id); }, []);
  const [online, setOnline] = useState(true);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine); update();
    window.addEventListener('online', update); window.addEventListener('offline', update);
    return () => { window.removeEventListener('online', update); window.removeEventListener('offline', update); };
  }, []);
  const model = liveActivity(room, tables, connected && online, now);
  const time = (value: number) => ageLabel(value, now);
  return <section className={styles.activity} aria-label="Live agent activity" data-tone={model.tone}>
    <button type="button" className={styles.summary} aria-expanded={open} onClick={() => setOpen(!open)}>
      <span className={styles.indicator} aria-hidden="true" /><span className={styles.heading}><small>BEHIND THE PLAN</small><strong aria-live="polite">{model.title}</strong></span>
      <span className={styles.updated}>{model.latestTime ? time(model.latestTime) : 'Waiting'}</span><span className={styles.toggle}>{open ? 'Hide activity −' : 'View activity +'}</span>
    </button>
    {!open && model.runs.length > 0 && <div className={styles.preview}>{model.runs.slice(-3).map(run => <span key={`${run.round}:${run.search_ref}`}>{run.search.reported ? run.search.title : categories[run.category]} <b>·</b> {run.label}</span>)}{model.runs.length > 3 && <span>+{model.runs.length - 3} more</span>}</div>}
    {open && <div className={styles.body}>
      <p className={styles.description}>{model.detail}</p>

      <div className={styles.sectionTitle}><h3>Search activity</h3><span>Apify · {model.runs.length} {model.runs.length === 1 ? 'search' : 'searches'} reported</span></div>
      {!model.runs.length ? <p className={styles.empty}>No actor run reported for this planning attempt. Agents may still be gathering details or using previously saved options.</p> : <ul className={styles.runs}>{model.runs.map(run => <li key={`${run.round}:${run.search_ref}`} data-state={run.phase}>
        <div className={styles.runTop}><span className={styles.runIcon} aria-hidden="true">{run.phase === 'COMPLETED' || run.phase === 'CACHED' ? '✓' : run.phase === 'FAILED' ? '!' : '↗'}</span><div><strong>{run.search.title} <small>{categories[run.category]} · {run.search.facts.find(([name]) => name === 'Leg')?.[1] || `Search ${run.number}`}</small></strong><p>{run.label}</p></div><span className={styles.time}>{time(run.time)}</span></div>
        {run.search.reported ? <dl className={styles.searchFacts}>{run.search.facts.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl> : <p className={styles.empty}>This run did not report its search details. They are not reconstructed from your current trip settings.</p>}
        {run.live && <div className={styles.runTrack} aria-label={run.phase === 'QUEUED' ? 'Queued' : 'Provider update received'}><span/><small>{run.phase === 'QUEUED' ? 'Waiting for a worker slot' : 'Following provider updates · no estimated completion time'}</small></div>}
        {run.count !== undefined && <p className={styles.result}>{run.count === 0 ? 'No usable options returned' : `${run.count} usable ${run.count === 1 ? 'option' : 'options'} returned`}{run.phase === 'CACHED' ? ' · no new actor run' : ''}</p>}
        {(run.excluded > 0 || run.duplicates > 0) && <p className={styles.empty}>{Number.isInteger(run.excluded) && run.excluded > 0 ? `${run.excluded} incomplete or mismatched results excluded. ` : ''}{Number.isInteger(run.duplicates) && run.duplicates > 0 ? `${run.duplicates} duplicates removed.` : ''}</p>}
        {run.truncated === true && <p className={styles.empty}>The collection limit was reached. These counts cover the collected results, not every result at the provider.</p>}
        {run.detail && <p className={styles.error}>{run.detail}</p>}
        {run.stale && <p className={styles.empty}>Last reported: {researchPhases[run.phase]}. We can’t confirm that work is still progressing.</p>}
        <details className={styles.source}><summary>Source details</summary><dl><dt>Provider</dt><dd>Apify</dd><dt>Actor</dt><dd>{typeof run.actor_id === 'string' ? run.actor_id : 'Not reported'}</dd><dt>Run</dt><dd>{typeof run.run_ref === 'string' && /^[A-Za-z0-9_-]+$/.test(run.run_ref) ? <a href={`https://console.apify.com/actors/runs/${encodeURIComponent(run.run_ref)}`} target="_blank" rel="noreferrer">Open provider run ↗</a> : 'Not accepted yet'}</dd><dt>Status checks</dt><dd>{run.polls || 0}</dd><dt>Search pass</dt><dd>{Number(run.round || 0) + 1}</dd></dl></details>
      </li>)}</ul>}
      <details className={styles.history}><summary>Planning stages</summary><ol className={styles.stages} aria-label="Planning stages">{(['LOAD','RESEARCH','SOLVE','AGENTS','VALIDATE','PUBLISH'] as const).map(stage => <li key={stage} data-state={model.graph.states[stage]}><span aria-hidden="true">{model.graph.states[stage] === 'completed' ? '✓' : '·'}</span><span>{stageLabels[stage]}<small>{model.graph.states[stage] === 'active' && model.tone !== 'live' ? 'Last reported' : model.graph.states[stage]}</small></span></li>)}</ol></details>
      {!!model.agents.length && <><h3>Your crew</h3><ul className={styles.agents}>{model.agents.map(agent => <li key={agent.id}><strong>{agent.name}</strong><span>{agent.label}{!agent.recent ? ' · last reported' : ''}</span></li>)}</ul></>}
      <details className={styles.history}><summary>Activity timeline · {model.events.length} updates</summary><ol>{model.events.slice(-80).reverse().map(e => <li key={String(e.eventId)}><span>{e.type === 'RESEARCH_ACTIVITY' ? `${categories[e.category]} · ${researchPhases[e.phase]}` : `${stageLabels[e.stage]} · ${e.phase.toLowerCase()}`}</span><time dateTime={e.time ? new Date(e.time).toISOString() : undefined}>{time(e.time)}</time></li>)}</ol></details>
      {!!model.earlier.length && <details className={styles.history}><summary>Earlier planning attempts · {model.earlier.length} updates</summary><p>These updates belong to older trip details or planning attempts.</p><ol>{model.earlier.slice(-30).reverse().map(e => <li key={String(e.eventId)}><span>{e.type === 'RESEARCH_ACTIVITY' ? `${categories[e.category]} · ${researchPhases[e.phase]}` : `${stageLabels[e.stage]} · ${e.phase.toLowerCase()}`}</span><time>{time(e.time)}</time></li>)}</ol></details>}
      <p className={styles.privacy}>Shared progress only. Private answers and conversations stay with their traveller.</p>
    </div>}
  </section>;
}
