import { expect, it } from 'vitest';
import { tripPhase, optionFacts, proposalGroups } from '../lib/plan-presentation';
import { liveActivity } from '../lib/activity';
it('keeps partial agreements in planning and never borrows another room agreement', () => {
 const tables={room_trip_brief:[{state:'CONFIRMED'}],my_intake:[{state:'CONFIRMED'}],my_rooms:[{id:2n,status:'AGREED'}],room_proposals:[{state:'AGREED',scope:'INDIVIDUAL_FLIGHT'}]};
 expect(tripPhase(tables)).toBe('planning');
 expect(tripPhase({...tables,room_proposals:[{state:'AGREED',scope:'COMPLETE_PLAN'}]})).toBe('agreed');
 expect(tripPhase({...tables,room_proposals:[{state:'AGREED',scope:'COMPLETE_PLAN'},{state:'AWAITING_REVIEW'}]})).toBe('review');
});
it('shows both flight directions without inventing missing return details', () => {
 const facts=optionFacts({category:'FLIGHT',departure_date:'2027-06-04',return_departure_date:'2027-06-06',stops:0,return_stops:1,checked_bags_included:0});
 expect(facts).toContainEqual(['Return','2027-06-06']);expect(facts).toContainEqual(['Connections','Nonstop']);expect(facts).toContainEqual(['Checked bags','0']);
 expect(optionFacts({category:'FLIGHT',stops:0})).not.toContainEqual(['Return','undefined']);
});
it('keeps superseded decisions out of the current plan groups', () => {
 expect(proposalGroups([{id:1,stage:'TRAVEL',state:'SUPERSEDED'},{id:2,stage:'TRAVEL',state:'AWAITING_REVIEW'}])).toEqual([['TRAVEL',[{id:2,stage:'TRAVEL',state:'AWAITING_REVIEW'}]]]);
});
it('does not identify another traveller as owing a private answer in shared activity', () => {
 const model=liveActivity({id:1n},{room_members:[{agentId:2n,displayName:'Jamie',isMe:false}],room_agent_activity:[{roomId:1n,agentId:2n,activity:'WAITING_FOR_OWNER'}]},true,Date.now());
 expect(model.agents[0].label).toBe('Ready');
});
