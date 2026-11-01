"use client";

import { useEffect, useRef, useState } from "react";
import type { connect } from "../lib/client";
import { json, money, proposalAction, questionAction, recoveryAction, type Row } from "../lib/state";
import { constraintValue, describeFact, established, factStrength, label, outgoingMessage, planningStatus, preferenceDefaults,
  readable, revisionEvents, revisionText, scoped, timeline, type Preferences, type Tables, type TimelineItem } from "../lib/planning";
import { LiveActivity } from "./live-activity";
import { discussionTimeline } from "../lib/discussion";
import { stages, workflow } from "../lib/workflow";
import { AgentAvatar } from "./agent-avatar";
import { AgentVoice, type VoicePlayback } from './agent-voice';
import { RoomAgents } from "./agent-companions";
import { MyLimits } from "./my-limits";
import { Icon, EmptyState } from "./product-shell";
import { tripPhase, phaseLabels, proposalGroups, optionTitle, optionFacts } from "../lib/plan-presentation";
import { myCharacter, messageCharacter } from "../lib/agents";

export type Reducers = ReturnType<typeof connect>["connection"]["reducers"];
type Action = (key: string, operation: () => Promise<unknown>) => Promise<boolean>;

function Source({ url }: { url: unknown }) {
  try {
    const parsed = new URL(String(url));
    return ["https:", "http:"].includes(parsed.protocol) ? <a href={parsed.href} target="_blank" rel="noreferrer">View source ↗</a> : null;
  } catch { return null; }
}

function Participation({ value, onChange, disabled }: { value: Preferences; onChange: (prefs: Preferences) => void; disabled: boolean }) {
  return <fieldset disabled={disabled} className="inline-permissions">
    <legend>How your agent represents you</legend>
    {([
      ["speakUpForMe", "Speak up for me when a plan breaks my requirements"],
      ["askMeFirst", "Ask me before agreeing to a compromise"],
      ["giveMeTime", "Give me time to decide"],
      ["remindersOptedIn", "Allow reminders"],
    ] as const).map(([key, text]) => <label className="check" key={key}>
      <input type="checkbox" checked={value[key]} onChange={e => onChange({ ...value, [key]: e.target.checked })} />{text}
    </label>)}
    <small>These settings do not approve a plan or authorize a purchase.</small>
  </fieldset>;
}

