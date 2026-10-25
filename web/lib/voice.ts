import type { Character } from './agents';
import type { TimelineItem } from './planning';
export const CARTESIA_VERSION = '2026-08-14';
export const voices: Record<Character, string> = {
  lavender: 'db6b0ed5-d5d3-463d-ae85-518a07d3c2b4',
  peach: '62ae83ad-4f6a-430b-af41-a9bede9286ca',
  mint: '47c38ca4-5f35-497b-b1a3-415245fb35e1',
  yellow: '9626c31c-bec5-4cca-baa8-f8ba9e84c8bc',
};
export const mediatorVoice = 'f786b574-daa5-4673-aa0c-cbe3e8534c02';
export function spokenText(item: TimelineItem, discussion: boolean): string | undefined {
  if (discussion !== (item.audience === 'shared')) return;
  if (item.kind === 'question' && item.row.state === 'OPEN') return item.row.prompt;
  // Only real agent utterances. Never narrate private human input, database
  // changes, or pretend the agents said a generated conversation.
  if (item.kind === 'message' && item.row.sender !== 'HUMAN' && /^(agent|shared|private):/.test(item.key)) return item.text?.trim() || undefined;
}
export type Phone = { value: string; start: number; end: number };
export function mouthAt(phones: Phone[], seconds: number): { open: number; width: number } {
  const phone = phones.find(p => seconds >= p.start && seconds < p.end)?.value.toLowerCase();
  if (!phone || /^[mbp\s.]+$/.test(phone)) return { open: 0, width: 1 };
  if (/[ouʊɔɒw]/.test(phone)) return { open: .8, width: .58 };
  if (/[aɑæʌəɛeɜ]/.test(phone)) return { open: .85, width: 1 };
  if (/[iɪjy]/.test(phone)) return { open: .35, width: 1.15 };
  return { open: .23, width: .85 };
}
export async function readSSE(body: ReadableStream<Uint8Array>, receive: (event: Record<string, any>) => void) {
  const reader = body.getReader(), decoder = new TextDecoder(); let pending = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done }).replace(/\r/g, '');
      let boundary: number;
      while ((boundary = pending.indexOf('\n\n')) >= 0) {
        const block = pending.slice(0, boundary); pending = pending.slice(boundary + 2);
        const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        if (data && data !== '[DONE]') receive(JSON.parse(data));
      }
      if (done) break;
      if (pending.length > 2_000_000) throw new Error('Speech stream exceeded its limit.');
    }
  } finally { reader.releaseLock(); }
}
