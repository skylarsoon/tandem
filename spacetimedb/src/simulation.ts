import { Identity } from 'spacetimedb';
import { t } from 'spacetimedb/server';
import spacetimedb from './schema';
import { hasAppAccess, participantIdentity } from './access';
import { type Ctx, closeJobThreads, createJob, digest, emitEvent, fail, OPEN_JOB_STATUSES, requireHuman, withReceipt } from './guards';
import { createRoom, retryPlanning } from './rooms';
import { upsertConstraint } from './effects';
import { answerQuestion } from './questions';
import { respondToProposal } from './proposals';

const crew = [
  { name: 'Ari Demo', origin: 'SFO', budget: 55000, color: 'lavender', stops: 0, note: 'I prefer nonstop flights and a simple journey.' },
  { name: 'Maya Demo', origin: 'LAX', budget: 40000, color: 'peach', stops: 1, note: 'Keeping costs down matters most; I can take a connection.' },
  { name: 'Leo Demo', origin: 'SEA', budget: 65000, color: 'mint', stops: 0, note: 'I prefer fewer connections and a relaxed pace.' },
  { name: 'Sam Demo', origin: 'PDX', budget: 60000, color: 'yellow', stops: 1, note: 'I am flexible about the route so the group can find a workable plan.' },
];

