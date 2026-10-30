import { execFileSync } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import { expect, type Page } from '@playwright/test';
export function invitationAdmin(reducer: string, args: unknown[]) {
  const url = process.env.TANDEM_STDB_URL;
  if (!url || new URL(url).hostname !== '127.0.0.1' || !process.env.TANDEM_STDB_DB)
    throw new Error('Invitation test administration is limited to a disposable local database');
  execFileSync(process.env.SPACETIME_BIN || 'spacetime', [
    ...(process.env.TANDEM_STDB_CLI_ROOT ? [`--root-dir=${process.env.TANDEM_STDB_CLI_ROOT}`] : []),
    'call', '--server', url, '--no-config', '-y', process.env.TANDEM_STDB_DB,
    reducer, ...args.map(a => JSON.stringify(a)),
  ], { stdio: 'pipe' });
}
export function createTestInvitation() {
  const token = randomBytes(32).toString('base64url');
  const hash = createHash('sha256').update(token).digest('hex');
  invitationAdmin('create_app_invitation', [hash, 'browser-contract-test', 600]);
  return { token, hash };
}
export const testPassword = 'A test-only passphrase 42!';
export async function registerTestBrowser(page: Page, token: string, email: string) {
  await page.goto(`/invite#invite=${token}`);
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Password', { exact: true }).fill(testPassword);
  await page.getByLabel('Confirm password', { exact: true }).fill(testPassword);
  await page.getByRole('button', { name: 'Create account →' }).click();
}
export async function admitTestBrowser(page: Page) {
  const { token } = createTestInvitation();
  const email = `browser-${randomBytes(8).toString('hex')}@example.test`;
  await registerTestBrowser(page, token, email);
  await expect(page.getByRole('heading', { name: 'Let your agent get to know you.' })).toBeVisible();
  return { email, password: testPassword };
}
