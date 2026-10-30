import { test, expect, type Page } from '@playwright/test';

// Run only against a disposable local database with a password-initialized admin.
// TANDEM_TEST_ADMIN_EMAIL / TANDEM_TEST_ADMIN_PASSWORD are test credentials only.
test('account restores trips and live profile edits across independent devices', async ({ browser, baseURL }) => {
  test.skip(!process.env.TANDEM_TEST_ADMIN_EMAIL || !process.env.TANDEM_TEST_ADMIN_PASSWORD, 'Requires an isolated test admin');
  if (!baseURL || !['127.0.0.1', 'localhost'].includes(new URL(baseURL).hostname)) throw new Error('This test must use a disposable local app');
  const desktop = await browser.newContext({ baseURL, viewport: { width: 1440, height: 1000 } });
  const mobile = await browser.newContext({ baseURL, viewport: { width: 390, height: 844 } });
  try {
    const a = await desktop.newPage(), b = await mobile.newPage();
    async function login(page: Page) {
      await page.goto('/admin');
      await page.getByLabel('Email', { exact: true }).fill(process.env.TANDEM_TEST_ADMIN_EMAIL!);
      await page.getByLabel('Password', { exact: true }).fill(process.env.TANDEM_TEST_ADMIN_PASSWORD!);
      await page.getByRole('button', { name: 'Sign in →' }).click();
      await page.getByRole('button', { name: 'Open app ↗' }).click();
      await page.waitForURL('**/app');
    }
    await login(a);
    const trip = `Cross-device trip ${Date.now()}`;
    await a.getByText('Create or join a trip', { exact: true }).click();
    await a.getByLabel('Your display name', { exact: true }).fill('Device traveller');
    await a.getByLabel('Trip name', { exact: true }).fill(trip);
    await a.getByRole('button', { name: 'Create trip', exact: true }).click();
    // Regression: invitation gate must not discard setup on resubscription.
    await a.getByRole('button', { name: 'Enter the group room →' }).click();
    await a.getByRole('link', { name: 'Your agent profile ↗' }).click();
    await expect(a.locator('.profile form')).toBeVisible();
    const back = a.getByRole('button', { name: 'Back', exact: false });
    if (await back.isVisible()) await back.click();
    await a.locator('.profile form input').first().fill('Shared traveller');
    await a.getByRole('button', { name: 'Save and continue', exact: true }).click();
    await login(b);
    await expect(b.locator('#trip-select option').filter({ hasText: trip })).toHaveCount(1);
    await b.locator('#trip-select').selectOption({ label: trip });
    await expect(b.getByRole('heading', { name: 'Your trip, in conversation' })).toBeVisible();
    await b.getByRole('link', { name: 'Your agent profile ↗' }).click();
    await b.getByRole('button', { name: 'Back', exact: false }).click();
    await expect(b.locator('.profile form input').first()).toHaveValue('Shared traveller');
    await b.locator('.profile form input').first().fill('Updated on mobile');
    await b.getByRole('button', { name: 'Save and continue', exact: true }).click();
    await a.getByRole('button', { name: 'Back', exact: false }).click();
    await expect(a.locator('.profile form input').first()).toHaveValue('Updated on mobile');
    const admin = await mobile.newPage();
    await admin.goto('/admin');
    await admin.getByRole('button', { name: 'Sign out', exact: true }).click();
    await expect(b.getByRole('link', { name: 'Sign in to restore your profile and trips →' })).toBeVisible();
    await expect(a.locator('.profile form input').first()).toHaveValue('Updated on mobile');
  } finally {
    await desktop.close(); await mobile.close();
  }
});
