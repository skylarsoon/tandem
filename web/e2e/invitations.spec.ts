import { test, expect } from '@playwright/test';
import { createTestInvitation, invitationAdmin } from './invitation-admin';
test.skip(!process.env.TANDEM_STDB_URL?.startsWith('http://127.0.0.1:'), 'Requires an isolated local invitation-enabled database');
test('one invitation admits one browser; revocation updates the open profile and voice access', async ({ browser, page }) => {
  await page.goto('/app');
  await expect(page.getByRole('heading', { name: /Good company.*By invitation/ })).toBeVisible();
  const { token, hash } = createTestInvitation();
  await page.goto(`/invite#invite=${token}`);
  await expect(page.getByRole('button', { name: 'Accept invitation' })).toBeEnabled();
  expect(page.url()).not.toContain('#');
  await expect(page.getByLabel('Your invitation code')).toHaveAttribute('type', 'password');
  await page.getByRole('button', { name: 'Accept invitation' }).click();
  await expect(page.getByRole('heading', { name: 'You’re in good company.' })).toBeVisible();
  await page.goto('/profile');
  await expect(page.getByRole('heading', { name: /Good company.*By invitation/ })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Let your agent get to know you.' })).toBeVisible();
  const second = await browser.newContext({ viewport: { width: 390, height: 844 } });
  try {
    const other = await second.newPage();
    await other.goto(new URL(`/invite#invite=${token}`, page.url()).href);
    await expect(other.getByRole('button', { name: 'Accept invitation' })).toBeEnabled();
    await other.getByRole('button', { name: 'Accept invitation' }).click();
    await expect(other.locator('p[role=alert]')).toContainText('could not be accepted');
    expect(await other.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    invitationAdmin('revoke_app_invitation', [hash]);
    await expect(page.getByRole('heading', { name: /Good company.*By invitation/ })).toBeVisible();
    const denied = await page.evaluate(async () => {
      const key = Object.keys(localStorage).find(k => k.startsWith('tandem:participant:'))!;
      return (await fetch('/api/voice/session', { method: 'POST', headers: { Authorization: `Bearer ${localStorage.getItem(key)}`, 'Content-Type': 'application/json' }, body: '{}' })).status;
    });
    expect(denied).toBe(403);
    await page.goto('/');
    await expect(page.locator('video track')).toHaveAttribute('src', '/media/tandem-demo-narration.vtt');
  } finally { await second.close(); }
});
