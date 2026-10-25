"use client";

import { useEffect, useRef, useState } from "react";
import type { connect } from "../lib/client";
import { json, money, proposalAction, questionAction, recoveryAction, type Row } from "../lib/state";
import { constraintValue, describeFact, established, factStrength, label, outgoingMessage, planningStatus, preferenceDefaults,
  readable, revisionEvents, revisionText, scoped, timeline, type Preferences, type Tables, type TimelineItem } from "../lib/planning";
import { discussionTimeline } from "../lib/discussion";
import { stages, workflow } from "../lib/workflow";
import { AgentAvatar } from "./agent-avatar";
import { AgentVoice, type VoicePlayback } from './agent-voice';
import { RoomAgents } from "./agent-companions";
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
    <p>{summary.text || "Review these exact options before deciding."}</p>
    {(Array.isArray(terms.selected) ? terms.selected : []).map((c: Row, index: number) => <div className="option" key={c.id}>
      <strong>{label(c.category || "Option")} · traveller {index + 1}</strong>
      <p>{c.title || (c.flight_identity && !/[a-f0-9]{32}/i.test(c.flight_identity) ? c.flight_identity : "Selected flight")} <b>{money(c.total_cents)}</b></p>
      <Source url={c.source_url} />
    </div>)}
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
      <p>Review the selected suggestions above and how your agent should represent you.</p>
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
  const [expanded, setExpanded] = useState(true);
  useEffect(() => {
    const mobile = window.matchMedia("(max-width: 760px)");
    const update = () => setExpanded(!mobile.matches);
    update(); mobile.addEventListener("change", update);
    return () => mobile.removeEventListener("change", update);
  }, []);
  const progress = workflow(room, tables), facts = established(room, tables), changes = revisionEvents(tables);
  const personal = tables.my_rules?.filter(r => r.state !== "set_aside") || [];
  const legacy = tables.my_constraints || [];
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
      {(personal.length > 0 || legacy.length > 0) && <details><summary>Your confirmed preferences · only you</summary>
        {personal.length ? personal.map(r => <p key={String(r.id)}>{describeFact({ rule: json<Row>(r.ruleJson, {}) })}<small className="fact-kind">{r.kind === "soft" ? "Preference" : "Requirement"}</small></p>) : legacy.map(c => <p key={String(c.id)}>{describeFact({ key: c.key, value: constraintValue(c.valueJson), unit: c.unit })}</p>)}
      </details>}
    </details>
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
  </>;
}