// Only an owned simulation may delegate to its unloginable, synthetic identities.
// Normal reducers still enforce constraints, question versions and proposal terms.
function demoContext(ctx: Ctx, owner: Identity): Ctx {
  return new Proxy(ctx, { get(target, key) { return key === 'sender' ? owner : Reflect.get(target, key, target); } });
}
function owned(ctx: Ctx, roomId: bigint) {
  requireHuman(ctx);
  const run = ctx.db.planningSimulation.roomId.find(roomId);
  if (!run || !run.owner.isEqual(participantIdentity(ctx.db, ctx.sender))) fail('NOT_AUTHORIZED', 'this simulation belongs to another account');
  return run;
}
function demoMember(ctx: Ctx, roomId: bigint, memberId: bigint) {
  owned(ctx, roomId);
  const member=ctx.db.membership.id.find(memberId);
  if (!member || member.roomId !== roomId || !ctx.db.appAccess.identity.find(member.member)?.invitationHash.startsWith('simulation:')) fail('NOT_AUTHORIZED', 'demo traveller required');
  return member;
}
function date(value: string) {
  const ms=Date.parse(value+'T00:00:00Z');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(ms) || new Date(ms).toISOString().slice(0,10)!==value) fail('INVALID_ARGUMENT','choose a valid date');
  return ms;
}
export const startPlanningSimulation = spacetimedb.reducer({ startDate:t.string(), endDate:t.string(), requestKey:t.string() }, (ctx,args)=>{
  requireHuman(ctx);
  const owner=participantIdentity(ctx.db,ctx.sender), now=Number(ctx.timestamp.microsSinceUnixEpoch/1000n);
  if(!/^[A-Za-z0-9_-]{8,80}$/.test(args.requestKey)) fail('INVALID_ARGUMENT','invalid request key');
  const day=new Date(now).toISOString().slice(0,10);
  const mine=[...ctx.db.planningSimulation.byOwner.filter(owner)];
  if(mine.some(r=>r.requestKey===args.requestKey)) return;
  if(mine.some(r=>r.day===day)) fail('SIMULATION_DAILY_LIMIT','one new simulation per account per UTC day; reopen your saved run');
  if([...ctx.db.planningSimulation.byDay.filter(day)].length>=3) fail('SIMULATION_CAPACITY','today’s three shared simulation slots have been used');
  const from=date(args.startDate),to=date(args.endDate);
  if(from<now+7*86400000 || from>now+180*86400000 || to-from<86400000 || to-from>7*86400000) fail('INVALID_ARGUMENT','choose a 1–7 night trip starting 8–180 days from now');
  const identities=crew.map((_,i)=>Identity.fromString(digest(`simulation:${owner.toHexString()}:${ctx.timestamp.microsSinceUnixEpoch}:${args.requestKey}:${i}`)));
  for(const identity of identities) {
    if(ctx.db.appAccess.identity.find(identity) || ctx.db.profile.owner.find(identity)) fail('INVALID_STATE','demo identity collision');
    ctx.db.appAccess.insert({identity,invitationHash:`simulation:${args.requestKey}`,grantedAt:ctx.timestamp,revoked:false});
  }
  createRoom(demoContext(ctx,identities[0]),{goal:'San Diego · planning simulation',displayName:crew[0].name});
  const roomId=[...ctx.db.membership.byMember.filter(identities[0])][0].roomId;
  const original=ctx.db.room.id.find(roomId)!;
  const settings={destination:'San Diego, California',destination_airport:'SAN',start_date:args.startDate,end_date:args.endDate,components:['FLIGHT'],stages:['FLIGHT'],arrive_together:'preferred',arrival_overlap_seconds:14400};
  const room=ctx.db.room.id.update({...original,settingsJson:JSON.stringify(settings)});
  ctx.db.planningSimulation.insert({roomId,owner,requestKey:args.requestKey,day,state:'ACTIVE',retries:0,createdAt:ctx.timestamp});
  ctx.db.tripBrief.insert({roomId,description:'Fictional travellers exploring real round-trip flights to San Diego. Demo presets, not real user preferences.',briefJson:JSON.stringify(settings),unclearJson:'[]',state:'CONFIRMED',revision:1n,updatedAt:ctx.timestamp});
  crew.forEach((person,i)=>{
    const identity=identities[i];
    if(i) {
      const m=ctx.db.membership.insert({id:0n,roomId,member:identity,displayName:person.name,role:'PARTICIPANT',active:true,joinedAt:ctx.timestamp});
      ctx.db.agent.insert({id:0n,roomId,kind:'PERSONAL',membershipId:m.id});
      ctx.db.profile.insert({owner:identity,displayName:person.name,avatarRef:'',selfDescription:person.note,priorityRanking:[],revision:1n,updatedAt:ctx.timestamp});
      ctx.db.participationPreferences.insert({owner:identity,speakUpForMe:true,askMeFirst:false,giveMeTime:false,remindersOptedIn:false,confirmed:true,confirmedAt:ctx.timestamp,proposedBy:'SIMULATION_PRESET',revision:1n});
    } else {
      ctx.db.profile.owner.update({...ctx.db.profile.owner.find(identity)!,selfDescription:person.note});
      ctx.db.participationPreferences.owner.update({...ctx.db.participationPreferences.owner.find(identity)!,speakUpForMe:true,askMeFirst:false,giveMeTime:false,confirmed:true,confirmedAt:ctx.timestamp,proposedBy:'SIMULATION_PRESET',revision:1n});
    }
    ctx.db.agentAppearance.insert({owner:identity,variant:person.color});
    const member=[...ctx.db.membership.byRoomMember.filter([roomId,identity])][0];
    ctx.db.intake.insert({id:0n,roomId,owner:identity,memberId:member.id,state:'CONFIRMED',coverageJson:JSON.stringify({origin:'ANSWERED',dates:'ANSWERED',money:'ANSWERED',settings:'ANSWERED'}),questionCount:0,briefRevision:1n,createdAt:ctx.timestamp,updatedAt:ctx.timestamp});
    for(const [key,value,hardness] of [['origin_airport',person.origin,'HARD'],['flight_budget_total',person.budget,'HARD'],['dates_ok',true,'HARD'],['max_stops',person.stops,'SOFT']] as const) {
      upsertConstraint(demoContext(ctx,identity),roomId,{key,valueJson:JSON.stringify(value),unit:key==='flight_budget_total'?'USD_cents':'',hardness,sourceQuote:'Explicit fictional demo preset'},false);
    }
  });
  createJob(ctx,room,'PLAN','owner started isolated planning simulation');
  emitEvent(ctx,roomId,'SIMULATION_STARTED',{message:'Four fictional travellers; real provider searches and backend negotiation. No bookings.'});
});

