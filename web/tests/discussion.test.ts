import { describe, it, expect } from 'vitest';
import { discussionTimeline } from '../lib/discussion';
import { spokenText } from '../lib/voice';
import type { TimelineItem } from '../lib/planning';
const facts = 'The fare is $128.00 including taxes. It lands at 18:38 local time. No checked bag is included.';
function message(id: number, sender = 1, type = 'EVALUATE', text = `This works for my traveller; they will still review and approve it themselves. ${facts}`, changes = {}): TimelineItem {
  return { key: `shared:${id}:`, kind: 'message', audience: 'shared', speaker: 'Agent', time: BigInt(id), text,
    row: { id, roomId: 1, senderAgentId: sender, recipientAgentId: 9, draftRef: 'draft-a', type, claimRefs: ['act:'+type], evidenceRefs: ['flight-a'], ...changes } };
}
describe('raw backend discussion', () => {
  it('preserves separate messages even when wording and recipient are identical', () => {
    const source = [message(1), message(2, 2), message(3)];
    expect(discussionTimeline(source)).toEqual(source);
    expect(discussionTimeline(source)[0]).toBe(source[0]);
  });
  it('does not group mediator messages addressed to different agents', () => {
    const source = [message(1, 9, 'PROPOSE', facts, { recipientAgentId: 1 }), message(2, 9, 'PROPOSE', facts, { recipientAgentId: 2 })];
    expect(discussionTimeline(source)).toEqual(source);
  });
  it('displays and speaks the complete published utterance, including repeated evidence', () => {
    const source = [message(1), message(2, 2)];
    const output = discussionTimeline(source);
    for (let i=0; i<output.length; i++) {
      expect(output[i].text).toBe(source[i].text);
      expect(spokenText(output[i], true)).toBe(source[i].text);
    }
  });
  it('only deduplicates subscription redelivery of the same room/message identity', () => {
    expect(discussionTimeline([message(1), message(1), message(1, 1, 'EVALUATE', facts, {roomId:2})])).toHaveLength(2);
  });
  it('keeps objections, qualifications and resumed evaluations verbatim', () => {
    const source = [message(1), message(2, 1, 'OBJECT', 'This does not fit. Some fees are unknown.'), message(3)];
    expect(discussionTimeline(source)).toEqual(source);
  });
  it('excludes private messages and organizer text', () => {
    const item = message(1);
    expect(discussionTimeline([{...item, audience:'private'}, {...item,row:{}}])).toEqual([]);
  });
});
