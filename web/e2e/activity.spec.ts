import { test, expect } from '@playwright/test';
import { admitTestBrowser, invitationAdmin } from './invitation-admin';
test.skip(!process.env.TANDEM_STDB_URL?.startsWith('http://127.0.0.1:'), 'Disposable local database required');
test('provider events update both views and fit mobile', async ({ page, context }) => {
  const url = process.env.TANDEM_STDB_URL!, db = process.env.TANDEM_STDB_DB!;
  await page.setViewportSize({ width: 1440, height: 1000 }); await admitTestBrowser(page); await page.goto('/app');
  await page.getByText('Create or join a trip', { exact: true }).click();
  await page.getByLabel('Your display name', { exact: true }).fill('Activity tester');
  await page.getByLabel('Trip name', { exact: true }).fill('A weekend together');
  await page.getByRole('button', { name: 'Create trip', exact: true }).click();
  await page.getByRole('button', { name: 'Enter the group room →' }).click();
  const roomId = Number(await page.locator('#trip-select').inputValue());
  const participant = await page.evaluate(() => localStorage.getItem(Object.keys(localStorage).find(k => k.startsWith('tandem:participant:'))!)!);
  async function call(token: string, name: string, args: object) {
    const r = await fetch(`${url}/v1/database/${db}/call/${name}`, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(args) });
    if (!r.ok) throw Error(`${name}: HTTP ${r.status}`);
    const body = await r.text(); return body ? JSON.parse(JSON.parse(body)) : undefined;
  }
  await call(participant, 'confirm_participation_preferences', { speakUpForMe: true, askMeFirst: true, giveMeTime: true, remindersOptedIn: false });
  await call(participant, 'set_constraint', { roomId, key: 'flight_budget_total', valueJson: '50000', unit: 'USD_CENTS', hardness: 'HARD', authorizedRangeJson: null, privateRationale: null });
  const worker = await (await fetch(`${url}/v1/identity`, { method: 'POST' })).json();
  invitationAdmin('add_worker', [{ __identity__: `0x${worker.identity}` }, 'activity-test']);
  const r = await fetch(`${url}/v1/database/${db}/sql`, { method: 'POST', headers: { Authorization: `Bearer ${worker.token}`, 'Content-Type': 'text/plain' }, body: 'SELECT * FROM worker_pending_jobs' });
  const [result] = await r.json();
  const jobs = result.rows.map((row: unknown[]) => Object.fromEntries(result.schema.elements.map((e: any, i: number) => [e.name.some, row[i]])));
  const job = jobs.filter((j: any) => j.room_id === roomId && j.kind === 'PLAN').at(-1);
  const claim = await call(worker.token, 'claim_job', { jobId: { some: job.id }, leaseSeconds: 120, requestKey: `claim-${Date.now()}` });
  let n = 0;
  async function emit(phase: string, search: string, category = 'FLIGHT', extra = {}) {
    await call(worker.token, 'emit_event', { jobId: job.id, attempt: claim.attempt, requestKey: `activity-${Date.now()}-${++n}`, type: 'RESEARCH_ACTIVITY', memberId: null, payloadJson: JSON.stringify({ schema_version: 1, job_ref: String(job.id), generation: String(claim.generation), attempt: String(claim.attempt), input_revision: claim.input_revision, membership_revision: claim.membership_revision, permission_revision: claim.permission_revision, provider: 'APIFY', phase, search_ref: search, category, actor_id: 'test-provider-actor', run_ref: `run-${search}`, round: 0, polls: n, ...extra }) });
  }
  await emit('RUNNING', 'flights'); await emit('QUEUED', 'stay', 'STAY');
  const panel = page.getByRole('region', { name: 'Live agent activity' });
  await expect(panel).toContainText('search in progress'); await panel.getByRole('button').click();
  await expect(panel).toContainText('Waiting for a search slot');
  await page.screenshot({ path: '/private/tmp/tandem-live-activity-desktop.png', fullPage: true });
  await page.getByRole('tab', { name: /Agent discussion/ }).click(); await expect(panel).toBeVisible();
  await emit('COMPLETED', 'flights', 'FLIGHT', { result_count: 12 }); await emit('FAILED', 'stay', 'STAY', { error_code: 'APIFY_POLL_DEADLINE_EXCEEDED' });
  await expect(panel).toContainText('12 usable options returned'); await expect(panel).toContainText('provider may still be running');
  await page.setViewportSize({ width: 390, height: 844 }); await expect(panel).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '/private/tmp/tandem-live-activity-mobile.png', fullPage: true });
  await context.setOffline(true); await expect(panel).toContainText('Connection lost', { timeout: 15000 });
});
