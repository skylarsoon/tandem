import type { TimelineItem } from './planning';

// Display published backend utterances verbatim. Repeated wording with distinct
// message IDs is a distinct turn; only subscription redelivery of an ID is ignored.
export function discussionTimeline(items: TimelineItem[]): TimelineItem[] {
  const seen = new Set<string>();
  return items.filter(item => {
    if (item.audience !== 'shared' || item.kind !== 'message' || item.row.senderAgentId == null) return false;
    const identity = `${item.row.roomId}:${item.row.id ?? item.key}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}