export const stopPlanningSimulation=spacetimedb.reducer({roomId:t.u64()},(ctx,{roomId})=>{
  const run=owned(ctx,roomId);
  if(run.state==='STOPPED') return;
  ctx.db.planningSimulation.roomId.update({...run,state:'STOPPED'});
  for(const j of ctx.db.job.byRoom.filter(roomId)) if(OPEN_JOB_STATUSES.includes(j.status)) {
    ctx.db.job.id.update({...j,status:'SUPERSEDED',leaseOwner:undefined,leaseExpiresAt:undefined,updatedAt:ctx.timestamp});
    closeJobThreads(ctx,j.id,'SIMULATION_STOPPED');
  }
  for(const q of ctx.db.question.byRoom.filter(roomId)) if(q.state==='OPEN') ctx.db.question.id.update({...q,state:'SUPERSEDED'});
  emitEvent(ctx,roomId,'SIMULATION_STOPPED',{message:'Stopped by the simulation owner. A provider request already in progress may still finish.'});
});
export const answerSimulationQuestion=spacetimedb.reducer({roomId:t.u64(),questionId:t.u64(),version:t.u32(),answerJson:t.string()},(ctx,args)=>{
  const run=owned(ctx,args.roomId);
  if(run.state!=='ACTIVE') fail('INVALID_STATE','simulation is stopped');
  const q=ctx.db.question.id.find(args.questionId);
  if(!q || q.roomId!==args.roomId) fail('NOT_AUTHORIZED','question does not belong to this simulation');
  const m=[...ctx.db.membership.byRoomMember.filter([args.roomId,q.owner])][0];
  if(!m) fail('NOT_FOUND','demo traveller');
  demoMember(ctx,args.roomId,m.id);
  if(args.answerJson.length>8000) fail('INVALID_ARGUMENT','answer too long');
  answerQuestion(demoContext(ctx,q.owner),{questionId:q.id,version:args.version,answerJson:args.answerJson});
});
export const reviewSimulationProposal=spacetimedb.reducer({roomId:t.u64(),memberId:t.u64(),proposalId:t.u64(),version:t.u32(),termsDigest:t.string(),response:t.string()},(ctx,args)=>{
  const run=owned(ctx,args.roomId), member=demoMember(ctx,args.roomId,args.memberId);
  if(run.state!=='ACTIVE') fail('INVALID_STATE','simulation is stopped');
  const p=ctx.db.proposal.id.find(args.proposalId);
  if(!p || p.roomId!==args.roomId) fail('NOT_AUTHORIZED','proposal does not belong to this simulation');
  if(!['ACCEPT','NOT_COMFORTABLE'].includes(args.response)) fail('INVALID_ARGUMENT','unsupported demo decision');
  respondToProposal(demoContext(ctx,member.member),{proposalId:p.id,version:args.version,termsDigest:args.termsDigest,response:args.response,note:'Explicit decision made by the simulation operator for a fictional traveller.'});
});
export const retryPlanningSimulation=spacetimedb.reducer({roomId:t.u64(),jobId:t.u64(),requestKey:t.string()},(ctx,args)=>{
  const run=owned(ctx,args.roomId);
  withReceipt(ctx, 'retry_planning_simulation', args.requestKey, args, () => {
    if(run.state!=='ACTIVE' || run.retries>=2) fail('SIMULATION_RETRY_LIMIT','this simulation cannot be retried again');
    const room=ctx.db.room.id.find(args.roomId)!;
    retryPlanning(demoContext(ctx,room.organizer),args);
    ctx.db.planningSimulation.roomId.update({...run,retries:run.retries+1});
    return {};
  });
});

