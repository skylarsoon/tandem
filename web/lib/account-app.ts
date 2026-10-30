import { connect } from './client';
import { grantAdminAppAccess, waitFor } from './admin-app';
type Client = ReturnType<typeof connect>;
const attempts = new WeakMap<Client, Promise<boolean>>();
export function grantTravellerAppAccess(client: Client): Promise<boolean> {
  const current = attempts.get(client);
  if (current) return current;
  const pending = (async () => {
    const response = await fetch('/api/account/open-app', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}', cache: 'no-store' });
    if (response.status === 401) return false;
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not restore your account.');
    await client.connection.reducers.redeemTravellerAccess({ token: data.token });
    await waitFor(client, state => state.status === 'ready' && state.tables.my_app_access?.[0]?.allowed === true);
    return true;
  })();
  attempts.set(client, pending);
  void pending.finally(() => attempts.delete(client)).catch(() => {});
  return pending;
}
export async function restoreSignedInAccount(client: Client) {
  return await grantTravellerAppAccess(client) || await grantAdminAppAccess(client);
}
export async function openTravellerApp(destination: '/app' | '/profile' = '/app') {
  const url = process.env.NEXT_PUBLIC_STDB_URL, database = process.env.NEXT_PUBLIC_STDB_DB;
  if (!url || !database) throw new Error('The app connection is not configured.');
  const client = connect(url, database);
  try {
    await waitFor(client, state => state.status === 'ready');
    if (!await grantTravellerAppAccess(client)) throw new Error('Please sign in again.');
    window.location.assign(destination);
  } finally { client.close(); }
}
