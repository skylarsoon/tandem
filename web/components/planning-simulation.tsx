'use client';

import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { connect } from '../lib/client';
import { InvitationGate } from './invitation-gate';
import { AgentAvatar } from './agent-avatar';
import { ProductHeader, Icon } from './product-shell';
import { activityLabels, type AgentActivity } from '../lib/agents';
import { ageLabel, researchPhases, searchDetails, stageLabels, timestamp } from '../lib/activity';
import { money, questionAction, recoveryAction, type Row } from '../lib/state';
import { readable } from '../lib/planning';
import { optionTitle, optionFacts, planPriceSummary } from '../lib/plan-presentation';
import styles from './planning-simulation.module.css';

type Client = ReturnType<typeof connect>;
type Run = { roomId: bigint; state: string; snapshotJson: string };
type Member = { id: string; agentId: string; name: string; color: string; origin: string; budget: number; note: string; activity?: string; updatedAt?: unknown };
type Event = { id: string; type: string; payload: Row; createdAt: unknown };
type Question = { id: string; memberId: string; kind: string; prompt: string; version: number; action: Row };
type Proposal = { id: string; version: number; state: string; termsDigest: string; terms: Row; summary: Row; consents: {memberId:string;response:string}[]; impacts:{memberId:string;impact:Row}[] };
type Snapshot = { settings: Row; roomStatus: string; retries: number; members: Member[]; mediatorId: string; job: {id:string;status:string;outcome?:string} | null; events: Event[]; messages: {id:string;senderAgentId:string;recipientAgentId:string;utterance:string;createdAt:unknown}[]; questions: Question[]; proposals: Proposal[] };
const dateAfter = (days:number) => new Date(Date.now()+days*86400000).toISOString().slice(0,10);
const colors = ['lavender','peach','mint','yellow'];
const retryable = new Set(['APIFY_POLL_DEADLINE_EXCEEDED','APIFY_POLL_BUDGET_EXHAUSTED','APIFY_RATE_LIMITED','APIFY_REQUEST_REFUSED','APIFY_REQUEST_FAILED','APIFY_CONNECTION_FAILED','APIFY_INVALID_RESPONSE','APIFY_RUN_FAILED','EXECUTION_FAILED','INVALID_RESEARCH_REQUEST','ACTOR_RUN_BUDGET_EXHAUSTED','MODEL_CALL_BUDGET_EXHAUSTED','EVIDENCE_EXPIRED','WAITING_FOR_INTAKE']);
const friendlyErrors:Record<string,string> = {
  SIMULATION_DAILY_LIMIT:'You already started a simulation today. Reopen your saved run below; a new slot opens at midnight UTC.',
  SIMULATION_CAPACITY:'Today’s shared simulation slots are full. Please return after midnight UTC.',
  SIMULATION_RETRY_LIMIT:'This run has used its two retries. Its searches and conversation are still saved.',
  JOB_NOT_ELIGIBLE:'This run cannot be retried in its current state. Check the latest activity below.',
};

export function PlanningSimulation() {
  const [client,setClient]=useState<Client>();
  const [error,setError]=useState('');
  useEffect(()=>{
    const url=process.env.NEXT_PUBLIC_STDB_URL,db=process.env.NEXT_PUBLIC_STDB_DB;
    if(!url || !db) {setError('We couldn’t connect to Tandem. Please try again shortly.');return;}
    try {const c=connect(url,db);setClient(c);return ()=>c.close();} catch {setError('Could not connect. Please refresh to try again.');}
  },[]);
  return <main className="product-app"><ProductHeader active="simulation"/><div className={styles.page}>{error?<p role="alert">{error}</p>:client?<InvitationGate client={client}><Workspace client={client}/></InvitationGate>:<p role="status">Opening the planning studio…</p>}</div></main>;
}