function ProposalCard({ proposal: p, tables, disabled, act, reducers, edit }: {
  proposal: Row; tables: Tables; disabled: boolean; act: Action; reducers: Reducers; edit: () => void;
}) {
  const terms = json<Row>(p.termsJson, {}), summary = json<Row>(p.sharedSummaryJson, {});
  const count = tables.room_consent_counts?.find(c => c.proposalId === p.id);
  const consent = tables.my_consents?.find(c => c.proposalId === p.id);
  const impact = tables.my_proposal_impacts?.find(c => c.proposalId === p.id);
  const [note, setNote] = useState("");
  return <>
    <div className="card-title"><h3>Plan · version {p.version}</h3><span className="tag">{readable(p.state)}</span></div>
    <div className="plan-totals"><div><small>Selected options total</small><strong>{Array.isArray(terms.selected) && terms.selected.length && terms.selected.every((c: Row) => typeof c.total_cents === 'number') ? money(terms.selected.reduce((sum: number, c: Row) => sum + c.total_cents, 0)) : 'See details'}</strong></div>{count && <div><small>Group agreement</small><strong>{count.accepted} <span>of {count.required} accepted</span></strong></div>}<div><small>Booking status</small><strong className="not-booked">Not booked by Tandem</strong></div></div>
    {summary.text && <details className="proposal-explanation"><summary>Why this plan works · shared summary</summary><p>{summary.text}</p></details>}
    {p.state === 'SUPERSEDED' && <p className="stale-plan">This version is out of date. Review the latest plan before responding.</p>}
    <div className="plan-options">{(Array.isArray(terms.selected) ? terms.selected : []).map((c: Row, index: number) => <div className="option plan-option" key={c.id || index}>
      <div className="option-heading"><span className="option-icon"><Icon name={c.category === 'STAY' ? 'trips' : 'arrow'} /></span><div><small>{label(c.category || "Option")} · {c.owner || 'Shared with the group'}</small><h4>{optionTitle(c)}</h4></div><strong>{money(c.total_cents)}</strong></div>
      <dl className="option-facts">{optionFacts(c).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
      <Source url={c.source_url} />
      {c.category === 'FLIGHT' && c.return_departure_date && !c.return_source_url && <small className="source-note">Source link may show the outbound flight only. Return details above are part of this proposal.</small>}
      {c.return_source_url && <span className="return-source">Return flight: <Source url={c.return_source_url}/></span>}
    </div>)}</div>
    {Array.isArray(summary.suggestions) && summary.suggestions.length > 0 && <details>
      <summary>Activity & dinner ideas · estimates outside approval</summary>
      {summary.suggestions.map((s: Row) => <p key={s.candidate}>{s.title} · {money(s.estimate_per_person_cents)} per person <Source url={s.source_url} /></p>)}
    </details>}
    {impact && <div className="personal-impact"><small>Only you · your share</small><p>{json<Row>(impact.impactJson, {}).text || "Your personal allocation is available for review."}</p></div>}
    {count && <p className="muted">{count.accepted} accepted · {count.pending} pending · {count.declined} declined · {count.required} reviewers</p>}
    <p className="muted">Your response: {readable(consent?.response || "Not recorded")}</p>
    {p.state === "AWAITING_REVIEW" && p.isReviewer && <>
      <label>Optional feedback<textarea value={note} disabled={disabled} onChange={e => setNote(e.target.value)} rows={2} /></label>
      <div className="actions">{[
        ["ACCEPT", "Accept exact terms"], ["NOT_COMFORTABLE", "Not comfortable"],
        ["ASK_MY_AGENT", "Ask my agent"], ["SUGGEST_ALTERNATIVE", "Suggest alternative"], ["DECIDE_LATER", "Decide later"],
      ].map(([response, text]) => <button key={response} disabled={disabled} onClick={() => void act(`proposal:${p.id}`, () => reducers.respondToProposal(proposalAction(p, response, note)))}>{text}</button>)}
      <button disabled={disabled} onClick={edit}>Discuss in chat</button></div>
    </>}
    {consent?.response === "ACCEPTED" && ["AGREED", "AWAITING_REVIEW"].includes(p.state) && <button disabled={disabled} onClick={() => void act(`proposal:${p.id}`, () => reducers.withdrawConsent({ proposalId: p.id, note: undefined }))}>Withdraw my acceptance</button>}
    <small>Approval applies to these terms. It does not reserve or purchase anything.</small>
  </>;
}

function MessageCard({ item, tables, room, disabled, act, reducers, prefs, setPrefs, excluded, setExcluded, edit }: {
  item: TimelineItem; tables: Tables; room: Row; disabled: boolean; act: Action; reducers: Reducers;
  prefs: Preferences; setPrefs: (prefs: Preferences) => void;
  excluded: string[]; setExcluded: (ids: string[]) => void; edit: () => void;
}) {
  const row = item.row;
  const [declineReason, setDeclineReason] = useState("");
  const organizer = tables.room_members?.some(m => m.isMe && m.role === "ORGANIZER");
  if (item.kind === "message" || item.kind === "event") return <p className="utterance">{item.text}</p>;
  if (item.kind === "brief") {
    const brief = json<Row>(row.briefJson, {});
    return <>
      <h3>{row.state === "CONFIRMED" ? "Confirmed trip brief" : "Does this sound right?"}</h3>
      {brief.destination && <p>{brief.destination}{brief.destination_airport ? ` (${brief.destination_airport})` : ""}</p>}
      {brief.start_date && <p>{brief.start_date}{brief.end_date ? ` → ${brief.end_date}` : ""}</p>}
      {Array.isArray(brief.components) && <p>{brief.components.map(label).join(", ")}</p>}
      {brief.vibe && <p>{brief.vibe}</p>}
      {row.state === "PROPOSED" && organizer && <div className="actions">
        <button disabled={disabled} onClick={() => void act(item.key, () => reducers.confirmTripBrief({ roomId: room.id, revision: row.revision }))}>Confirm trip brief</button>
        <button disabled={disabled} onClick={edit}>Edit in chat</button>
      </div>}
    </>;
  }
  if (item.kind === "question") {
    const schema = json<Row>(row.answerSchemaJson, { type: "boolean" });
    const recovery = row.kind === 'PLAN_RECOVERY';
    const choices = recovery ? json<Row>(row.actionJson, {}).choices : [];
    const grant = ['OBJECTION_DISCLOSURE', 'CONCESSION_CONSULTATION', 'BROADER_EXPLORATION'].includes(row.kind);
    const booleanAnswer = !recovery && (schema.type === 'boolean' || row.kind === 'PARTICIPATION_CONFIRMATION' || grant);
    return <>
    <h3>A question for you</h3><p>{row.prompt}</p>
    {row.kind === "PARTICIPATION_CONFIRMATION" && row.state === "OPEN" && <Participation value={prefs} onChange={setPrefs} disabled={disabled} />}
    {row.state === 'OPEN' && grant && <label>Reason if you say no (optional)<textarea rows={2} maxLength={500} value={declineReason} disabled={disabled} onChange={e => setDeclineReason(e.target.value)} /></label>}
    {row.state === "OPEN" ? <div className="actions">
      {recovery && Array.isArray(choices) && choices.map((choice: Row) => <button key={choice.id} disabled={disabled} onClick={() => void act(item.key, () => reducers.answerQuestion(recoveryAction(row, choice.id)))}>{choice.label}</button>)}
      {booleanAnswer && [true, false].map(yes => <button key={String(yes)} disabled={disabled} onClick={() => void act(item.key, () => reducers.answerQuestion(questionAction(row, yes, prefs, declineReason)))}>{yes ? "Yes" : "No"}</button>)}
      <button disabled={disabled} onClick={edit}>Reply in chat</button>
    </div> : <small>{row.state === "ANSWERED" ? "Answer recorded" : "Earlier question · no longer active"}</small>}
  </>;
  }
  if (item.kind === "patch") {
    const patch = json<Row>(row.patchJson, {}), pending = row.state === "PENDING";
    const intake = tables.my_intake?.[0];
    const batch = intake?.state === "SUMMARY";
    const canConfirm = patch.key === "origin_airport" || !intake || intake.state === "CONFIRMED";
    return <>
      <h3>{describeFact(patch)}</h3><p className="muted">{factStrength(patch)}</p>
      {patch.source_quote && <blockquote>“{patch.source_quote}”</blockquote>}
      {patch.source_profile && <small>Suggested from your profile · confirm for this trip</small>}
      {patch.key === "trip_note" && <small>Saved as context; the current planner may not be able to enforce it.</small>}
      {pending ? <>
        {batch && <label className="check"><input type="checkbox" disabled={disabled} checked={!excluded.includes(String(row.id))} onChange={e => setExcluded(e.target.checked ? excluded.filter(id => id !== String(row.id)) : [...excluded, String(row.id)])} />Include in my confirmed details</label>}
        <div className="actions">
          {canConfirm && <button disabled={disabled} onClick={() => void act(item.key, () => patch.key === "origin_airport"
            ? reducers.confirmOrigin({ roomId: room.id, patchId: row.id })
            : reducers.confirmConstraintPatch({ patchId: row.id }))}>Confirm detail</button>}
          <button disabled={disabled} onClick={edit}>Edit in chat</button>
          <button disabled={disabled} onClick={() => void act(item.key, () => reducers.rejectConstraintPatch({ patchId: row.id }))}>Dismiss</button>
        </div>
      </> : <small>{row.state === "CONFIRMED" ? "Confirmed" : "Earlier suggestion · dismissed"}</small>}
    </>;
  }
  if (item.kind === "intake") return <>
    <h3>{row.state === "CONFIRMED" ? "Your details are confirmed" : "Ready to use these details?"}</h3>
    {row.state === "SUMMARY" && <>
      <p>Choose the details your agent should use, then confirm how it represents you.</p>
      <div className="intake-review-list">{(tables.my_constraint_patches || []).filter(p => p.state === 'PENDING').map(p => { const patch = json<Row>(p.patchJson, {}); return <label className="check" key={String(p.id)}><input type="checkbox" disabled={disabled} checked={!excluded.includes(String(p.id))} onChange={e => setExcluded(e.target.checked ? excluded.filter(id => id !== String(p.id)) : [...excluded, String(p.id)])}/><span><strong>{describeFact(patch)}</strong><small>{patch.source_quote ? `You said “${patch.source_quote}”` : patch.source_profile ? 'From your profile' : factStrength(patch)}</small></span></label>; })}</div>
      <Participation value={prefs} onChange={setPrefs} disabled={disabled} />
      <div className="actions"><button disabled={disabled} onClick={() => void act(item.key, () => reducers.confirmIntake({
        roomId: room.id, acceptedPatchIds: (tables.my_constraint_patches || []).filter(p => p.state === "PENDING" && !excluded.includes(String(p.id))).map(p => p.id), ...prefs,
      }))}>Confirm my details</button><button disabled={disabled} onClick={edit}>Edit in chat</button></div>
    </>}
  </>;
  return <ProposalCard proposal={row} tables={tables} disabled={disabled} act={act} reducers={reducers} edit={edit} />;
}

export function TripOverview({ room, tables, connected }: { room: Row; tables: Tables; connected: boolean }) {
  tables = connected ? scoped(tables, room) : {};
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    const mobile = window.matchMedia("(max-width: 760px)");
    const update = () => setExpanded(!mobile.matches);
    update(); mobile.addEventListener("change", update);
    return () => mobile.removeEventListener("change", update);
  }, []);
  const progress = workflow(room, tables), facts = established(room, tables), changes = revisionEvents(tables);
  return <>
    <div className="trip-heading"><p className="eyebrow">YOUR TRIP</p><h1>{room.goal}</h1></div>
    <p className="status-line"><span className={`dot ${connected ? "online" : ""}`} />{planningStatus(room, tables, connected)}</p>
    <details className="overview-sections" open={expanded} onToggle={e => setExpanded(e.currentTarget.open)}><summary>Trip details & workflow</summary>
    <details className="trip-overview" open>
      <summary>Trip overview</summary>
      {facts.length ? <dl className="trip-facts">{facts.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl> : <p className="muted">Tell your agent what you have in mind. Confirmed details will appear here.</p>}
      <h2 className="section-label">Travellers</h2>
      <div className="traveller-list">{(tables.room_members || []).filter(m => m.active).map(m => <span className="traveller" key={String(m.id)}><span className="avatar">{m.displayName?.slice(0, 1)}</span>{m.displayName}{m.isMe ? " (you)" : ""}</span>)}</div>
      {room.inviteCode && <details><summary>Invite someone</summary><p>Share this trip code: <code>{room.inviteCode}</code></p></details>}
    </details>
    <details className="trip-process-details"><summary>Planning history & stages</summary>
    <section className="revision-summary" aria-label="Revision summary"><div className="section-row"><h2 className="section-label">Latest revision</h2><span className="revision-number">{room.inputRevision ? `#${room.inputRevision}` : "—"}</span></div>
      <p>{changes.length ? revisionText(changes[changes.length - 1]) : "No confirmed changes yet."}</p>
      <small>Suggestions in chat take effect after you confirm them.</small>
    </section>
    <section className="compact-progress" aria-label="Workflow progress"><h2 className="section-label">Workflow</h2>
      <ol><li className={tables.room_trip_brief?.some(b => b.state === "CONFIRMED") ? "completed" : "current"}><span />Establish the trip</li>
        {stages.map(([key, name]) => <li key={key} className={progress.states[key]}><span />{name}<small>{progress.states[key] === "pending" ? "" : progress.states[key]}</small></li>)}
      </ol>
      <details><summary>Revision history</summary>{changes.length ? [...changes].reverse().map(e => <p key={String(e.id)}>{revisionText(e)}</p>) : <p className="muted">Confirmed trip changes will appear here.</p>}</details>
    </section>
    </details>
    </details>
  </>;
}

