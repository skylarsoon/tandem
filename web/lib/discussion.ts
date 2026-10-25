import type { TimelineItem } from './planning';

// Present the audit stream without replaying unchanged evaluations after a
// permission resume. Source records remain untouched in the personal timeline.
// Every displayed/spoken sentence below is copied from a real public utterance.
export function discussionTimeline(items: TimelineItem[]): TimelineItem[] {
  const result: TimelineItem[] = [];
  const latest = new Map<string, { signature: string; item: TimelineItem }>();
  const introductions = new Map<string, TimelineItem>();
  const factsByDraft = new Map<string, Set<string>>();
  for (const item of items) {
    if (item.audience !== 'shared' || item.kind !== 'message' || item.row.senderAgentId == null) continue;
    const text = item.text?.trim();
    const draft = item.row.draftRef;
    if (!text || !draft) { result.push(item); continue; }
    const scope = `${item.row.roomId}:${draft}`;
    const channel = `${scope}:${item.row.senderAgentId}:${item.row.recipientAgentId}`;
    const signature = JSON.stringify([item.row.type, text, [...(item.row.evidenceRefs || [])].sort(), [...(item.row.claimRefs || [])].sort()]);
    const previous = latest.get(channel);
    if (previous?.signature === signature) {
      previous.item.discussionMeta!.deliveries++;
      continue;
    }
    // A changed objection/question is meaningful even if an earlier evaluation
    // used the same wording. Never suppress that transition.
    if (!['PROPOSE', 'EVALUATE'].includes(item.row.type)) introductions.clear();
    const introductionKey = `${scope}:${item.row.senderAgentId}:${text}`;
    if (item.row.type === 'PROPOSE' && introductions.has(introductionKey)) {
      const introduction = introductions.get(introductionKey)!;
      introduction.discussionMeta!.deliveries++;
      introduction.discussionMeta!.recipients = [...new Set([...introduction.discussionMeta!.recipients, String(item.row.recipientAgentId)])];
      latest.set(channel, { signature, item: introduction });
      continue;
    }
    const known = factsByDraft.get(scope) ?? new Set<string>();
    const sentences = text.split(/(?<=[.!?])\s+(?=[A-Z])/u);
    // Only the worker's neutral evidence sentences can be abbreviated. Acts,
    // objections, qualifications and unknown wording are always retained.
    const evidence = (sentence: string) => /^(The fare is |The listed fare is |It lands at |A checked bag is included\.|No checked bag is included\.|Checked-bag coverage is not confirmed\.|Everyone would land between |Shared costs come to )/.test(sentence);
    const compact = sentences.filter(sentence => !evidence(sentence) || !known.has(sentence));
    for (const sentence of sentences) if (evidence(sentence)) known.add(sentence);
    factsByDraft.set(scope, known);
    const shown: TimelineItem = { ...item, text: compact.length ? compact.join(' ') : text,
      discussionMeta: { originalText: text, deliveries: 1, recipients: [String(item.row.recipientAgentId)] } };
    result.push(shown);
    latest.set(channel, { signature, item: shown });
    if (item.row.type === 'PROPOSE') introductions.set(introductionKey, shown);
  }
  return result;
}