function Workspace({client}:{client:Client}) {
  const state=useSyncExternalStore(client.session.subscribe,client.session.getSnapshot,client.session.getSnapshot);
  const connected=state.status==='ready';
  const [runs,setRuns]=useState<Run[]>([]),[loaded,setLoaded]=useState(false),[selected,setSelected]=useState('');
  const [start,setStart]=useState(()=>dateAfter(25)),[end,setEnd]=useState(()=>dateAfter(28));
  const [pending,setPending]=useState(false),[error,setError]=useState(''),[subscriptionError,setSubscriptionError]=useState('');
  const request=useRef<string | undefined>(undefined),retryRequest=useRef<{job:string;key:string}|undefined>(undefined);
  const [now,setNow]=useState(()=>Date.now());
  const feed=useRef<HTMLDivElement>(null),follow=useRef(true);
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),5000);return ()=>clearInterval(timer);},[]);
  useEffect(()=>{
    if(!connected)return;
    let live=true;
    setLoaded(false);setSubscriptionError('');
    const table=client.connection.db.myPlanningSimulations;
    const refresh=()=>{if(live)setRuns([...table.iter()].sort((a,b)=>a.roomId>b.roomId?-1:1));};
    const update=()=>queueMicrotask(refresh);
    table.onInsert(update);table.onUpdate(update);table.onDelete(update);
    const sub=client.connection.subscriptionBuilder().onApplied(()=>{if(live){refresh();setLoaded(true);}}).onError(()=>{if(live)setSubscriptionError('Could not load your simulations. Refresh to reconnect.');}).subscribe('SELECT * FROM my_planning_simulations');
    return ()=>{live=false;sub.unsubscribe();table.removeOnInsert(update);table.removeOnUpdate(update);table.removeOnDelete(update);};
  },[client,connected]);
  const run=runs.find(r=>String(r.roomId)===selected) || (!selected?runs[0]:undefined);
  const snapshot:Snapshot | undefined=run?JSON.parse(run.snapshotJson):undefined;
  useEffect(()=>{if(follow.current && feed.current)feed.current.scrollTo({top:feed.current.scrollHeight,behavior:'smooth'});},[snapshot?.messages.length,run?.roomId]);
  async function act(fn:()=>Promise<unknown>) {
    if(pending || !connected)return;
    setPending(true);setError('');
    try {await fn();return true;} catch(e) {
      const text=e instanceof Error?e.message:String(e);
      setError(Object.entries(friendlyErrors).find(([code])=>text.includes(code))?.[1] || text);
      return false;
    } finally {setPending(false);}
  }
  const disabled=pending || !connected || !loaded;
  const latest=snapshot?.events.filter(e=>e.type==='WORKFLOW_PROGRESS' && String(e.payload.job_ref)===snapshot?.job?.id).at(-1);
  const terminal=run?.state==='STOPPED' || ['FAILED','FINISHED','SUPERSEDED'].includes(snapshot?.job?.status || '');
  const searches=new Map<string,Event>();
  snapshot?.events.filter(e=>e.type==='RESEARCH_ACTIVITY' && String(e.payload.job_ref)===snapshot?.job?.id).forEach(e=>searches.set(`${e.payload.round}:${e.payload.search_ref}`,e));
  const capped=['ACTOR_RUN_BUDGET_EXHAUSTED','MODEL_CALL_BUDGET_EXHAUSTED'].includes(snapshot?.job?.outcome || '');
  const canRetry=run?.state==='ACTIVE' && ['FAILED','FINISHED'].includes(snapshot?.job?.status || '') && retryable.has(snapshot?.job?.outcome || '') && (snapshot?.retries || 0)<2;
  const title=!connected?'Reconnecting · last saved activity':run?.state==='STOPPED'?'Simulation stopped':capped?'Daily provider allowance reached':snapshot?.questions.length?'A traveller needs your decision':snapshot?.proposals.some(p=>p.state==='AGREED')?'The demo travellers have agreed':snapshot?.proposals.some(p=>p.state==='AWAITING_REVIEW')?'A real proposal, ready to review':snapshot?.job?.status==='PENDING'?'Queued for the planning worker':snapshot?.job?.status==='FAILED'?'Planning needs attention':snapshot?.job?.status==='FINISHED'?'Planning attempt finished':latest && Date.now()-timestamp(latest.createdAt)<45000?(stageLabels[latest.payload.stage] || 'Worker update received'):'Waiting for a fresh worker update';
  function speaker(id:string) {return snapshot?.members.find(m=>m.agentId===id);}
  return <>
    <header className={styles.heading}><div><p className={styles.eyebrow}>THE PLANNING STUDIO / LIVE SIMULATION</p><h1>Four little minds.<br/><span>One shared adventure.</span></h1><p>Meet the agents behind the plan. Watch real searches, follow the discussion, and help the demo travellers make a decision.</p></div><a href="/app">Your real trips <Icon name="arrow" size={17}/></a></header>
    {!run && <section className={styles.setup}>
      <div className={styles.setupArt}><div className={styles.crewPreview}>{colors.map(color=><AgentAvatar key={color} variant={color} size="room"/>)}</div><span className={styles.eyebrow}>FOUR STARTING POINTS. SAN DIEGO TOGETHER.</span><h2>A front-row seat<br/>to finding common ground.</h2><p>Four fictional travellers bring different budgets and connection preferences. Their agents use the same search and negotiation system as your real trips.</p><div className={styles.tags}><span>SFO</span><span>LAX</span><span>SEA</span><span>PDX</span><Icon name="arrow"/><strong>SAN</strong></div></div>
      <form className={styles.setupForm} onSubmit={e=>{e.preventDefault();request.current??=crypto.randomUUID();void act(async()=>{await client.connection.reducers.startPlanningSimulation({startDate:start,endDate:end,requestKey:request.current!});setSelected('');request.current=undefined;});}}>
        <p className={styles.eyebrow}>YOUR DEMO GETAWAY</p><h2>Let’s get them going.</h2><p>This simulation plans round-trip flights. No bookings, payments, or changes to your own profile.</p><div className={styles.dates}><label>Departure<input type="date" value={start} min={dateAfter(8)} max={dateAfter(180)} onChange={e=>{setStart(e.target.value);request.current=undefined;}} required disabled={disabled}/></label><label>Return<input type="date" value={end} min={start} max={start?new Date(Date.parse(start+'T00:00:00Z')+7*86400000).toISOString().slice(0,10):undefined} onChange={e=>{setEnd(e.target.value);request.current=undefined;}} required disabled={disabled}/></label></div>
        <button className={styles.primary} disabled={disabled || Date.parse(end)<=Date.parse(start)}>{pending?'Creating your simulation…':'Start live planning'}<Icon name="arrow" size={18}/></button><small>One new run per account each UTC day, with three shared daily slots. Runs use Tandem’s existing provider allowance. Results and timing depend on live providers.</small>
      </form>
    </section>}
    {subscriptionError && <p className={styles.error} role="alert">{subscriptionError}</p>}{error && <p className={styles.error} role="alert">{error}</p>}
    {run && snapshot && <>
      <div className={styles.runBar}><div><span className={styles.eyebrow}>SAN DIEGO / ROUND-TRIP FLIGHTS</span><strong>{snapshot.settings.start_date} — {snapshot.settings.end_date}</strong></div><div className={styles.actions}>{runs.length>1 && <select aria-label="Saved simulations" value={String(run.roomId)} onChange={e=>{setSelected(e.target.value);follow.current=true;}}>{runs.map(r=><option key={String(r.roomId)} value={String(r.roomId)}>Simulation {String(r.roomId)} · {r.state.toLowerCase()}</option>)}</select>}<button disabled={disabled} onClick={()=>setSelected('new')}>New simulation</button>{run.state==='ACTIVE' && !terminal && <button disabled={disabled} onClick={()=>{if(window.confirm('Stop this simulation? Its history stays saved. A search already running may still finish.'))void act(()=>client.connection.reducers.stopPlanningSimulation({roomId:run.roomId}));}}>Stop run</button>}</div></div>
      <div className={styles.status} role="status"><span className={styles.statusDot} data-active={connected && !terminal}/><div><strong>{title}</strong><p>{capped?'Your run is saved. The allowance resets at midnight UTC; retry afterwards.':run.state==='STOPPED'?'The worker has been asked to stop. All recorded messages and searches remain here.':snapshot.job?.outcome && terminal?readable(snapshot.job.outcome):'Updates arrive directly from the worker. Quiet periods mean no new message has been reported.'}</p></div>{canRetry && <button disabled={disabled} onClick={()=>void act(()=>{if(retryRequest.current?.job!==snapshot.job!.id)retryRequest.current={job:snapshot.job!.id,key:crypto.randomUUID()};return client.connection.reducers.retryPlanningSimulation({roomId:run.roomId,jobId:BigInt(snapshot.job!.id),requestKey:retryRequest.current.key});})}>Retry planning</button>}</div>
      <section className={styles.crew} aria-label="Your four demo agents">{snapshot.members.map(m=>{
        const fresh=connected && !terminal && now-timestamp(m.updatedAt)<45000;
        const activity=fresh && m.activity && Object.hasOwn(activityLabels,m.activity)?m.activity as AgentActivity:'IDLE';
        return <article key={m.id} className={styles.member} data-color={m.color}><AgentAvatar variant={m.color} size="room" activity={activity}/><div><h2>{m.name}</h2><span>{m.origin} <Icon name="arrow" size={12}/> SAN</span><strong>{money(m.budget)} budget</strong><small>{m.note}</small><p>{fresh?activityLabels[activity]:run.state==='STOPPED'?'Stopped':terminal?'Last attempt ended':'Awaiting an update'}</p></div></article>;
      })}</section>
      <div className={styles.workspace}>
        <section className={styles.discussion} aria-labelledby="discussion-title"><div className={styles.panelHead}><div><p className={styles.eyebrow}>THE CONVERSATION</p><h2 id="discussion-title">Around the table.</h2></div><span>{snapshot.messages.length} messages</span></div><div className={styles.feed} ref={feed} onScroll={()=>{const el=feed.current;if(el)follow.current=el.scrollHeight-el.scrollTop-el.clientHeight<90;}}>
          {snapshot.messages.length?snapshot.messages.map(message=>{const m=speaker(message.senderAgentId),to=speaker(message.recipientAgentId),mediator=message.senderAgentId===snapshot.mediatorId;return <article key={message.id} className={styles.message} data-mediator={mediator}><AgentAvatar variant={m?.color || 'mint'} mediator={mediator} size="message"/><div><div className={styles.messageHead}><strong>{m?`${m.name}’s Pip`:mediator?'Mediator':'Agent'}</strong><small>{to?`to ${to.name}’s Pip`:message.recipientAgentId===snapshot.mediatorId?'to Mediator':'to the group'}</small></div><p>{message.utterance}</p><time>{ageLabel(timestamp(message.createdAt),now)}</time></div></article>;}):<div className={styles.empty}><Icon name="chat" size={30}/><h3>The discussion will appear here.</h3><p>The agents first need search evidence to discuss. You’ll see their actual messages as the backend publishes them.</p></div>}
        </div><div className={styles.panelFoot}><span>Actual shared agent messages · no scripted dialogue</span><button onClick={()=>{follow.current=true;feed.current?.scrollTo({top:feed.current.scrollHeight,behavior:'smooth'});}}>Latest <Icon name="arrow" size={14}/></button></div></section>
        <aside className={styles.research} aria-labelledby="research-title"><div className={styles.panelHead}><div><p className={styles.eyebrow}>BEHIND THE SEARCH</p><h2 id="research-title">Show your work.</h2></div><Icon name="plan"/></div><div className={styles.searchList}>{searches.size?[...searches.values()].map(event=>{const p=event.payload,detail=searchDetails(p.category,p.search_context),stale=!['COMPLETED','CACHED','FAILED'].includes(p.phase) && (!connected || terminal || now-timestamp(event.createdAt)>45000);return <article className={styles.search} key={`${p.round}:${p.search_ref}`}><span className={styles.searchStatus} data-failed={p.phase==='FAILED'}>{stale?(terminal?(p.phase==='QUEUED'?'Not started · run ended':'Run ended · last reported'):'Last reported · awaiting update'):researchPhases[p.phase] || readable(p.phase)}</span><h3>{detail.title}</h3><dl>{detail.facts.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>{Number.isInteger(p.result_count) && <p>{p.result_count} results collected</p>}{p.error_code && <p className={styles.error}>{readable(p.error_code)}</p>}<details><summary>Source & update</summary><p>Apify · {p.actor_id || 'Actor not reported'}</p><small>{ageLabel(timestamp(event.createdAt),now)} · {p.polls || 0} status checks</small></details></article>;}):<div className={styles.empty}><Icon name="trips" size={28}/><p>No search activity has been reported for this attempt yet.</p></div>}</div></aside>
      </div>
      {snapshot.questions.length>0 && <section className={styles.decisions}><p className={styles.eyebrow}>YOUR TURN / FICTIONAL TRAVELLERS</p><h2>Help them choose the next step.</h2><p>You’re answering as a demo traveller. Your real preferences stay untouched.</p>{snapshot.questions.map(q=><QuestionCard key={q.id} question={q} name={snapshot.members.find(m=>m.id===q.memberId)?.name || 'Demo traveller'} disabled={disabled || run.state!=='ACTIVE'} answer={answerJson=>act(()=>client.connection.reducers.answerSimulationQuestion({roomId:run.roomId,questionId:BigInt(q.id),version:q.version,answerJson}))}/>)}</section>}
      {snapshot.proposals.map(p=><ProposalCard key={p.id} proposal={p} members={snapshot.members} disabled={disabled || run.state!=='ACTIVE'} review={(memberId,response)=>act(()=>client.connection.reducers.reviewSimulationProposal({roomId:run.roomId,memberId:BigInt(memberId),proposalId:BigInt(p.id),version:p.version,termsDigest:p.termsDigest,response}))}/>)}
      <p className={styles.footnote}><Icon name="lock" size={15}/>This is your private simulation. Fictional traveller presets; real searches and agent work. No tickets are booked.</p>
    </>}
    {!run && runs.length>0 && <section className={styles.saved}><h2>Pick up where you left off.</h2>{runs.map(r=><button key={String(r.roomId)} onClick={()=>setSelected(String(r.roomId))}>Open simulation {String(r.roomId)}<Icon name="arrow" size={16}/></button>)}</section>}
  </>;
}