export function PlanningConversation({ room, tables: input, reducers, connected, selectedView, onViewChange }: {
  room: Row; tables: Tables; reducers: Reducers; connected: boolean;
  selectedView?: 'personal' | 'discussion' | 'plan' | 'limits';
  onViewChange?: (view: 'personal' | 'discussion' | 'plan' | 'limits') => void;
}) {
  const tables = connected ? scoped(input, room) : {}, items = timeline(room, tables);
  const sharedSetup = tables.room_members?.some(m => m.isMe && m.role === "ORGANIZER") && !tables.room_trip_brief?.length;
  type View = 'personal' | 'discussion' | 'plan' | 'limits';
  const [localView, setLocalView] = useState<View>('personal');
  const view = selectedView ?? localView, setView = onViewChange ?? setLocalView;
  const [privateOpen, setPrivateOpen] = useState(false);
  const [followLive, setFollowLive] = useState(true), [unread, setUnread] = useState(0);
  const [incoming, setIncoming] = useState<{key:string;target?:string}>();
  const knownDiscussion = useRef<Set<string> | undefined>(undefined);
  const autoScrollUntil = useRef(0);
  const phase = tripPhase(tables);
  const proposals = tables.room_proposals || [];
  const groups = proposalGroups(proposals);
  const stages = ['brief', 'intake', 'planning', 'review', 'agreed'] as const;
  const phaseIndex = stages.indexOf(phase);
  const [playback, setPlayback] = useState<VoicePlayback | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const discussionItems = discussionTimeline(items);
  const ownerQuestion = items.find(item => item.kind === "question" && item.row.state === "OPEN");
  const relevantItems = view === "discussion" ? discussionItems : items.filter(item => item.kind !== 'proposal' && (item.audience === 'private' || item.kind === 'brief' || item.key.startsWith('description:')));
  const archived = relevantItems.filter(item => item.kind === 'event' || (item.kind === 'patch' && !['PENDING', 'PROPOSED', 'OPEN'].includes(item.row.state)));
  const visibleItems = showHistory ? relevantItems : relevantItems.filter(item => !archived.includes(item));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState("");
  const [pending, setPending] = useState<string[]>([]);
  const inflight = useRef(new Set<string>());
  const [excluded, setExcluded] = useState<string[]>([]);
  const [editing, setEditing] = useState<{ key: string; title: string }>();
  const [corrections, setCorrections] = useState<string[]>([]);
  const [prefs, setPrefs] = useState<Preferences>(preferenceDefaults);
  const authoritativePrefs = tables.my_participation_preferences?.[0];
  const composer = useRef<HTMLTextAreaElement>(null), scroll = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  useEffect(() => {
    if (authoritativePrefs) setPrefs({ speakUpForMe: authoritativePrefs.speakUpForMe, askMeFirst: authoritativePrefs.askMeFirst,
      giveMeTime: authoritativePrefs.giveMeTime, remindersOptedIn: authoritativePrefs.remindersOptedIn });
  }, [authoritativePrefs]);
  const latest = visibleItems.at(-1)?.key;
  const lastDiscussionKey = discussionItems.at(-1)?.key;
  useEffect(() => {
    const keys = new Set(discussionItems.map(item => item.key));
    const added = knownDiscussion.current ? discussionItems.filter(item => !knownDiscussion.current!.has(item.key)) : [];
    knownDiscussion.current = keys;
    if (!connected || !added.length) return;
    const newest = added.at(-1)!;
    setIncoming({key:newest.key,target:String(newest.row.senderAgentId)});
    if (!followLive) setUnread(count => count + added.length);
    const timer = setTimeout(() => setIncoming(undefined),4500);
    return () => clearTimeout(timer);
  }, [lastDiscussionKey,connected]);
  const pendingCorrection = items.some(item => corrections.includes(item.key) && ["OPEN", "PENDING", "PROPOSED", "SUMMARY"].includes(item.row.state));
  useEffect(() => {
    if (!scroll.current || (view === 'discussion' ? !followLive : !atBottom.current)) return;
    autoScrollUntil.current = Date.now() + 600;
    scroll.current.scrollTo({top:scroll.current.scrollHeight,behavior: view === 'discussion' && !matchMedia('(prefers-reduced-motion: reduce)').matches ? 'smooth' : 'auto'});
    if (view === 'discussion') setUnread(0);
  }, [latest, view, followLive]);
  useEffect(() => {
    if (!playback || (view === 'discussion' && !followLive)) return;
    const node = scroll.current?.querySelector(`[data-message-key="${CSS.escape(playback.key)}"]`);
    if (node instanceof HTMLElement && scroll.current) { autoScrollUntil.current = Date.now() + 600; scroll.current.scrollTo({ top: node.offsetTop - scroll.current.offsetTop - 20, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); }
  }, [playback?.key,followLive]);
  const act: Action = async (key, operation) => {
    if (!connected || inflight.current.has(key)) return false;
    inflight.current.add(key); setPending(keys => [...keys, key]); setError("");
    try { await operation(); return true; }
    catch (e) { const message = e instanceof Error ? e.message : "This action could not be completed. Try again after reconnecting."; setError(message.includes("STALE_SNAPSHOT") ? "The trip changed while you were reviewing it. Read the latest details before confirming again." : message); return false; }
    finally { inflight.current.delete(key); setPending(keys => keys.filter(k => k !== key)); }
  };
  const startEdit = (item: TimelineItem) => {
    const title = item.kind === "question" ? item.row.prompt : item.kind === "patch" ? describeFact(json<Row>(item.row.patchJson, {})) : item.kind === "brief" ? "the trip brief" : item.kind === "proposal" ? "the proposed plan" : "my trip details";
    setView("personal"); setEditing({ key: item.key, title }); setReceipt(""); requestAnimationFrame(() => composer.current?.focus());
  };
  const send = async () => {
    const body = message.trim(); if (!body) return;
    const outgoing = outgoingMessage(room, tables, editing ? `Regarding ${editing.title}: ${body}` : body,
      pendingCorrection ? undefined : { prefs, excluded });
    const ok = await act("composer", () => outgoing.kind === "description" ? reducers.setTripDescription(outgoing.args)
      : outgoing.kind === "confirmation" ? reducers.confirmIntake(outgoing.args) : reducers.submitPrivateMessage(outgoing.args));
    if (ok) {
      if (editing) setCorrections(keys => [...keys, editing.key]);
      setEditing(undefined); setMessage(""); setReceipt(outgoing.kind === "confirmation"
        ? "Your details are confirmed" : "Message sent · awaiting your agent’s response");
      atBottom.current = true;
    }
  };
  return <section className={`conversation-panel conversation-panel--${view}`} aria-label="Trip conversation">
    <div className="conversation-heading"><div><p className="eyebrow">{view === 'discussion' ? 'DIFFERENT PERSPECTIVES. COMMON GROUND.' : view === 'plan' ? 'THE DETAILS. THE DECISIONS. YOUR SAY.' : view === 'limits' ? 'YOUR OWN CORNER OF THE TRIP' : 'A LITTLE HELP, JUST FOR YOU'}</p><h2>{view === 'discussion' ? 'A meeting of little minds.' : view === 'plan' ? 'A plan worth saying yes to.' : view === 'limits' ? 'Your needs, understood.' : 'Your trip, in conversation'}</h2></div><AgentAvatar speechTarget={String(tables.room_members?.find(m => m.isMe)?.agentId ?? 'personal')} variant={myCharacter(tables)} size="message" animated={false} /></div>
    <ol className="journey-strip" aria-label="Trip journey">{stages.map((stage, index) => <li key={stage} aria-current={stage === phase ? 'step' : undefined} data-complete={index < phaseIndex}><span>{index < phaseIndex ? '✓' : String(index + 1).padStart(2, '0')}</span>{phaseLabels[stage]}</li>)}</ol>
    <div className="conversation-tabs" role="tablist" aria-label="Conversation view" onKeyDown={event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const tabs = ['personal','discussion','plan','limits'] as const;
      const index = tabs.indexOf(view);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? 3 : (index + (event.key === 'ArrowRight' ? 1 : -1) + 4) % 4;
      setView(tabs[next]); document.getElementById(`trip-tab-${tabs[next]}`)?.focus();
    }}>
      {([['personal','Your agent','chat'], ['discussion','Agent discussion','spark'], ['plan','Your plan','plan'], ['limits','My limits','lock']] as const).map(([id, text, icon]) => <button id={`trip-tab-${id}`} key={id} type="button" role="tab" tabIndex={view === id ? 0 : -1} aria-controls="room-view" aria-selected={view === id} onClick={() => setView(id)}><Icon name={icon} size={16}/>{text}{id === 'plan' && proposals.some(p => p.state === 'AWAITING_REVIEW') && <span className="tab-attention" aria-label="Ready for review"/>}{id === 'personal' && ownerQuestion && <span className="tab-attention" aria-label="Private question waiting"/>}</button>)}
    </div>
    <div className="room-view" id="room-view" role="tabpanel" aria-labelledby={`trip-tab-${view}`}>
    {view === 'discussion' && <RoomAgents room={room} tables={tables} connected={connected} activeTarget={playback?.target} liveTarget={incoming?.target} speaking={playback?.phase === 'speaking'} />}
    <div className="voice-dock" hidden={view === 'plan' || view === 'limits'}>
    <AgentVoice onPlayback={setPlayback} key={String(room.id)} items={view === "discussion" ? discussionItems : relevantItems} tables={tables} discussion={view === 'discussion'} connected={connected && (view === 'personal' || view === 'discussion')} onTranscript={async body => {
      const outgoing = outgoingMessage(room, tables, body);
      const ok = await act('voice-composer', () => outgoing.kind === 'description'
        ? reducers.setTripDescription(outgoing.args) : reducers.submitPrivateMessage({ roomId: room.id, body }));
      if (!ok) setMessage(current => current ? `${current}\n${body}` : body);
      else { atBottom.current = true; setReceipt('Spoken details sent · review the agent’s suggestions before confirming.'); }
      return ok;
    }} />
    </div>
    <div className="conversation-workspace">
    <LiveActivity room={room} tables={input} connected={connected} busy={!connected || pending.length > 0} retry={tables.room_members?.some(m => m.isMe && m.role === 'ORGANIZER') ? job => { void act('retry-planning', () => reducers.retryPlanning({roomId: room.id, jobId: BigInt(job), requestKey: crypto.randomUUID()})); } : undefined} />
    {view === 'discussion' && <div className="discussion-live-bar"><span><i data-connected={connected}/>{!connected ? 'Reconnecting to shared updates' : followLive ? 'Following live updates' : 'Reading earlier messages'}</span><button type="button" aria-pressed={followLive} onClick={() => { setFollowLive(value => !value); setUnread(0); }}>{followLive ? 'Pause following' : unread ? `Follow live · ${unread} new` : 'Follow live ↓'}</button></div>}
    {view === 'personal' && (phase === 'review' || phase === 'agreed') && <div className="next-step-banner"><span className="next-step-icon"><Icon name="plan" size={20}/></span><div><strong>{phase === 'agreed' ? 'You found your common ground.' : 'There’s a plan ready for your review.'}</strong><small>{phase === 'agreed' ? 'Your agreed decisions and personal share are saved in Your plan.' : 'Check the options and your share before giving your final yes.'}</small></div><button onClick={() => setView('plan')}>{phase === 'agreed' ? 'View agreed plan' : 'Review plan'} →</button></div>}
    {view === 'personal' && tables.my_intake?.[0]?.state === 'CONFIRMED' && tables.room_intake_progress?.[0]?.ready < tables.room_intake_progress?.[0]?.total && <div className="readiness-note"><Icon name="person" size={16}/><span>Your details are ready. {tables.room_intake_progress[0].ready} of {tables.room_intake_progress[0].total} travellers have confirmed. Everyone gets their own time.</span></div>}
    {ownerQuestion && <div className="next-step-banner next-step-banner--question"><span className="next-step-icon"><Icon name="chat" size={20}/></span><div><strong>Your companion has a private question.</strong><small>Your answer helps it find the right next step.</small></div><button onClick={() => { setView('personal'); atBottom.current = false; requestAnimationFrame(() => document.querySelector(`[data-message-key="${ownerQuestion.key}"]`)?.scrollIntoView({block:'center'})); }}>Answer privately →</button></div>}
    <div className="conversation-feed" hidden={view === "plan" || view === "limits"} role="log" aria-label="Trip messages" aria-live="polite" aria-relevant="additions" ref={scroll} onScroll={() => {
      if (scroll.current) {
        atBottom.current = scroll.current.scrollHeight - scroll.current.scrollTop - scroll.current.clientHeight < 100;
        if (view === 'discussion' && Date.now() > autoScrollUntil.current && !atBottom.current) setFollowLive(false);
      }
    }}>
      {archived.length > 0 && <button type="button" className="conversation-history" aria-expanded={showHistory} onClick={() => setShowHistory(value => !value)}>{showHistory ? 'Hide earlier updates' : `Show ${archived.length} earlier updates & saved details`}</button>}
      {!visibleItems.length && <div className="conversation-empty"><span aria-hidden="true">↗</span><h3>{view === 'discussion' ? 'Every voice, one plan.' : 'Start with an idea.'}</h3><p>{view === 'discussion' ? 'Shared agent messages appear here as your companions compare options. Each traveller first confirms their needs privately.' : 'A weekend away, a camping trip, a celebration—tell your agent what you’re imagining. It can ask for the details as you go.'}</p></div>}
      {visibleItems.map(item => {
        const human = item.kind === "message" && item.row.sender === "HUMAN";
        const blocked = corrections.includes(item.key) || editing?.key === item.key;
        const agent = tables.room_agents?.find(a => a.id === item.row.senderAgentId);
        const member = tables.room_members?.find(m => m.agentId === item.row.senderAgentId);
        const appearance = messageCharacter(tables, item.row.senderAgentId);
        const recipient = tables.room_agents?.find(a => a.id === item.row.recipientAgentId)?.kind === 'MEDIATOR' ? 'Mediator' : tables.room_members?.find(m => m.agentId === item.row.recipientAgentId)?.displayName;
        const speaker = human ? "You" : item.row.senderAgentId ? agent?.kind === "MEDIATOR" ? "Mediator" : `${member?.displayName || "Personal"}’s agent` : item.speaker;
        return <article key={item.key} data-message-key={item.key} data-speaking={playback?.key === item.key} data-new={view === 'discussion' && incoming?.key === item.key} data-color={appearance.variant} className={`chat-item ${human ? "from-you" : "from-agent"} ${item.kind === "event" ? "chat-event" : ""} ${item.audience === "shared" ? "shared-item" : "private-item"}`}>
          <div className="message-meta">{!human && item.kind !== "event" && (item.audience === "private" || item.row.senderAgentId != null) && <AgentAvatar speechTarget={String(item.row.senderAgentId ?? tables.room_members?.find(m => m.isMe)?.agentId ?? 'personal')} variant={appearance.variant} mediator={appearance.mediator} size="message" animated={false} />}<strong>{speaker}</strong>{view === 'discussion' && recipient && <span className="message-recipient">to {recipient}{recipient === 'Mediator' ? '' : '’s agent'}</span>}{view === 'discussion' && item.time > 0n && <time className="message-time" dateTime={new Date(Number(item.time)/1000).toISOString()}>{new Date(Number(item.time)/1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time>}{playback?.key === item.key && <span className="message-speaking">{playback.phase === 'speaking' ? 'Speaking' : 'Preparing voice'}</span>}<span>{item.audience === "private" ? "Only you" : "Shared with trip"}</span></div>
          <MessageCard item={item} tables={tables} room={room} disabled={!connected || pending.length > 0 || blocked || pendingCorrection} act={act} reducers={reducers} prefs={prefs} setPrefs={setPrefs} excluded={excluded} setExcluded={setExcluded} edit={() => startEdit(item)} />
          {item.row.type === 'RESEARCH_UPDATE' && <p className="discussion-source">Research handoff · findings are still being evaluated.</p>}
          {item.audience === 'shared' && item.row.sanitizationStatus === 'TEMPLATE_FALLBACK' && <p className="discussion-source">Verified fallback summary · fixed wording from recorded facts.</p>}
          {blocked && <small className="correction-status">{corrections.includes(item.key) ? "Correction sent · awaiting an updated response" : "Write your correction below, or cancel to keep this suggestion."}</small>}
        </article>;
      })}
    </div>
    {view === 'plan' && <section className="plan-view" aria-label="Plan review">
      <div className="view-intro"><span className="privacy-label"><Icon name="plan" size={15}/>SHARED PLAN · YOUR APPROVAL</span><h2>{phase === 'agreed' ? 'Everyone found their yes.' : 'The trip, coming together.'}</h2><p>Review the details and your own share. Nothing is booked until you choose to book it.</p></div>
      {!groups.length && <EmptyState title="Good things take a little planning." icon="plan">Your plan will appear here after your companions find options that fit. You can follow their searches and discussion as they work.</EmptyState>}
      {groups.map(([stage, plans]) => <section className="decision-group" key={stage}><h3 className="eyebrow">{label(stage)}</h3>{plans.map(p => <article className="plan-review-card" key={String(p.id)}><ProposalCard proposal={p} tables={tables} disabled={!connected || pending.length > 0} act={act} reducers={reducers} edit={() => startEdit({ kind: 'proposal', row: p, key: `proposal:${p.id}`, audience: 'shared', speaker: '', time: 0n })}/></article>)}</section>)}
      {proposals.some(p => p.state === 'SUPERSEDED') && <details className="plan-history"><summary>Earlier versions · no longer open for approval</summary>{proposals.filter(p => p.state === 'SUPERSEDED').map(p => <article className="plan-review-card" key={String(p.id)}><ProposalCard proposal={p} tables={tables} disabled act={act} reducers={reducers} edit={() => setView('personal')}/></article>)}</details>}
    </section>}
    {view === 'limits' && <MyLimits tables={tables} disabled={!connected || pending.length > 0} reducers={reducers} edit={() => { setView('personal'); requestAnimationFrame(() => composer.current?.focus()); }}/>}
    </div>
    {error && <p role="alert" className="notice room-error">{error}</p>}
    {view === 'discussion' ? <div className="discussion-footer"><span>Every agent has a voice. The final decision is yours.</span><button type="button" aria-expanded={privateOpen} onClick={() => setPrivateOpen(!privateOpen)}>{privateOpen ? 'Close private conversation' : ownerQuestion ? 'Your Pip has a question' : 'Talk to your Pip'}</button>{proposals.length > 0 && <button type="button" className="primary-action" onClick={() => setView('plan')}>Review the plan ↗</button>}</div> : null}
    {(view === 'personal' || (view === 'discussion' && privateOpen)) ? <div className="composer-wrap">
      {view === 'discussion' && <div className="private-planning-replies" aria-label="Private conversation with your Pip" role="log" aria-live="polite"><small>ONLY YOU AND YOUR PIP</small>{items.filter(item => item.audience === 'private' && (item.kind === 'message' || (item.kind === 'question' && item.row.state === 'OPEN'))).slice(-3).map(item => <article key={item.key}><strong>{item.row.sender === 'HUMAN' ? 'You' : 'Your Pip'}</strong><MessageCard item={item} tables={tables} room={room} disabled={!connected || pending.length > 0} act={act} reducers={reducers} prefs={prefs} setPrefs={setPrefs} excluded={excluded} setExcluded={setExcluded} edit={() => startEdit(item)} /></article>)}</div>}
      {editing && <div className="reply-context"><span>Replying about {editing.title}</span><button onClick={() => setEditing(undefined)}>Cancel reply</button></div>}
      <form className="chat-composer" onSubmit={e => { e.preventDefault(); void send(); }}>
        <label className="sr-only" htmlFor="trip-message">Message your agent</label>
        <textarea id="trip-message" ref={composer} value={message} disabled={!connected || pending.includes("composer")} onChange={e => { setMessage(e.target.value); setReceipt(""); }} rows={2} placeholder={sharedSetup ? "Describe the shared trip idea…" : "Tell your agent what you have in mind…"} maxLength={4000} />
        <div className="composer-bottom"><small>{!connected ? "Reconnecting · your draft stays here" : sharedSetup ? "First trip idea · shared with travellers" : "Private to your agent · add details anytime"}</small><button className="send-message" disabled={!connected || !message.trim() || pending.includes("composer")}>{pending.includes("composer") ? "Sending…" : "Send message ↑"}</button></div>
      </form>
      <p className="send-receipt" role="status">{receipt || "Your agent suggests. You confirm changes and approve the final plan."}</p>
    </div> : null}
    </div>
  </section>;
}
