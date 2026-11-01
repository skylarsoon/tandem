// Isolated browser harness. No network database and no provider calls.
import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import { Dashboard } from '../../app/app/page';
import { ProfileOnboarding } from '../../components/profile';
import { ProductHeader } from '../../components/product-shell';
import { PlanningConversation } from '../../components/planning-room';
import type { Tables } from '../../lib/planning';

declare global { interface Window { productFixture: any } }
const room = {id:1n,goal:'A weekend by the coast',status:'PLANNING',inputRevision:1n,membershipRevision:1n,permissionRevision:1n,inviteCode:'COAST'};
const stamp = { microsSinceUnixEpoch: BigInt(Date.now()) * 1000n };
const base: Tables = {
  my_rooms:[room],my_profile:[{displayName:'Alex'}],my_agent_appearance:[{variant:'lavender'}],
  room_members:[{id:1n,roomId:1n,displayName:'Alex',isMe:true,active:true,role:'ORGANIZER',agentId:11n},{id:2n,roomId:1n,displayName:'Jamie',isMe:false,active:true,agentId:12n}],
  room_agents:[{id:11n,roomId:1n,kind:'PERSONAL'},{id:12n,roomId:1n,kind:'PERSONAL'},{id:13n,roomId:1n,kind:'MEDIATOR'}],
  room_trip_brief:[{roomId:1n,revision:1n,state:'CONFIRMED',briefJson:JSON.stringify({destination:'San Diego',start_date:'2027-06-04',end_date:'2027-06-06',components:['FLIGHT','STAY']})}],
  my_intake:[{roomId:1n,state:'CONFIRMED'}],room_intake_progress:[{roomId:1n,ready:2,total:2}],
  my_private_messages:[{id:1n,roomId:1n,sender:'HUMAN',body:'My private reason stays with my agent.',createdAt:1n},{id:2n,roomId:1n,sender:'AGENT',body:'Your details are saved. I’ll check with you before making a change.',createdAt:2n}],
  room_messages:[{id:1n,roomId:1n,senderAgentId:13n,utterance:'I found a flight and a shared stay. Let’s check whether the plan fits everyone.',createdAt:3n},{id:2n,roomId:1n,senderAgentId:12n,utterance:'The arrival time works. I’ll ask my traveller to review the full plan.',createdAt:4n}],
  room_events:[{id:1n,roomId:1n,audience:'ROOM',type:'RESEARCH_ACTIVITY',createdAt:stamp,safePayloadJson:JSON.stringify({schema_version:1,job_ref:'1',generation:'1',attempt:'1',input_revision:'1',membership_revision:'1',permission_revision:'1',provider:'APIFY',phase:'RUNNING',category:'FLIGHT',search_ref:'flights',round:0,search_context:{origin:'SFO',destination:'SAN',departure_date:'2027-06-04',leg:'outbound',travellers:1,result_limit:10},actor_id:'isolated-test-actor',run_ref:'isolated-test-run',polls:2})}],
  my_rules:[{id:1n,roomId:1n,ruleJson:'{"category":"STAY","attribute":"rating","operator":"gte","value":4.5,"kind":"hard"}',kind:'hard',state:'active',sourceQuote:'A well-reviewed place, please.'},{id:2n,roomId:1n,ruleJson:'{"category":"FLIGHT","attribute":"stops","operator":"lte","value":0,"kind":"hard"}',kind:'hard',state:'active',legacyKey:'max_stops',sourceQuote:'No stops.'}],
  room_proposals:[{id:1n,roomId:1n,stage:'TRAVEL',scope:'COMPLETE_PLAN',decisionId:'travel',version:2,termsDigest:'displayed-terms-only',state:'AWAITING_REVIEW',isReviewer:true,termsJson:JSON.stringify({selected:[{id:'flight',category:'FLIGHT',owner:'Alex',departure_date:'2027-06-04',return_departure_date:'2027-06-06',arrival_local:'08:36',stops:0,return_stops:0,total_cents:14100},{id:'stay',category:'STAY',title:'A place by the coast',check_in:'2027-06-04',check_out:'2027-06-06',bedrooms:2,capacity:4,rating:4.8,total_cents:24000}]}),sharedSummaryJson:'{"text":"A flight and shared stay for your weekend."}'}],
  room_consent_counts:[{roomId:1n,proposalId:1n,required:2,accepted:1,pending:1,declined:0}],
  my_proposal_impacts:[{roomId:1n,proposalId:1n,impactJson:'{"text":"Your share is $261.00."}'}],
};
const calls: any[] = []; let snapshot: any = {status:'ready',appearanceStatus:'ready',tables:base}; const listeners = new Set<()=>void>();
function update(tables: Tables) { snapshot = {...snapshot,tables:{...snapshot.tables,...tables}}; listeners.forEach(fn=>fn()); }
const reducers = new Proxy({}, {get:(_, method)=>async(args:any)=>{
  calls.push({method,args});
  if(method==='saveProfileAnswer') update({my_profile_answers:[...(snapshot.tables.my_profile_answers||[]).filter((a:any)=>a.questionId!==args.questionId),{questionId:args.questionId,status:'ANSWERED',valueJson:args.valueJson}]});
  if(method==='createRoom'||method==='joinRoom') update({my_rooms:[...snapshot.tables.my_rooms,{...room,id:BigInt(snapshot.tables.my_rooms.length+1),goal:args.goal||'A friend’s trip'}]});
  if(method==='setAgentAppearance') update({my_agent_appearance:[{variant:args.variant}]});
  if(method==='setRuleState') update({my_rules:snapshot.tables.my_rules.map((r:any)=>r.id===args.ruleId?{...r,state:args.state}:r)});
}}) as any;
const client:any = {session:{subscribe:(fn:()=>void)=>{listeners.add(fn);return()=>listeners.delete(fn);},getSnapshot:()=>snapshot,select:()=>{}},connection:{reducers}};
function Fixture(){
  const [screen,setScreen] = useState('home'); const [tables,setTables] = useState(base); const [connected,setConnected]=useState(true);
  window.productFixture={calls,screen:setScreen,update,room:room,setConnected,patch:(patch:Tables)=>setTables(t=>({...t,...patch}))};
  return <main className="product-app planning-app"><ProductHeader active={screen==='profile'?'profile':'trips'}/>{screen==='home'?<Dashboard client={client}/>:screen==='profile'?<ProfileOnboarding client={client}/>:<div className="test-room-shell"><PlanningConversation room={room} tables={tables} reducers={reducers} connected={connected}/></div>}</main>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
