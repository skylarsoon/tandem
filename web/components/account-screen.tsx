'use client';
import { useEffect, useState, useSyncExternalStore, type FormEvent } from 'react';
import { connect } from '../lib/client';
import { openTravellerApp } from '../lib/account-app';
import styles from './invitation-gate.module.css';
type Client = ReturnType<typeof connect>;
type Account = { email: string; mustChangePassword: boolean; admin?: boolean };
async function api(action: string, body: object) {
  const response = await fetch(`/api/account/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), cache: 'no-store' });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Please try again.');
  return data;
}
function message(error: unknown) {
  const text = error instanceof Error ? error.message : 'Please try again.';
  if (text.includes('INVITATION_INVALID')) return 'This invitation is invalid, expired, used, or revoked. Ask the team for a new link.';
  if (text.includes('EMAIL_IN_USE')) return 'An account already uses this email. Sign in instead.';
  if (text.includes('INVALID_HANDOFF')) return 'Registration timed out. Please try again.';
  return text;
}
export function AccountScreen({ invitation = false }: { invitation?: boolean }) {
  const [client, setClient] = useState<Client>(), [error, setError] = useState('');
  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_STDB_URL, db = process.env.NEXT_PUBLIC_STDB_DB;
    if (!url || !db) { setError('Tandem is not configured yet. Please try again later.'); return; }
    const c = connect(url, db); setClient(c); return () => c.close();
  }, []);
  return <main><a className="wordmark" href="/">tandem ↗</a>{error ? <p role="alert">{error}</p> : client ? <AccountForm client={client} invitation={invitation} /> : <p role="status">Connecting securely…</p>}</main>;
}
function AccountForm({ client, invitation }: { client: Client; invitation: boolean }) {
  const state = useSyncExternalStore(client.session.subscribe, client.session.getSnapshot, client.session.getSnapshot);
  const [account, setAccount] = useState<Account | null>(null), [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<'login' | 'register'>(invitation ? 'register' : 'login');
  const [email, setEmail] = useState(''), [password, setPassword] = useState(''), [confirmation, setConfirmation] = useState('');
  const [currentPassword, setCurrentPassword] = useState(''), [token, setToken] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState(''), [changing, setChanging] = useState(false);
  const legacyAccess = state.status === 'ready' && state.tables.my_app_access?.[0]?.allowed === true;
  useEffect(() => {
    const invitation = new URLSearchParams(window.location.hash.slice(1)).get('invite');
    if (invitation) { setToken(invitation); setMode('register'); window.history.replaceState(null, '', window.location.pathname); }
    if (new URLSearchParams(window.location.search).get('register') === '1') setMode('register');
    let active = true;
    void (async () => {
      try {
        const response = await fetch('/api/account/status', { cache: 'no-store' });
        if (response.ok) { if (active) setAccount(await response.json()); }
        else if (response.status === 401) {
          const admin = await fetch('/api/admin/status', { cache: 'no-store' });
          if (admin.ok && active) setAccount({ ...await admin.json(), admin: true });
        } else if (active) setError((await response.json()).error || 'Account service unavailable.');
      } catch { if (active) setError('Could not check your session. Please refresh.'); }
      finally { if (active) setLoading(false); }
    })();
    return () => { active = false; };
  }, []);
  async function perform(fn: () => Promise<void>) {
    if (busy) return; setBusy(true); setError(''); setNotice('');
    try { await fn(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault(); void perform(async () => {
      if (mode === 'register') {
        if (password !== confirmation) throw new Error('The passwords don’t match.');
        const result = await api('register', { email, password, invitation: token.trim() });
        try { await client.connection.reducers.completeTravellerRegistration({ token: result.token }); }
        catch (e) {
          // A reducer response can be lost after commit. The protected cookie
          // is usable only when registration actually succeeded.
          const status = await fetch('/api/account/status', { cache: 'no-store' });
          if (!status.ok) throw e;
        }
        setPassword(''); setConfirmation(''); window.location.assign('/profile');
      } else {
        const result = await api('login', { email, password }); setPassword(''); setAccount(result);
        if (!result.mustChangePassword) await openTravellerApp();
      }
    });
  }
  const reset = account && (account.mustChangePassword || changing);
  return <section className={styles.gate} aria-label="Your Tandem account">
    <div className={styles.icon} aria-hidden="true">✳</div><p className={styles.eyebrow}>A LITTLE COMMON GROUND / YOUR ACCOUNT</p>
    {loading ? <p role="status">Checking your session…</p> : account?.admin ? <>
      <h2>Your account,<br />everywhere.</h2><p>You’re signed in as <strong>{account.email}</strong>.</p><p>Use your admin workspace to open your trips, change your password, or manage invitations.</p><a href="/admin">Open admin workspace →</a>
    </> : account ? <>
      <h2>{reset ? 'Make it yours.' : 'Your account,\neverywhere.'}</h2><p><strong>{account.email}</strong></p>
      {reset ? <form onSubmit={e => { e.preventDefault(); void perform(async () => {
        if (password !== confirmation) throw new Error('The passwords don’t match.');
        await api('password', { currentPassword, password });
        setPassword(''); setConfirmation(''); setCurrentPassword(''); setChanging(false); setAccount({ ...account, mustChangePassword: false });
        setNotice('Password updated. Your other sessions have been signed out.');
      }); }}>
        {!account.mustChangePassword && <label>Current password<input required type="password" autoComplete="current-password" maxLength={128} value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} /></label>}
        <label>New password<input required type="password" autoComplete="new-password" minLength={14} maxLength={128} value={password} onChange={e => setPassword(e.target.value)} /></label>
        <label>Confirm new password<input required type="password" autoComplete="new-password" minLength={14} maxLength={128} value={confirmation} onChange={e => setConfirmation(e.target.value)} /></label>
        <small>Use a unique passphrase of 14–128 characters.</small><button disabled={busy}>Save password →</button>
      </form> : <><p>Your profile, companion, and trips stay with your account on every device.</p>
        <div className={styles.actions}><button disabled={busy} onClick={() => void perform(() => openTravellerApp())}>Open my trips →</button>
        <button disabled={busy} onClick={() => void perform(() => openTravellerApp('/profile'))}>Your profile</button>
        <button disabled={busy} onClick={() => { setChanging(true); setPassword(''); setConfirmation(''); }}>Change password</button></div></>}
      <button className={styles.secondary} disabled={busy} onClick={() => void perform(async () => { await api('logout', {}); setAccount(null); setPassword(''); setConfirmation(''); setCurrentPassword(''); setChanging(false); setMode('login'); })}>Sign out</button>
    </> : <>
      <h2>{mode === 'register' ? 'Good company.\nYour own account.' : 'Welcome back.\nPick up your plans.'}</h2>
      <p>{mode === 'register' ? 'Set up your login once. Your companion, profile, and trips will be waiting on every device.' : 'Sign in to continue with your companion and the people you’re travelling with.'}</p>
      <form onSubmit={submit}>
        <label>Email<input required type="email" autoComplete="username" maxLength={254} value={email} onChange={e => setEmail(e.target.value)} /></label>
        <label>Password<input required type="password" autoComplete={mode === 'register' ? 'new-password' : 'current-password'} minLength={mode === 'register' ? 14 : 1} maxLength={128} value={password} onChange={e => setPassword(e.target.value)} /></label>
        {mode === 'register' && <>
          <label>Confirm password<input required type="password" autoComplete="new-password" minLength={14} maxLength={128} value={confirmation} onChange={e => setConfirmation(e.target.value)} /></label>
          {/^[A-Za-z0-9_-]{43}$/.test(token.trim()) ? <p className={styles.invitation}>✦ Your invitation is ready. It will be accepted when you create your account.</p> : legacyAccess ? <p className={styles.invitation}>Your existing profile and trips in this browser will be kept.</p> : <label>Invitation code<input required type="password" autoComplete="off" maxLength={43} value={token} onChange={e => setToken(e.target.value)} placeholder="Open your invitation link or paste its code" /></label>}
          <small>Use a unique passphrase of 14–128 characters.</small>
        </>}
        <button disabled={busy || (mode === 'register' && (state.status !== 'ready' || (!legacyAccess && !/^[A-Za-z0-9_-]{43}$/.test(token.trim()))))}>{busy ? 'One moment…' : mode === 'register' ? 'Create account →' : 'Sign in →'}</button>
      </form>
      <button className={styles.secondary} disabled={busy} onClick={() => { setMode(mode === 'register' ? 'login' : 'register'); setPassword(''); setConfirmation(''); setError(''); }}>{mode === 'register' ? 'Already have an account? Sign in' : 'Have an invitation? Create an account'}</button>
      <p className={styles.fine}>Already used an invitation in this browser? Create an account here to keep your existing profile and trips. If you forget your password, contact the project owner for a reset.</p>
      <a href="/admin">Admin sign-in →</a>
    </>}
    {error && <p className={styles.error} role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>;
}
