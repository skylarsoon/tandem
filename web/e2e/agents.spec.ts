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
      await route.fulfill({ body: readFileSync(file), contentType: file.endsWith('.webp') ? 'image/webp' : file.endsWith('.png') ? 'image/png' : 'image/jpeg' });
    } else await route.fulfill({ contentType: 'text/html', body: `<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}</style></head><body><div id="root"></div><script>${bundle.replaceAll('</script', '<\\/script')}</script></body></html>` });
  });
  await page.goto('http://companions.test/');
  await expect(page.getByRole('heading', { name: 'Hey Skylar, where are we going?' })).toBeVisible();
  await expect(page.locator('.agent-avatar--hero .pip-companion')).toBeVisible();
}

test('character selection follows the owner into the room and private messages', async ({ page }, info) => {
  await open(page);
  await page.screenshot({ path: info.outputPath('companions-home.png'), fullPage: true });
  await page.getByText('Make this agent yours').click();
  await page.getByRole('button', { name: 'Choose Mint' }).click();
  await expect(page.getByRole('button', { name: 'Choose Mint' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.agent-avatar--hero')).toHaveClass(/agent-avatar--mint/);
  await page.getByRole('button', { name: 'Enter trip' }).click();
  await expect(page.locator('.is-your-agent .agent-avatar')).toHaveClass(/agent-avatar--mint/);
  await expect(page.locator('.private-item .agent-avatar')).toHaveClass(/agent-avatar--mint/);
  await expect(page.locator('.shared-item .agent-avatar')).toHaveClass(/agent-avatar--peach/);
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
  expect(await page.locator('.agent-avatar--hero').evaluate(el => el.getAnimations({ subtree: true }).length)).toBe(0);
  await page.getByRole('button', { name: 'Enter trip' }).click();
  await page.evaluate(() => window.agentsFixture.setTables({ room_agent_activity: [{ agentId: 12n, roomId: 1n, activity: 'WRITING' }] }));
  await expect(page.getByText('Writing', { exact: true })).toBeVisible();
  await page.evaluate(() => window.agentsFixture.setConnected(false));
  await expect(page.getByText('Writing', { exact: true })).toHaveCount(0);
  await expect(page.locator('.room-companions img')).toHaveCount(0);
  await page.evaluate(() => window.agentsFixture.setConnected(true));
  await expect(page.getByText('Writing', { exact: true })).toBeVisible();
  await page.evaluate(() => window.agentsFixture.setTables({ my_agent_appearance: [{ variant: 'peach' }] }));
  await page.locator('.is-your-agent [data-pip-base]').dispatchEvent('error');
  await expect(page.locator('.is-your-agent .agent-avatar-fallback')).toBeVisible();
  await expect(page.locator('.is-your-agent')).toContainText('Your agent');
});


test('companion setup persists selection before entering the room and allows retry', async ({ page }) => {
  await open(page);
  await page.evaluate(() => window.agentsFixture.setSetup(true));
  await expect(page.getByRole('heading', { name: 'Meet your travel companion.' })).toBeVisible();
  await page.getByRole('button', { name: 'Choose Mint' }).click();
  await page.evaluate(() => { window.agentsFixture.failNext = true; });
  await page.getByRole('button', { name: 'Enter the group room' }).click();
  await expect(page.getByRole('alert')).toContainText('could not be saved');
  await expect(page.getByRole('button', { name: 'Choose Mint' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Enter the group room' }).click();
  await expect(page.locator('.is-your-agent .agent-avatar')).toHaveClass(/agent-avatar--mint/);
});

test('group discussion shows shared utterances without private messages', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: 'Enter trip' }).click();
  await expect(page.locator('.private-item')).toBeVisible();
  await page.getByRole('tab', { name: 'Agent discussion' }).click();
  await expect(page.locator('.private-item')).toHaveCount(0);
  await expect(page.getByText('A quiet weekend by the coast sounds lovely.')).toBeVisible();
  await expect(page.getByLabel('Message your agent')).toHaveCount(0);
  for (const viewport of [{ width: 1280, height: 720 }, { width: 1024, height: 600 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const bounds = await page.locator('.conversation-panel--discussion').boundingBox();
    expect(bounds!.height).toBeLessThanOrEqual(viewport.height + 1);
    const footer = await page.getByRole('button', { name: 'Back to your agent' }).boundingBox();
    expect(footer!.y + footer!.height).toBeLessThanOrEqual(viewport.height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.getByRole('button', { name: 'Back to your agent' }).click();
  await expect(page.locator('.private-item')).toBeVisible();
});
