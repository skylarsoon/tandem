import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import catalog from '../lib/decision-catalog.json';
import { allStagesAgreed } from '../lib/trip-completion';
import { tripPhase } from '../lib/plan-presentation';
import { planningStatus } from '../lib/planning';
import { liveActivity } from '../lib/activity';
const room = {id: 36n, settingsJson: JSON.stringify({stages:['FLIGHT','STAY','ACTIVITY','DINNER'],start_date:'2026-11-20',end_date:'2026-11-23'})};
const agreed = (decisionId: string) => ({roomId:36n,decisionId,state:'AGREED'});
const tables = {room_proposals:['flight','stay','activity:1','activity:2','dinner'].map(agreed)};
it('uses the same decision catalog as the worker', () => {
 expect(catalog).toEqual(JSON.parse(readFileSync(new URL('../../contracts/decisions.json', import.meta.url),'utf8')));
});
it('requires every requested decision, including all repeated activities', () => {
 expect(allStagesAgreed(room,tables)).toBe(true);
 expect(allStagesAgreed(room,{room_proposals:tables.room_proposals.filter(p=>p.decisionId!=='activity:2')})).toBe(false);
 expect(allStagesAgreed(room,{room_proposals:tables.room_proposals.map(p=>({...p,roomId:99n}))})).toBe(false);
 expect(allStagesAgreed(room,{room_proposals:tables.room_proposals.map(p=>({...p,state:p.decisionId==='dinner'?'SUPERSEDED':p.state}))})).toBe(false);
 expect(allStagesAgreed(room,{room_proposals:[...tables.room_proposals,{...agreed('dinner'),state:'AWAITING_REVIEW'}]})).toBe(false);
});
it('shows a completed staged trip consistently without pretending it is booked', () => {
 expect(tripPhase(tables,room)).toBe('agreed');
 expect(planningStatus(room,tables,true)).toBe('Agreed · everyone approved');
 expect(liveActivity(room,tables,true,Date.now()).title).toBe('All trip decisions agreed');
 expect(liveActivity(room,tables,true,Date.now()).detail).toContain('Nothing has been reserved');
 expect(liveActivity(room,tables,false,Date.now()).title).toContain('Connection lost');
});
