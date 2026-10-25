import { test, expect, type Page } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const bundle = buildSync({ entryPoints: [path.resolve('e2e/fixtures/agents.tsx')], bundle: true, write: false, format: 'iife', platform: 'browser', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"development"' } }).outputFiles[0].text;
const css = readFileSync(path.resolve('app/globals.css'), 'utf8');
async function open(page: Page) {
  await page.route('http://companions.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/agents/') || url.pathname.startsWith('/destinations/')) {
      const file = path.resolve('public', `.${url.pathname}`);
      await route.fulfill({ body: readFileSync(file), contentType: file.endsWith('.webp') ? 'image/webp' : 'image/jpeg' });
    } else await route.fulfill({ contentType: 'text/html', body: `<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}</style></head><body><div id="root"></div><script>${bundle.replaceAll('</script', '<\\/script')}</script></body></html>` });
  });
  await page.goto('http://companions.test/');
  await expect(page.getByRole('heading', { name: 'Hey Skylar, where are we going?' })).toBeVisible();
  await expect(page.locator('.agent-avatar--hero img')).toBeVisible();
}

test('character selection follows the owner into the room and private messages', async ({ page }, info) => {
  await open(page);
  await page.screenshot({ path: info.outputPath('companions-home.png'), fullPage: true });
  await page.getByText('Make this agent yours').click();
  await page.getByRole('button', { name: 'Choose Mint' }).click();
  await expect(page.getByRole('button', { name: 'Choose Mint' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.agent-avatar--hero img')).toHaveAttribute('src', '/agents/mint.webp');
  await page.getByRole('button', { name: 'Enter trip' }).click();
  await expect(page.locator('.is-your-agent img')).toHaveAttribute('src', '/agents/mint.webp');
  await expect(page.locator('.private-item img')).toHaveAttribute('src', '/agents/mint.webp');
  await expect(page.locator('.shared-item img')).toHaveAttribute('src', '/agents/peach.webp');
  await expect(page.getByText('Exploring options', { exact: true })).toBeVisible();
  await expect(page.getByText('Sam · away', { exact: true })).toBeVisible();
  await expect(page.locator('.mediator-companion')).toContainText('Mediator');
  await page.screenshot({ path: info.outputPath('companions-room.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.getByLabel('Message your agent')).toBeVisible();
  await page.screenshot({ path: info.outputPath('companions-mobile.png'), fullPage: true });
});

test('failed saves preserve appearance and permit retry', async ({ page }) => {
  await open(page);
  await page.getByText('Make this agent yours').click();
  await page.evaluate(() => { window.agentsFixture.failNext = true; });
  await page.getByRole('button', { name: 'Choose Peach' }).click();
  await expect(page.getByRole('alert')).toContainText('could not be saved');
  await expect(page.getByRole('button', { name: 'Choose Lavender' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Choose Peach' }).click();
  await expect(page.getByRole('button', { name: 'Choose Peach' })).toHaveAttribute('aria-pressed', 'true');
});

test('reduced motion, asset failure, activity updates and reconnects', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await open(page);
  expect(await page.locator('.agent-avatar--hero img').evaluate(el => getComputedStyle(el).animationName)).toBe('none');
  await page.getByRole('button', { name: 'Enter trip' }).click();
  await page.evaluate(() => window.agentsFixture.setTables({ room_agent_activity: [{ agentId: 12n, roomId: 1n, activity: 'WRITING' }] }));
  await expect(page.getByText('Writing', { exact: true })).toBeVisible();
  await page.evaluate(() => window.agentsFixture.setConnected(false));
  await expect(page.getByText('Writing', { exact: true })).toHaveCount(0);
  await expect(page.locator('.room-companions img')).toHaveCount(0);
  await page.evaluate(() => window.agentsFixture.setConnected(true));
  await expect(page.getByText('Writing', { exact: true })).toBeVisible();
  await page.locator('.is-your-agent img').dispatchEvent('error');
  await expect(page.locator('.is-your-agent .agent-avatar-fallback')).toBeVisible();
  await expect(page.locator('.is-your-agent')).toContainText('Your agent');
});
