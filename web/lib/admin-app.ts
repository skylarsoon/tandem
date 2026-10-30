import { connect } from './client';
import type { ClientSnapshot } from './state';
type Client = ReturnType<typeof connect>;
const attempts = new WeakMap<Client, Promise<boolean>>();
function waitFor(client: Client, predicate: (state: ClientSnapshot) => boolean) {
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => { unsubscribe(); reject(new Error('The app took too long to connect. Please try again.')); }, 15000);
    let unsubscribe = () => {};
    const check = () => {
      const state = client.session.getSnapshot();
      if (predicate(state)) { clearTimeout(timer); unsubscribe(); resolve(); }
      else if (state.status === 'error') { clearTimeout(timer); unsubscribe(); reject(new Error('Could not connect to the app. Please try again.')); }
    };
    unsubscribe = client.session.subscribe(check); check();
  });
}
export function grantAdminAppAccess(client: Client): Promise<boolean> {
  const existing = attempts.get(client);
  if (existing) return existing;
  const pending = (async () => {
    const response = await fetch('/api/admin/open-app', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}', cache: 'no-store' });
    if (response.status === 401) return false; // Ordinary guests still use invitations.
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not open the app.');
    await client.connection.reducers.redeemAdminAppAccess({ token: data.token });
    await waitFor(client, state => state.status === 'ready' && state.tables.my_app_access?.[0]?.allowed === true);
    return true;
  })();
  attempts.set(client, pending);
  void pending.finally(() => attempts.delete(client)).catch(() => {});
  return pending;
}
export async function openAdminApp() {
  const url = process.env.NEXT_PUBLIC_STDB_URL, database = process.env.NEXT_PUBLIC_STDB_DB;
  if (!url || !database) throw new Error('The app connection is not configured.');
  const client = connect(url, database); // The authenticated handoff binds this device to the account's traveller.
  try {
    await waitFor(client, state => state.status === 'ready');
    if (!await grantAdminAppAccess(client)) throw new Error('Please sign in to admin again.');
  } finally { client.close(); }
}
