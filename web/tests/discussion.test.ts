import { describe, it, expect } from 'vitest';
import { discussionTimeline } from '../lib/discussion';
import { spokenText } from '../lib/voice';
import type { TimelineItem } from '../lib/planning';
const facts = 'The fare is $128.00 including taxes. It lands at 18:38 local time. No checked bag is included.';
function message(id: number, sender = 1, type = 'EVALUATE', text = `This works for my traveller; they will still review and approve it themselves. ${facts}`, changes = {}): TimelineItem {
  return { key: `shared:${id}:`, kind: 'message', audience: 'shared', speaker: 'Agent', time: BigInt(id), text,
    row: { id, roomId: 1, senderAgentId: sender, recipientAgentId: 9, draftRef: 'draft-a', type, claimRefs: ['act:'+type], evidenceRefs: ['flight-a'], ...changes } };
}
describe('discussion presentation of real audit messages', () => {
  it('collapses resumed identical evaluations without mutating the audit records', () => {
    const source = [message(1), message(2, 2), message(3)];
    const output = discussionTimeline(source);
    expect(output).toHaveLength(2); expect(output[0].key).toBe(source[0].key);
    expect(output[0].discussionMeta?.deliveries).toBe(2);
    expect(source[0].discussionMeta).toBeUndefined(); expect(source[0].text).toContain(facts);
  });
  it('groups the same mediator proposal sent to different agents and keeps recipients', () => {
    const output = discussionTimeline([message(1, 9, 'PROPOSE', 'Could you evaluate this option? '+facts, { recipientAgentId: 1 }), message(2, 9, 'PROPOSE', 'Could you evaluate this option? '+facts, { recipientAgentId: 2, evidenceRefs: ['flight-b'] })]);
    expect(output).toHaveLength(1); expect(output[0].discussionMeta?.recipients).toEqual(['1', '2']);
  });
  it('speaks evidence once but preserves each distinct agent decision and full text', () => {
    const output = discussionTimeline([message(1, 9, 'PROPOSE', 'Could you evaluate this option? '+facts), message(2), message(3, 2)]);
    expect(output).toHaveLength(3);
    expect(spokenText(output[0], true)).toContain('$128.00');
    expect(spokenText(output[1], true)).toBe('This works for my traveller; they will still review and approve it themselves.');
    expect(output[1].discussionMeta?.originalText).toContain('$128.00');
  });
  it('retains changed prices, evidence, drafts, and rooms', () => {
    expect(discussionTimeline([message(1), message(2, 1, 'EVALUATE', 'Changed fare. The fare is $150.00 including taxes.')])).toHaveLength(2);
    expect(discussionTimeline([message(1), message(2, 1, 'EVALUATE', undefined, { evidenceRefs: ['flight-b'] })])).toHaveLength(2);
    for (const change of [{ draftRef: 'draft-b' }, { roomId: 2 }]) {
      const output = discussionTimeline([message(1), message(2, 1, 'EVALUATE', undefined, change)]);
      expect(output).toHaveLength(2); expect(output[1].text).toContain(facts);
    }
  });
  it('keeps objections and an evaluation after an objection', () => {
    const output = discussionTimeline([message(1), message(2, 1, 'OBJECT', 'This option is outside what my traveller can commit to.'), message(3)]);
    expect(output).toHaveLength(3); expect(output[1].text).toContain('outside');
  });
  it('does not abbreviate objections, clarifications, or unknown qualifications', () => {
    const output = discussionTimeline([message(1), message(2, 2, 'CLARIFY', 'Could you confirm the baggage? This price is not confirmed.')]);
    expect(output[1].text).toBe('Could you confirm the baggage? This price is not confirmed.');
  });
  it('excludes private messages and organizer text; unknown drafts stay intact', () => {
    const publicMessage = message(1);
    expect(discussionTimeline([{ ...publicMessage, audience: 'private' }, { ...publicMessage, row: {} }])).toEqual([]);
    expect(discussionTimeline([message(1, 1, 'EVALUATE', undefined, { draftRef: undefined }), message(2, 1, 'EVALUATE', undefined, { draftRef: undefined })])).toHaveLength(2);
  });
});
