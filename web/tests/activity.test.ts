import { expect, it } from 'vitest';
import { liveActivity, searchDetails } from '../lib/activity';
const room = { id: 1n, inputRevision: 1n, membershipRevision: 1n, permissionRevision: 1n };
const now = 1800000000000;
function event(id: number, phase = 'RUNNING', extra = {}, time = now) {
  return { id: BigInt(id), roomId: 1n, audience: 'ROOM', type: 'RESEARCH_ACTIVITY', createdAt: { microsSinceUnixEpoch: BigInt(time * 1000) }, safePayloadJson: JSON.stringify({ schema_version: 1, provider: 'APIFY', job_ref: '1', generation: '1', attempt: '1', input_revision: 1, membership_revision: 1, permission_revision: 1, category: 'FLIGHT', search_ref: 'safe-search', phase, round: 0, actor_id: 'actor', run_ref: 'run1', ...extra }) };
}
it('folds real run updates and distinguishes cache reuse and zero results', () => {
  const model = liveActivity(room, { room_events: [event(1), event(2, 'COMPLETED', { result_count: 0 }), event(3, 'CACHED', { search_ref: 'another', result_count: 4 })] }, true, now);
  expect(model.runs).toHaveLength(2);
  expect(model.runs[0].count).toBe(0);
  expect(model.runs[1].label).toBe('Reused recent results');
  expect(model.runs.every(r => !r.live)).toBe(true);
});
it('shows simultaneous searches and never claims disconnected or stale runs are live', () => {
  const tables = { room_events: [event(1), event(2, 'QUEUED', { search_ref: 'other' })] };
  expect(liveActivity(room, tables, true, now).title).toBe('1 search in progress · 1 queued');
  expect(liveActivity(room, tables, false, now).runs.every(r => !r.live)).toBe(true);
  expect(liveActivity(room, tables, true, now + 46000).runs.every(r => r.stale)).toBe(true);
});
it('excludes private and unrelated room activity, fences input revisions and newer jobs', () => {
  const tables = { room_events: [event(1), { ...event(2), audience: 'OWNER_PRIVATE' }, { ...event(3), roomId: 2n }, event(4, 'RUNNING', { job_ref: 'new' })] };
  expect(liveActivity(room, tables, true, now).runs).toHaveLength(1);
  expect(liveActivity(room, tables, true, now).runs[0].job_ref).toBe('new');
  expect(liveActivity({ ...room, inputRevision: 2n }, tables, true, now).runs).toHaveLength(0);
});
it('unknown provider errors never become public exception text', () => {
  const m = liveActivity(room, { room_events: [event(1, 'FAILED', { error_code: 'PRIVATE_CANARY' })] }, true, now);
  expect(m.runs[0].detail).not.toContain('PRIVATE_CANARY');
});
it('stopped worker never leaves its previous provider run animated as active', () => {
  const stop = { ...event(2), type: 'WORKFLOW_PROGRESS', safePayloadJson: JSON.stringify({ schema_version: 1, job_ref: '1', generation: '1', attempt: '1', input_revision: 1, membership_revision: 1, permission_revision: 1, stage: 'STOPPED', phase: 'FAILED', step_ref: 'stop' }) };
  const m = liveActivity(room, { room_events: [event(1), stop] }, true, now);
  expect(m.title).toBe('Planning needs attention'); expect(m.runs[0].live).toBe(false);
});

it('displays only reported search criteria and hides private or unknown input fields', () => {
 const detail=searchDetails('FLIGHT',{origin:'SAN',destination:'SFO',departure_date:'2027-06-06',leg:'return',result_limit:10,private_reason:'PRIVATE_CANARY',maxStops:0});
 expect(detail.title).toBe('SAN → SFO');expect(detail.facts).toContainEqual(['Return date','2027-06-06']);expect(JSON.stringify(detail)).not.toContain('PRIVATE_CANARY');
 expect(searchDetails('FLIGHT',undefined).reported).toBe(false);
 expect(searchDetails('DINNER',{location:'San Diego',queries:['restaurants for groups']}).facts).toContainEqual(['Searching for','restaurants for groups']);
});
it('carries result exclusions and collection limits without treating them as approved options', () => {
 const model=liveActivity(room,{room_events:[event(1,'COMPLETED',{result_count:10,excluded_count:2,duplicate_count:1,collection_truncated:true,search_context:{origin:'SFO',destination:'SAN'}})]},true,now);
 expect(model.runs[0]).toMatchObject({count:10,excluded:2,duplicates:1,truncated:true,live:false});expect(model.runs[0].search.title).toBe('SFO → SAN');
});

it('an accepted retry is visibly queued until fresh worker events arrive', () => {
  const retry = {...event(3), type:'PLANNING_RETRY_REQUESTED', safePayloadJson:JSON.stringify({job_id:'2',previous_job_id:'1'})};
  const m=liveActivity(room,{room_events:[event(1,'FAILED'),retry]},true,now);
  expect(m.retryPending).toBe(true);expect(m.title).toContain('Retry queued');expect(m.tone).toBe('waiting');
  const started=liveActivity(room,{room_events:[event(1,'FAILED'),retry,event(4,'RUNNING',{job_ref:'2',generation:'2'})]},true,now);
  expect(started.retryPending).toBe(false);expect(started.title).toContain('search in progress');
});

it('fresh agent work remains visible after search telemetry ages, but never revives stopped work', () => {
  const work={roomId:1n,agentId:2n,activity:'WRITING',updatedAt:{microsSinceUnixEpoch:BigInt(now*1000)}};
  const tables={room_events:[event(1,'COMPLETED',{},now-120000)],room_agent_activity:[work]};
  const m=liveActivity(room,tables,true,now);
  expect(m.title).toBe('Your agents are preparing a reply');expect(m.latestTime).toBe(now);
  expect(liveActivity(room,tables,true,now+46000).tone).not.toBe('live');
  expect(liveActivity(room,tables,false,now).title).toContain('Connection lost');
  const stop={...event(2),type:'WORKFLOW_PROGRESS',safePayloadJson:JSON.stringify({schema_version:1,job_ref:'1',generation:'1',attempt:'1',input_revision:1,membership_revision:1,permission_revision:1,stage:'STOPPED',phase:'FAILED'})};
  expect(liveActivity(room,{...tables,room_events:[...tables.room_events,stop]},true,now).title).toBe('Planning needs attention');
});
