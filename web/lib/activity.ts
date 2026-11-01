import { json, type Row } from './state';
import { sameRevisions, workflow } from './workflow';
import { planningStatus } from './planning';

export const researchPhases: Record<string, string> = {
  QUEUED: 'Waiting for a search slot', STARTING: 'Starting actor', RUNNING: 'Searching sources',
  COLLECTING: 'Collecting and checking results', COMPLETED: 'Search complete', CACHED: 'Reused recent results', FAILED: 'Search interrupted',
};
export const categories: Record<string, string> = { FLIGHT: 'Flights', STAY: 'Places to stay', TRANSFER: 'Transfers', ACTIVITY: 'Things to do', DINNER: 'Places to eat' };
export const stageLabels: Record<string, string> = { LOAD: 'Reading confirmed trip details', RESEARCH: 'Researching options', SOLVE: 'Comparing options for the group', AGENTS: 'Your agents are comparing the plan', VALIDATE: 'Checking the plan against your needs', PUBLISH: 'Preparing your proposal', REVIEW: 'Ready for your review', AGREED: 'Everyone has agreed', STOPPED: 'Planning stopped' };
const errors: Record<string, string> = {
  APIFY_POLL_DEADLINE_EXCEEDED: 'The worker stopped waiting for the search. The provider may still be running.',
  APIFY_POLL_BUDGET_EXHAUSTED: 'The search reached its status-check limit.',
  APIFY_RATE_LIMITED: 'Apify is limiting requests. The search could not continue.',
  APIFY_AUTH_FAILED: 'The search provider needs attention from the team.',
  APIFY_RESOURCE_UNAVAILABLE: 'The requested search actor is unavailable.',
  APIFY_START_AMBIGUOUS: 'The start could not be confirmed. No duplicate paid run was started.',
  ACTOR_RUN_BUDGET_EXHAUSTED: 'The search allowance has been reached.',
  APIFY_RUN_FAILED: 'The provider reported that this run did not finish successfully.',
  APIFY_CONNECTION_FAILED: 'The worker could not reach the search provider.',
};
export function timestamp(value: any): number {
  const micros = value?.microsSinceUnixEpoch ?? value?.__timestamp_micros_since_unix_epoch__;
  const n = micros === undefined ? 0 : Number(micros) / 1000;
  return Number.isFinite(n) ? n : 0;
}
export function ageLabel(time: number, now: number) {
  if (!time) return 'Time unavailable';
  const age = Math.max(0, Math.floor((now - time) / 1000));
  return age < 5 ? 'Just now' : age < 60 ? `${age}s ago` : age < 3600 ? `${Math.floor(age / 60)}m ago` : `${Math.floor(age / 3600)}h ago`;
}
export type ActivityRecord = Row & { time: number; eventId: bigint; type: string; job_ref: string; generation: string; attempt: string; phase: string; category: string; search_ref: string; round: number; actor_id: string; run_ref?: string; polls: number; stage: string };
export function liveActivity(room: Row, tables: Record<string, Row[]>, connected: boolean, now: number) {
  const graph = workflow(room, tables);
  const all = (tables.room_events || []).filter(e => String(e.roomId) === String(room.id) && e.audience === 'ROOM')
    .flatMap<ActivityRecord>(e => {
      if (!['WORKFLOW_PROGRESS', 'RESEARCH_ACTIVITY'].includes(e.type)) return [];
      const p = json<Row>(e.safePayloadJson, {});
      if (p.schema_version !== 1 || !p.job_ref || !p.generation) return [];
      if (e.type === 'RESEARCH_ACTIVITY' && (p.provider !== 'APIFY' || !Object.hasOwn(researchPhases, p.phase) || !Object.hasOwn(categories, p.category) || typeof p.search_ref !== 'string')) return [];
      if (e.type === 'WORKFLOW_PROGRESS' && (!Object.hasOwn(stageLabels, p.stage) || !['STARTED','COMPLETED','RECOVERING','PAUSED','FINISHED','FAILED'].includes(p.phase))) return [];
      return [{ ...p, type: e.type, eventId: e.id, time: timestamp(e.createdAt) } as ActivityRecord];
    }).sort((a, b) => a.eventId < b.eventId ? -1 : a.eventId > b.eventId ? 1 : 0);
  const matching = all.filter(e => sameRevisions(e, room));
  const last = matching.at(-1);
  const events = last ? matching.filter(e => e.job_ref === last.job_ref && e.generation === last.generation) : [];
  const currentAttempt = last?.attempt;
  const latestRuns = new Map<string, ActivityRecord>();
  for (const e of events.filter(e => e.type === 'RESEARCH_ACTIVITY')) latestRuns.set(`${e.round}:${e.search_ref}`, e);
  const stopped = graph.last?.stage === 'STOPPED';
  const runs = [...latestRuns.values()].map((run, index) => {
    const terminal = ['COMPLETED','CACHED','FAILED'].includes(run.phase);
    const stale = !terminal && (!connected || !run.time || now - run.time > 45000 || run.attempt !== currentAttempt || stopped);
    return { ...run, number: index + 1, stale, live: !terminal && !stale,
      label: stale ? (!connected ? 'Disconnected · last reported' : stopped ? 'Monitoring stopped' : 'Awaiting a fresh update') : researchPhases[run.phase],
      detail: run.phase === 'FAILED' ? errors[run.error_code] || 'The search could not continue. The team can check the provider connection.' : undefined,
      count: Number.isInteger(run.result_count) && run.result_count >= 0 ? run.result_count as number : undefined };
  });
  const running = runs.filter(r => r.live && r.phase !== 'QUEUED');
  const queued = runs.filter(r => r.live && r.phase === 'QUEUED');
  const failed = runs.filter(r => r.phase === 'FAILED');
  const latestTime = last?.time || 0;
  const fresh = connected && latestTime > 0 && now - latestTime <= 45000;
  const ended = !!graph.published || stopped || graph.last?.phase === 'PAUSED';
  let title = planningStatus(room, tables, connected);
  let tone = 'quiet';
  if (!connected) title = 'Connection lost · showing last received activity';
  else if (stopped) { title = graph.last?.phase === 'FAILED' ? 'Planning needs attention' : 'Planning has stopped'; tone = graph.last?.phase === 'FAILED' ? 'error' : 'quiet'; }
  else if (running.length && !ended) { title = `${running.length} ${running.length === 1 ? 'search' : 'searches'} in progress${queued.length ? ` · ${queued.length} queued` : ''}`; tone = 'live'; }
  else if (failed.length && !ended) { title = 'A search needs attention'; tone = 'error'; }
  else if (queued.length && !ended) { title = `${queued.length} ${queued.length === 1 ? 'search' : 'searches'} queued`; tone = 'waiting'; }
  else if (graph.last?.phase === 'STARTED' && !ended) {
    title = fresh ? stageLabels[graph.last.stage] : 'Waiting for a fresh worker update'; tone = fresh ? 'live' : 'quiet';
  } else if (graph.published) tone = 'done';
  else if (graph.last?.phase === 'PAUSED') tone = 'waiting';
  // Agent statuses have their own timestamps and are not proof of a live worker.
  const agents = (tables.room_agent_activity || []).filter(a => String(a.roomId) === String(room.id)).map(a => {
    const member = tables.room_members?.find(m => m.agentId === a.agentId);
    const mediator = tables.room_agents?.find(agent => agent.id === a.agentId)?.kind === 'MEDIATOR';
    const labels: Record<string,string> = { SEARCHING: 'Researching options', THINKING: 'Considering the plan', CHECKING: 'Checking options', WRITING: 'Preparing a reply', WAITING_FOR_OWNER: 'Waiting for input', IDLE: 'Ready' };
    const time = timestamp(a.updatedAt), recent = connected && time > 0 && now - time <= 45000 && !stopped;
    return { id: String(a.agentId), name: mediator ? 'Mediator' : `${member?.displayName || 'Traveller'}’s agent`,
      label: a.activity === 'WAITING_FOR_OWNER' && !member?.isMe ? 'Ready' : labels[a.activity] || 'Status received', busy: !['IDLE','WAITING_FOR_OWNER'].includes(a.activity), recent, time };
  });
  if (!events.length && connected && agents.some(a => a.recent && a.busy)) { title = 'Your agents are working on the trip details'; tone = 'live'; }
  return { title, tone, runs, agents, events, latestTime, graph, earlier: all.filter(e => !events.includes(e)),
    detail: !events.length ? 'No worker execution has been reported for the current trip details yet.' : stopped ? 'Your saved trip is safe. This is the last reported state; a new attempt will appear here when it starts.' : 'Updates come from worker and provider events. Search results still need to be checked against the group’s needs.' };
}
