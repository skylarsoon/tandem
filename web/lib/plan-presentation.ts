import { type Row } from './state';
import type { Tables } from './planning';

export type TripPhase = 'brief' | 'intake' | 'planning' | 'review' | 'agreed';
export function tripPhase(tables: Tables): TripPhase {
  const proposals = (tables.room_proposals || []).filter(p => p.state !== 'SUPERSEDED');
  if (proposals.some(p => p.state === 'AWAITING_REVIEW')) return 'review';
  if (proposals.some(p => p.state === 'AGREED' && p.scope === 'COMPLETE_PLAN')) return 'agreed';
  if (!tables.room_trip_brief?.some(b => b.state === 'CONFIRMED')) return 'brief';
  if (tables.my_intake?.[0]?.state !== 'CONFIRMED') return 'intake';
  return 'planning';
}
export const phaseLabels: Record<TripPhase, string> = { brief: 'Shape the trip', intake: 'Share your needs', planning: 'Explore together', review: 'Review the plan', agreed: 'Agreed together' };
export function proposalGroups(proposals: Row[]) {
  const groups = new Map<string, Row[]>();
  for (const proposal of proposals.filter(p => p.state !== 'SUPERSEDED')) {
    const key = proposal.stage || proposal.scope || 'COMPLETE_PLAN';
    groups.set(key, [...(groups.get(key) || []), proposal]);
  }
  return [...groups];
}
export function optionTitle(candidate: Row): string {
  if (candidate.title) return candidate.title;
  if (candidate.category === 'FLIGHT') return candidate.return_departure_date ? 'Round-trip flight' : 'Flight';
  return candidate.category === 'STAY' ? 'A place to stay' : 'Selected option';
}
export function optionFacts(c: Row): [string, string][] {
  const facts: [string, string][] = [];
  const add = (label: string, value: unknown) => { if (value !== undefined && value !== null && value !== '') facts.push([label, String(value)]); };
  if (c.category === 'FLIGHT') {
    add('Outbound', c.departure_date);
    if (c.arrival_local) add('Arrives', `${c.arrival_local}${c.arrival_timezone ? ` · ${c.arrival_timezone.replaceAll('_', ' ')}` : ' local'}`);
    add('Connections', c.stops === 0 ? 'Nonstop' : c.stops);
    add('Return', c.return_departure_date);
    if (c.return_departure_date) add('Return connections', c.return_stops === 0 ? 'Nonstop' : c.return_stops);
    add('Checked bags', c.checked_bags_included != null ? c.checked_bags_included : c.baggage_included === false ? 'Not included' : undefined);
  } else if (c.category === 'STAY') {
    add('Check-in', c.check_in); add('Check-out', c.check_out);
    add('Bedrooms', c.bedrooms); add('Sleeps', c.capacity); add('Rating', c.rating);
  }
  return facts;
}