function QuestionCard({question:q,name,disabled,answer}:{question:Question;name:string;disabled:boolean;answer:(json:string)=>Promise<unknown>}) {
  const row={...q,id:BigInt(q.id),state:'OPEN',actionJson:JSON.stringify(q.action)};
  const choices=Array.isArray(q.action.choices)?q.action.choices:[];
  return <article className={styles.question}><strong>{name}’s Pip asks</strong><p>{q.prompt}</p><div className={styles.actions}>{q.kind==='PLAN_RECOVERY'?choices.map((choice:Row)=><button key={choice.id} disabled={disabled} onClick={()=>void answer(recoveryAction(row,choice.id).answerJson)}>{choice.label || choice.title || readable(choice.id)}</button>):['MISSING_FACT','OBJECTION_DISCLOSURE','CONCESSION_CONSULTATION','BROADER_EXPLORATION'].includes(q.kind)?<><button disabled={disabled} onClick={()=>void answer(questionAction(row,true,{}).answerJson)}>Yes, for {name}</button><button disabled={disabled} onClick={()=>void answer(questionAction(row,false,{}).answerJson)}>No, for {name}</button></>:<p>This question needs a supported response before planning can continue.</p>}</div></article>;
}
function Source({url}:{url:unknown}) {try {const parsed=new URL(String(url));return ['http:','https:'].includes(parsed.protocol)?<a href={parsed.href} target="_blank" rel="noopener noreferrer">View flight source <Icon name="arrow" size={14}/></a>:null;}catch{return null;}}
function ProposalCard({proposal:p,members,disabled,review}:{proposal:Proposal;members:Member[];disabled:boolean;review:(memberId:string,response:string)=>Promise<unknown>}) {
  const selected:Row[]=Array.isArray(p.terms.selected)?p.terms.selected:[];
  const [priceLabel,priceValue]=planPriceSummary(selected);
  return <section className={styles.proposal}><div className={styles.panelHead}><div><p className={styles.eyebrow}>THE PROPOSAL / VERSION {p.version}</p><h2>A plan to consider.</h2></div><span>{readable(p.state)}</span></div><div className={styles.proposalBody}>{p.summary.text && <p>{p.summary.text}</p>}<div className={styles.total}><span>{priceLabel}</span><strong>{priceValue}</strong><small>Not booked</small></div><div className={styles.options}>{selected.map((option,index)=><article key={option.id || index}><h3>{optionTitle(option)}</h3><strong>{money(option.total_cents)}</strong><dl>{optionFacts(option).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl><Source url={option.source_url}/>{option.return_source_url && <Source url={option.return_source_url}/>}</article>)}</div><h3>Review for each demo traveller.</h3><p>These buttons record a decision for the fictional travellers only. Acceptance applies to this exact version.</p><div className={styles.reviews}>{members.map(m=>{const consent=p.consents.find(c=>c.memberId===m.id),impact=p.impacts.find(i=>i.memberId===m.id);return <article key={m.id}><strong>{m.name}</strong>{impact?.impact.text && <p>{impact.impact.text}</p>}<span>{readable(consent?.response || 'Not reviewed')}</span>{p.state==='AWAITING_REVIEW' && <div className={styles.actions}><button disabled={disabled || consent?.response==='ACCEPTED'} onClick={()=>void review(m.id,'ACCEPT')}>Accept for {m.name}</button><button disabled={disabled} onClick={()=>void review(m.id,'NOT_COMFORTABLE')}>Not comfortable</button></div>}</article>;})}</div></div></section>;
}