export function PlanningConversation({ room, tables: input, reducers, connected }: {
  room: Row; tables: Tables; reducers: Reducers; connected: boolean;
}) {
  const tables = connected ? scoped(input, room) : {}, items = timeline(room, tables);
  const sharedSetup = tables.room_members?.some(m => m.isMe && m.role === "ORGANIZER") && !tables.room_trip_brief?.length;
  const [view, setView] = useState<"personal" | "discussion">("personal");
  const [playback, setPlayback] = useState<VoicePlayback | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const discussionItems = discussionTimeline(items);
  const ownerQuestion = items.find(item => item.kind === "question" && item.row.state === "OPEN");
  const relevantItems = view === "discussion" ? discussionItems : items;
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
  useEffect(() => { if (view === 'discussion' && scroll.current) scroll.current.scrollTop = 0; }, [view]);
  const latest = items.at(-1)?.key;
  const pendingCorrection = items.some(item => corrections.includes(item.key) && ["OPEN", "PENDING", "PROPOSED", "SUMMARY"].includes(item.row.state));
  useEffect(() => {
    if (atBottom.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [latest, items.length]);
  useEffect(() => {
    if (!playback) return;
    const node = scroll.current?.querySelector(`[data-message-key="${CSS.escape(playback.key)}"]`);
    if (node instanceof HTMLElement && scroll.current) scroll.current.scrollTo({ top: node.offsetTop - scroll.current.offsetTop - 20, behavior: 'smooth' });
  }, [playback?.key]);
  const act: Action = async (key, operation) => {
    if (!connected || inflight.current.has(key)) return false;
    inflight.current.add(key); setPending(keys => [...keys, key]); setError("");
    try { await operation(); return true; }
    catch (e) { setError(e instanceof Error ? e.message : "This action could not be completed. Try again after reconnecting."); return false; }
    finally { inflight.current.delete(key); setPending(keys => keys.filter(k => k !== key)); }
  };
  const startEdit = (item: TimelineItem) => {
    const title = item.kind === "question" ? item.row.prompt : item.kind === "patch" ? describeFact(json<Row>(item.row.patchJson, {})) : item.kind === "brief" ? "the trip brief" : item.kind === "proposal" ? "the proposed plan" : "my trip details";
    setEditing({ key: item.key, title }); setReceipt(""); composer.current?.focus();
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
    <div className="conversation-heading">{view === 'discussion' && <button type="button" className="studio-back" onClick={() => setView('personal')}>← Back to trip</button>}<div><p className="eyebrow">{view === 'discussion' ? 'FOUR PERSPECTIVES. ONE GETAWAY.' : 'YOUR OWN CORNER OF THE TRIP'}</p><h2>{view === 'discussion' ? 'A meeting of little minds.' : 'Your trip, in conversation'}</h2></div><AgentAvatar speechTarget={String(tables.room_members?.find(m => m.isMe)?.agentId ?? 'personal')} variant={myCharacter(tables)} size="message" animated={false} /></div>
    <p className="conversation-intro">{view === 'discussion' ? `${room.title || room.name || 'Your trip'} · Real agent messages, shared with everyone.` : 'A plan that works for everyone starts with a little conversation.'}</p>
    <RoomAgents room={room} tables={tables} connected={connected} activeTarget={playback?.target} speaking={playback?.phase === 'speaking'} />
    <div className="conversation-tabs" role="tablist" aria-label="Conversation view">
      <button type="button" role="tab" aria-selected={view === 'personal'} onClick={() => setView('personal')}>Your agent <span>Private replies</span></button>
      <button type="button" role="tab" aria-selected={view === 'discussion'} onClick={() => setView('discussion')}>Agent discussion <span>{discussionItems.length} updates</span></button>
    </div>
    <AgentVoice onPlayback={setPlayback} key={String(room.id)} items={view === "discussion" ? discussionItems : items} tables={tables} discussion={view === 'discussion'} connected={connected} onTranscript={async body => {
      const outgoing = outgoingMessage(room, tables, body);
      const ok = await act('voice-composer', () => outgoing.kind === 'description'
        ? reducers.setTripDescription(outgoing.args) : reducers.submitPrivateMessage({ roomId: room.id, body }));
      if (!ok) setMessage(current => current ? `${current}\n${body}` : body);
      else { atBottom.current = true; setReceipt('Spoken details sent · review the agent’s suggestions before confirming.'); }
      return ok;
    }} />

    <div className="conversation-feed" role="log" aria-label="Trip messages" aria-live="polite" aria-relevant="additions" ref={scroll} onScroll={() => {
      if (scroll.current) atBottom.current = scroll.current.scrollHeight - scroll.current.scrollTop - scroll.current.clientHeight < 100;
    }}>
    {view === 'discussion' && ownerQuestion && <div className="discussion-action-needed" role="status"><div><strong>Your agent needs your input</strong><span>A private question is waiting before planning can continue.</span></div><button type="button" onClick={() => { setView('personal'); atBottom.current = false; requestAnimationFrame(() => document.querySelector(`[data-message-key="${ownerQuestion.key}"]`)?.scrollIntoView({ block: 'center' })); }}>Answer privately →</button></div>}
      {archived.length > 0 && <button type="button" className="conversation-history" aria-expanded={showHistory} onClick={() => setShowHistory(value => !value)}>{showHistory ? 'Hide earlier updates' : `Show ${archived.length} earlier updates & saved details`}</button>}
      {!visibleItems.length && <div className="conversation-empty"><span aria-hidden="true">↗</span><h3>{view === 'discussion' ? 'Every voice, one plan.' : 'Start with an idea.'}</h3><p>{view === 'discussion' ? 'Shared agent messages appear here as your companions compare options. Each traveller first confirms their needs privately.' : 'A weekend away, a camping trip, a celebration—tell your agent what you’re imagining. It can ask for the details as you go.'}</p></div>}
      {visibleItems.map(item => {
        const human = item.kind === "message" && item.row.sender === "HUMAN";
        const blocked = corrections.includes(item.key) || editing?.key === item.key;
        const agent = tables.room_agents?.find(a => a.id === item.row.senderAgentId);
        const member = tables.room_members?.find(m => m.agentId === item.row.senderAgentId);
        const appearance = messageCharacter(tables, item.row.senderAgentId);
        const speaker = human ? "You" : item.row.senderAgentId ? agent?.kind === "MEDIATOR" ? "Mediator" : `${member?.displayName || "Personal"}’s agent` : item.speaker;
        return <article key={item.key} data-message-key={item.key} data-speaking={playback?.key === item.key} data-color={appearance.variant} className={`chat-item ${human ? "from-you" : "from-agent"} ${item.kind === "event" ? "chat-event" : ""} ${item.audience === "shared" ? "shared-item" : "private-item"}`}>
          <div className="message-meta">{!human && item.kind !== "event" && (item.audience === "private" || item.row.senderAgentId != null) && <AgentAvatar speechTarget={String(item.row.senderAgentId ?? tables.room_members?.find(m => m.isMe)?.agentId ?? 'personal')} variant={appearance.variant} mediator={appearance.mediator} size="message" animated={false} />}<strong>{speaker}</strong>{playback?.key === item.key && <span className="message-speaking">{playback.phase === 'speaking' ? 'Speaking' : 'Preparing voice'}</span>}<span>{item.audience === "private" ? "Only you" : "Shared with trip"}</span></div>
          <MessageCard item={item} tables={tables} room={room} disabled={!connected || pending.length > 0 || blocked || pendingCorrection} act={act} reducers={reducers} prefs={prefs} setPrefs={setPrefs} excluded={excluded} setExcluded={setExcluded} edit={() => startEdit(item)} />
          {view === 'discussion' && item.discussionMeta && (item.discussionMeta.deliveries > 1 || item.text !== item.discussionMeta.originalText) && <details className="discussion-source"><summary>Full message{item.discussionMeta.deliveries > 1 ? ` · ${item.discussionMeta.deliveries} deliveries grouped` : ' · repeated details shortened'}</summary><p>{item.discussionMeta.originalText}</p></details>}
          {blocked && <small className="correction-status">{corrections.includes(item.key) ? "Correction sent · awaiting an updated response" : "Write your correction below, or cancel to keep this suggestion."}</small>}
        </article>;
      })}
    </div>
    {view === 'discussion' ? <div className="discussion-footer"><span>Every agent has a voice. The final decision is yours.</span><button type="button" onClick={() => setView('personal')}>Back to your agent →</button>{tables.room_proposals?.length ? <button type="button" onClick={() => { setView('personal'); atBottom.current = true; requestAnimationFrame(() => { if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }); }}>Review the plan ↗</button> : null}</div> : <div className="composer-wrap">
      {error && <p role="alert" className="notice">{error}</p>}
      {editing && <div className="reply-context"><span>Replying about {editing.title}</span><button onClick={() => setEditing(undefined)}>Cancel reply</button></div>}
      <form className="chat-composer" onSubmit={e => { e.preventDefault(); void send(); }}>
        <label className="sr-only" htmlFor="trip-message">Message your agent</label>
        <textarea id="trip-message" ref={composer} value={message} disabled={!connected || pending.includes("composer")} onChange={e => { setMessage(e.target.value); setReceipt(""); }} rows={2} placeholder={sharedSetup ? "Describe the shared trip idea…" : "Tell your agent what you have in mind…"} maxLength={4000} />
        <div className="composer-bottom"><small>{!connected ? "Reconnecting · your draft stays here" : sharedSetup ? "First trip idea · shared with travellers" : "Private to your agent · add details anytime"}</small><button className="send-message" disabled={!connected || !message.trim() || pending.includes("composer")}>{pending.includes("composer") ? "Sending…" : "Send message ↑"}</button></div>
      </form>
      <p className="send-receipt" role="status">{receipt || "Your agent suggests. You confirm changes and approve the final plan."}</p>
    </div>}
  </section>;
}
