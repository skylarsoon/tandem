import { describe, it, expect, vi, afterEach } from 'vitest';
import { mouthAt, readSSE, spokenText, voices, mediatorVoice } from '../lib/voice';
import { memberCharacter, myCharacter } from '../lib/agents';
import { outgoingMessage, type TimelineItem } from '../lib/planning';
import { recoveryAction, questionAction } from '../lib/state';
import { POST } from '../app/api/voice/session/route';
function item(patch: Partial<TimelineItem>): TimelineItem { return { key: 'agent:1:', kind: 'message', audience: 'private', speaker: 'Agent', row: {}, text: 'Your budget is noted.', time: 1n, ...patch }; }
afterEach(() => vi.restoreAllMocks());
describe('voice privacy and timing', () => {
  it('uses distinct voices for four companions and the mediator', () => {
    expect(new Set([...Object.values(voices), mediatorVoice]).size).toBe(5);
  });
  it('speaks only agent text in the selected audience', () => {
    expect(spokenText(item({}), false)).toBe('Your budget is noted.');
    expect(spokenText(item({}), true)).toBeUndefined();
    expect(spokenText(item({ row: { sender: 'HUMAN' } }), false)).toBeUndefined();
    expect(spokenText(item({ key: 'description:1:', audience: 'shared' }), true)).toBeUndefined();
    expect(spokenText(item({ kind: 'event' }), false)).toBeUndefined();
    expect(spokenText(item({ key: 'shared:1:', audience: 'shared' }), true)).toBe('Your budget is noted.');
  });
  it('closes the mouth in gaps and on bilabials, using provider timing', () => {
    const phones = [{ value: 'm', start: .1, end: .2 }, { value: 'a', start: .2, end: .4 }, { value: 'u', start: .4, end: .6 }];
    expect(mouthAt(phones, .15).open).toBe(0);
    expect(mouthAt(phones, .3).open).toBeGreaterThan(.5);
    expect(mouthAt(phones, .5).width).toBeLessThan(1);
    expect(mouthAt(phones, .7).open).toBe(0);
  });
  it('parses fragmented SSE with multiple events without losing chunks', async () => {
    const encoder = new TextEncoder(), events: unknown[] = [];
    const stream = new ReadableStream({ start(controller) {
      for (const part of ['data: {"type":"ch', 'unk","data":"AA=="}\r\n', '\r\ndata: {"type":"done"}\n\n']) controller.enqueue(encoder.encode(part));
      controller.close();
    } });
    await readSSE(stream, event => events.push(event));
    expect(events).toEqual([{ type: 'chunk', data: 'AA==' }, { type: 'done' }]);
  });
  it('spoken confirm remains a private message without explicit owner approval', () => {
    expect(outgoingMessage({ id: 1n }, { my_intake: [{ state: 'SUMMARY', coverageJson: '{"origin":"ANSWERED","money":"ANSWERED"}' }] }, 'confirm').kind).toBe('private');
  });
  it('assigns four distinct colors consistently across owners and shuffled subscriptions', () => {
    const members = [1n, 2n, 3n, 4n].map(id => ({ id, active: true, isMe: id === 2n }));
    const first = { room_members: members }, shuffled = { room_members: [...members].reverse() };
    const colors = members.map(member => memberCharacter(first, member));
    expect(new Set(colors).size).toBe(4);
    expect(members.map(member => memberCharacter(shuffled, member))).toEqual(colors);
    expect(myCharacter(first)).toBe(colors[1]);
  });
});
describe('scoped Cartesia session', () => {
  it('accepts the real request Host when Next has normalized the internal URL', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ token: 'test-scoped' }));
    const response = await POST(new Request('http://localhost:3003/api/voice/session', { method: 'POST', headers: { host: '127.0.0.1:3003', origin: 'http://127.0.0.1:3003' }, body: JSON.stringify({ key: 'fake-test-key' }) }));
    expect(response.status).toBe(200);
  });

  it('rejects cross-origin calls without forwarding credentials', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch');
    const response = await POST(new Request('http://127.0.0.1:3003/api/voice/session', { method: 'POST', headers: { origin: 'https://elsewhere.test' }, body: '{}' }));
    expect(response.status).toBe(403); expect(fetch).not.toHaveBeenCalled();
  });
  it('returns only a short-lived token with TTS and STT permissions', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ token: 'scoped-test-token' }));
    const response = await POST(new Request('http://127.0.0.1:3003/api/voice/session', { method: 'POST', headers: { origin: 'http://127.0.0.1:3003' }, body: JSON.stringify({ key: 'fake-test-key' }) }));
    const data = await response.json(); expect(data.token).toBe('scoped-test-token'); expect(JSON.stringify(data)).not.toContain('fake-test-key');
    expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual({ grants: { tts: true, stt: true }, expires_in: 600 });
    expect(response.headers.get('cache-control')).toBe('no-store');
  });
});

describe('new backend question controls', () => {
  it('binds recovery to an offered choice and the displayed question version', () => {
    const question = { id: 8n, version: 3, state: 'OPEN', kind: 'PLAN_RECOVERY', actionJson: '{"choices":[{"id":"search","label":"Search again"}]}' };
    expect(recoveryAction(question, 'search')).toEqual({ questionId: 8n, version: 3, answerJson: '{"choice":"search"}' });
    expect(() => recoveryAction(question, 'invented')).toThrow();
    expect(() => recoveryAction({ ...question, state: 'ANSWERED' }, 'search')).toThrow();
  });
  it('keeps an optional decline reason with the permission response', () => {
    const answer = questionAction({ id: 1n, version: 2, state: 'OPEN', kind: 'CONCESSION_CONSULTATION' }, false, {}, 'Too expensive');
    expect(JSON.parse(answer.answerJson)).toEqual({ granted: false, reason: 'Too expensive' });
  });
});
