import { test, expect, type Page } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { createServer, type Server } from 'node:http';
const bundle = buildSync({ entryPoints: [path.resolve('e2e/fixtures/planning.tsx')], bundle: true, write: false, format: 'iife', platform: 'browser', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"development"' } }).outputFiles[0].text;
let origin: string, server: Server;
test.beforeAll(async () => {
  // AudioWorklet module loads bypass Playwright routing. Serve that real module
  // on an OS-assigned ephemeral port; all app/provider traffic stays intercepted.
  server = createServer((_request, response) => { response.setHeader('Content-Type', 'application/javascript'); response.end(readFileSync(path.resolve('public/voice/pcm-capture.js'))); });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${(server.address() as {port: number}).port}`;
});
test.afterAll(async () => { await new Promise<void>(resolve => server.close(() => resolve())); });
const css = readFileSync(path.resolve('app/globals.css'), 'utf8');
async function open(page: Page) {
  await page.route(`${origin}/**`, async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/voice/session') return route.fulfill({ json: { token: 'test-scoped-token', expiresAt: Date.now() + 600000 } });
    if (pathname.startsWith('/agents/') || pathname.startsWith('/voice/')) return route.fulfill({ body: readFileSync(path.resolve('public', `.${pathname}`)), contentType: pathname.endsWith('.js') ? 'application/javascript' : 'image/png' });
    return route.fulfill({ contentType: 'text/html', body: `<html><head><style>${css}</style></head><body><div id="root"></div><script>${bundle.replaceAll('</script', '<\\/script')}</script></body></html>` });
  });
  await page.goto(origin);
  await expect(page.getByRole('heading', { name: 'Your trip, in conversation' })).toBeVisible();
}
function speech() {
  const bytes = Buffer.alloc(24000 * 2);
  return [ { type: 'phoneme_timestamps', phoneme_timestamps: { phonemes: ['a'], start: [0], end: [.9] } },
    { type: 'chunk', data: bytes.toString('base64') }, { type: 'done' } ].map(event => `data: ${JSON.stringify(event)}\n\n`).join('');
}
test('voice is opt-in, reads only new selected-audience replies, and stops at a privacy boundary', async ({ page }) => {
  const spoken: string[] = [];
  await page.route('https://api.cartesia.ai/tts/sse', async route => {
    spoken.push(route.request().postDataJSON().transcript); await route.fulfill({ contentType: 'text/event-stream', body: speech() });
  });
  await open(page);
  expect(spoken).toEqual([]);
  await page.getByRole('button', { name: 'Enable voice' }).click();
  await expect(page.getByRole('button', { name: 'Voice on' })).toBeVisible();
  expect(spoken).toEqual([]); // Opening voice does not narrate old private history.
  await page.evaluate(() => window.planningFixture.setTables({ my_agent_messages: [{ id: 9n, roomId: 1n, utterance: 'Your budget is noted.', createdAt: 9n }] }));
  await expect.poll(() => spoken).toEqual(['Your budget is noted.']);
  await page.getByRole('tab', { name: 'Agent discussion' }).click();
  await expect(page.getByRole('button', { name: 'Enable voice' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Talk to Pip' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Enable voice' }).click();
  await expect(page.getByRole('button', { name: 'Voice on' })).toBeVisible();
  await page.evaluate(() => window.planningFixture.setTables({ my_agent_messages: [{ id: 10n, roomId: 1n, utterance: 'Private reason must not be spoken here.', createdAt: 10n }], room_messages: [{ id: 12n, roomId: 1n, senderAgentId: 2n, utterance: 'We found a shared option.', createdAt: 12n }] }));
  await expect.poll(() => spoken).toEqual(['Your budget is noted.', 'We found a shared option.']);
  await page.evaluate(() => window.planningFixture.setConnected(false));
  await expect(page.getByRole('button', { name: 'Enable voice' })).toBeDisabled();
});
test('streaming microphone turns populate real message actions, never approve, and release capture', async ({ page }) => {
  let ws: any; let chunks = 0;
  await page.routeWebSocket('wss://api.cartesia.ai/stt/turns/websocket?**', socket => { ws = socket; socket.onMessage(() => chunks++); });
  await open(page);
  await page.evaluate(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    (window as any).voiceStreams = [];
    navigator.mediaDevices.getUserMedia = async options => { const stream = await original(options); (window as any).voiceStreams.push(stream); return stream; };
  });
  await page.getByRole('button', { name: 'Talk to Pip' }).click();
  await expect(page.getByText('Listening · pause when you finish a thought')).toBeVisible();
  await expect.poll(() => chunks).toBeGreaterThan(0);
  ws.send(JSON.stringify({ type: 'turn.start', request_id: 'one-connection' }));
  ws.send(JSON.stringify({ type: 'turn.update', transcript: 'My budget is' }));
  await expect(page.locator('.voice-transcript')).toHaveText('My budget is');
  ws.send(JSON.stringify({ type: 'turn.end', transcript: 'My budget is 500 dollars.' }));
  await expect.poll(() => page.evaluate(() => window.planningFixture.calls.map((c: any) => c.method))).toEqual(['submitPrivateMessage']);
  ws.send(JSON.stringify({ type: 'turn.start', request_id: 'one-connection' }));
  ws.send(JSON.stringify({ type: 'turn.end', transcript: 'confirm' }));
  await expect.poll(() => page.evaluate(() => window.planningFixture.calls.map((c: any) => c.method))).toEqual(['submitPrivateMessage', 'submitPrivateMessage']);
  await page.getByRole('tab', { name: 'Agent discussion' }).click();
  expect(await page.evaluate(() => (window as any).voiceStreams.every((s: MediaStream) => s.getTracks().every(t => t.readyState === 'ended')))).toBe(true);
});