const SimulationRow=t.row('SimulationRow',{roomId:t.u64().primaryKey(),state:t.string(),createdAt:t.timestamp(),snapshotJson:t.string()});
export const myPlanningSimulations=spacetimedb.view({name:'my_planning_simulations',public:true},t.array(SimulationRow),ctx=>{
  if(!hasAppAccess(ctx.db,ctx.sender)) return [];
  return [...ctx.db.planningSimulation.byOwner.filter(participantIdentity(ctx.db,ctx.sender))].sort((a,b)=>a.roomId>b.roomId?-1:1).slice(0,5).map(run=>{
    const room=ctx.db.room.id.find(run.roomId)!;
    const members=[...ctx.db.membership.byRoomMember.filter(run.roomId)].sort((a,b)=>a.id<b.id?-1:1);
    const agents=[...ctx.db.agent.byRoom.filter(run.roomId)];
    const memberId=(identity:Identity)=>members.find(m=>m.member.isEqual(identity))?.id;
    const jobs=[...ctx.db.job.byRoom.filter(run.roomId)].sort((a,b)=>a.id>b.id?-1:1);
    const proposals=[...ctx.db.proposal.byRoomStage.filter(run.roomId)].slice(-10);
    const snapshot={settings:JSON.parse(room.settingsJson),roomStatus:room.status,retries:run.retries,
      members:members.map((m,i)=>({id:m.id,agentId:agents.find(a=>a.membershipId===m.id)?.id,...crew[i],activity:ctx.db.agentStatus.agentId.find(agents.find(a=>a.membershipId===m.id)!.id)?.activity,updatedAt:ctx.db.agentStatus.agentId.find(agents.find(a=>a.membershipId===m.id)!.id)?.updatedAt})),
      mediatorId:agents.find(a=>a.kind==='MEDIATOR')?.id,
      job:jobs[0] ? {id:jobs[0].id,status:jobs[0].status,outcome:jobs[0].outcome,updatedAt:jobs[0].updatedAt} : null,
      messages:[...ctx.db.agentMessage.byRoom.filter(run.roomId)].filter(m=>m.effectiveAudience==='ROOM').slice(-200).map(m=>({id:m.id,senderAgentId:m.senderAgentId,recipientAgentId:m.recipientAgentId,utterance:m.utterance,type:m.type,createdAt:m.createdAt})),
      events:[...ctx.db.decisionEvent.byRoom.filter(run.roomId)].filter(e=>e.audience==='ROOM').slice(-300).map(e=>({id:e.id,type:e.type,payload:JSON.parse(e.safePayloadJson),createdAt:e.createdAt})),
      questions:[...ctx.db.question.byRoom.filter(run.roomId)].filter(q=>q.state==='OPEN').map(q=>({id:q.id,memberId:memberId(q.owner),kind:q.kind,prompt:q.prompt,version:q.version,action:JSON.parse(q.actionJson)})),
      proposals:proposals.map(p=>({id:p.id,version:p.version,state:p.state,termsDigest:p.termsDigest,terms:JSON.parse(p.termsJson),summary:JSON.parse(p.sharedSummaryJson),consents:[...ctx.db.consent.byProposalMember.filter(p.id)].map(c=>({memberId:memberId(c.member),response:c.response})),impacts:members.flatMap(m=>[...ctx.db.participantImpact.byOwner.filter(m.member)].filter(x=>x.proposalId===p.id).map(x=>({memberId:m.id,impact:JSON.parse(x.impactJson)})))})),
      candidates:[...ctx.db.candidate.byRoomRef.filter(run.roomId)].slice(-100).map(c=>({ref:c.candidateRef,category:c.category,mode:c.mode,url:c.sourceUrl,facts:JSON.parse(c.factsJson),missing:c.missingFields})),
    };
    return {roomId:run.roomId,state:run.state,createdAt:run.createdAt,snapshotJson:JSON.stringify(snapshot,(_,v)=>typeof v==='bigint'?v.toString():v)};
  });
});
