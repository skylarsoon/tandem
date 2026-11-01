import catalog from './decision-catalog.json';
import { json, type Row } from './state';

// Match the worker's catalog-defined decision IDs, including repeated activities.
// A stage milestone alone cannot prove that all the requested decisions exist.
export function allStagesAgreed(room: Row, tables: Record<string, Row[]>): boolean {
  const settings = json<Row>(room.settingsJson, {});
  const stages = settings.stages;
  if (!Array.isArray(stages) || !stages.length) return false;
  const proposals = (tables.room_proposals || []).filter(p => String(p.roomId) === String(room.id));
  if (proposals.some(p => p.state === 'AWAITING_REVIEW')) return false;
  const agreed = new Set(proposals.filter(p => p.state === 'AGREED').map(p => p.decisionId));
  return stages.every(category => {
    const shape = (catalog.categories as Record<string, { repeats_setting?: string }>)[category];
    if (!shape) return false;
    if (!shape.repeats_setting) return agreed.has(category.toLowerCase());
    let count = settings[shape.repeats_setting];
    if (count == null && settings.start_date && settings.end_date) {
      count = Math.max(1, (Date.parse(settings.end_date) - Date.parse(settings.start_date)) / 86400000 - 1);
    }
    count = count ?? 1;
    if (!Number.isInteger(count) || count < 1 || count > 366) return false;
    return Array.from({length: count}, (_, i) => `${category.toLowerCase()}:${i + 1}`).every(id => agreed.has(id));
  });
}
