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
export async function admitTestBrowser(page: Page) {
  const { token } = createTestInvitation();
  await page.goto(`/invite#invite=${token}`);
  await expect(page.getByRole('button', { name: 'Accept invitation' })).toBeEnabled();
  await page.getByRole('button', { name: 'Accept invitation' }).click();
  await expect(page.getByRole('heading', { name: 'You’re in good company.' })).toBeVisible();
}
