"use client";
import { useEffect, useState, type FormEvent } from 'react';
import styles from './page.module.css';
type Overview = {
  email: string; mustChangePassword: boolean; asOf?: string;
  invitations?: { hash: string; label: string; expiresAt: string; status: string }[];
  users?: { identity: string; name: string; revoked: boolean; grantedAt: string }[];
  counts?: Record<string, number>;
  recentJobs?: { id: string; kind: string; status: string; attempt: number; updatedAt: string }[];
  events?: { id: string; email: string; action: string; at: string }[];
};
const date = (micros?: string) => micros ? new Date(Number(micros) / 1000).toLocaleString() : '—';
const words = (value: string) => value.toLowerCase().replaceAll('_', ' ');
export default function AdminPage() {
  const [data, setData] = useState<Overview | null>(null), [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [tab, setTab] = useState('Invitations'), [passwordView, setPasswordView] = useState(false);
  const [email, setEmail] = useState(''), [password, setPassword] = useState('');
  const [currentPassword, setCurrentPassword] = useState(''), [confirmation, setConfirmation] = useState('');
  const [label, setLabel] = useState(''), [hours, setHours] = useState(168), [invitation, setInvitation] = useState('');
  async function api(action: string, body?: object) {
    const response = await fetch(`/api/admin/${action}`, { method: body ? 'POST' : 'GET', cache: 'no-store', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    const result = await response.json();
    if (!response.ok) {
      if (response.status === 401 && action !== 'login' && action !== 'password') { setData(null); setInvitation(''); }
      throw new Error(result.error || 'The request could not be completed.');
    }
    return result;
  }
  async function refresh() { setData(await api('status')); }
  useEffect(() => {
    let active = true;
    fetch('/api/admin/status', { cache: 'no-store' }).then(async response => {
      if (response.status === 401) return null;
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not connect to administration.');
      return result;
    }).then(result => { if (active) setData(result); }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  async function perform(fn: () => Promise<void>) {
    setError(''); setNotice(''); setBusy(true);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : 'Please try again.'); }
    finally { setBusy(false); }
  }
  function submitLogin(event: FormEvent) {
    event.preventDefault(); void perform(async () => { await api('login', { email, password }); setPassword(''); await refresh(); });
  }
  function submitPassword(event: FormEvent) {
    event.preventDefault(); void perform(async () => {
      if (password !== confirmation) throw new Error('The new passwords don’t match.');
      await api('password', { password, currentPassword });
      setPassword(''); setConfirmation(''); setCurrentPassword(''); setPasswordView(false);
      await refresh(); setNotice('Password updated. Your other admin sessions have been signed out.');
    });
  }
  const reset = !!data && (data.mustChangePassword || passwordView);
  return <main className={styles.page}>
    <header className={styles.header}><a href="/" aria-label="Tandem home"><img src="/brand/tandem.svg" width="170" height="38" alt="tandem" /></a><span className={styles.eyebrow}>ADMIN WORKSPACE</span>
      {data && <div className={styles.account}><span>{data.email}</span>{!data.mustChangePassword && <button disabled={busy} onClick={() => { setPasswordView(!passwordView); setPassword(''); setConfirmation(''); setCurrentPassword(''); }}>Change password</button>}<button disabled={busy} onClick={() => void perform(async () => { await api('logout', {}); setData(null); setInvitation(''); setPassword(''); setCurrentPassword(''); setConfirmation(''); setPasswordView(false); })}>Sign out</button></div>}
    </header>
    {error && <p className={styles.error} role="alert">{error}</p>}{notice && <p className={styles.notice} role="status">{notice}</p>}
    {loading ? <p role="status">Connecting to your workspace…</p> : !data ? <section className={styles.login}>
      <div className={styles.sprout} aria-hidden="true">✳</div><p className={styles.eyebrow}>A LITTLE COMMON GROUND</p><h1>Keep good<br />company.</h1><p>Sign in to manage invitations, user access, and the health of Tandem.</p>
      <form onSubmit={submitLogin}><label>Email<input type="email" autoComplete="username" required value={email} onChange={e => setEmail(e.target.value)} maxLength={254} /></label><label>Password<input type="password" autoComplete="current-password" required maxLength={128} value={password} onChange={e => setPassword(e.target.value)} /></label><button className={styles.primary} disabled={busy}>{busy ? 'Signing in…' : 'Sign in →'}</button></form><p className={styles.fine}>Admin accounts are issued by the team. If you need a password reset, contact the project owner.</p>
    </section> : reset ? <section className={styles.login}>
      <p className={styles.eyebrow}>YOUR ACCOUNT</p><h1>{data.mustChangePassword ? 'Make it yours.' : 'A fresh password.'}</h1><p>{data.mustChangePassword ? 'Before entering the workspace, replace your temporary password with a private passphrase.' : 'Changing your password signs out your other admin sessions.'}</p>
      <form onSubmit={submitPassword}>{!data.mustChangePassword && <label>Current password<input required type="password" autoComplete="current-password" maxLength={128} value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} /></label>}<label>New password<input required type="password" autoComplete="new-password" minLength={14} maxLength={128} value={password} onChange={e => setPassword(e.target.value)} /></label><label>Confirm new password<input required type="password" autoComplete="new-password" minLength={14} maxLength={128} value={confirmation} onChange={e => setConfirmation(e.target.value)} /></label><p className={styles.fine}>Use 14–128 characters. A long, unique passphrase works well.</p><button className={styles.primary} disabled={busy}>{busy ? 'Saving…' : 'Save password →'}</button></form>
    </section> : <>
      <div className={styles.intro}><div><p className={styles.eyebrow}>TANDEM / OPERATIONS</p><h1>A little care.<br />A smoother journey.</h1><p>Manage access and keep an eye on the work happening behind the scenes.</p></div><button disabled={busy} onClick={() => void perform(refresh)}>Refresh ↻</button></div>
      <nav className={styles.tabs} aria-label="Admin sections">{['Invitations', 'People', 'System health'].map(name => <button key={name} aria-current={tab === name ? 'page' : undefined} onClick={() => setTab(name)}>{name}</button>)}</nav>
      {tab === 'Invitations' && <div className={styles.columns}>
        <section className={styles.card}><p className={styles.eyebrow}>OPEN A PLACE</p><h2>Invite someone in.</h2><p>Create a personal, single-use link. Share it with the intended traveller yourself.</p><form onSubmit={e => { e.preventDefault(); void perform(async () => { const result = await api('invitations', { label, hours }); setInvitation(result.invitation); setLabel(''); await refresh(); }); }}><label>Invitation label<input value={label} required maxLength={100} placeholder="Who is this for?" onChange={e => setLabel(e.target.value)} /></label><label>Link expires after<select value={hours} onChange={e => setHours(Number(e.target.value))}><option value={24}>24 hours</option><option value={72}>3 days</option><option value={168}>7 days</option></select></label><button className={styles.primary} disabled={busy}>Create invitation →</button></form>
        {invitation && <div className={styles.share}><strong>Your private invitation</strong><p>Copy this now. The complete link is only shown here.</p><input aria-label="Created invitation link" readOnly value={invitation} onFocus={e => e.target.select()} /><button onClick={() => void perform(async () => { await navigator.clipboard.writeText(invitation); setNotice('Invitation copied.'); })}>Copy link</button></div>}</section>
        <section className={styles.card}><h2>Invitations</h2><p className={styles.fine}>Latest 100. Revoking a used invitation also removes access granted by it.</p><div className={styles.list}>{data.invitations?.length ? data.invitations.map(i => <article className={styles.row} key={i.hash}><div><strong>{i.label || 'Untitled invitation'}</strong><small>Expires {date(i.expiresAt)}</small></div><span className={styles.status}>{i.status}</span><button disabled={busy || i.status === 'revoked'} onClick={() => { if (confirm('Revoke this invitation and any current access granted by it?')) void perform(async () => { await api('revoke-invitation', { hash: i.hash }); await refresh(); }); }}>Revoke</button></article>) : <p>No invitations yet.</p>}</div></section>
      </div>}
      {tab === 'People' && <section className={styles.card}><h2>People with app access</h2><p>Suspension removes access immediately. Restoring access preserves the same browser identity and its trips.</p><p className={styles.fine}>Latest 100 grants. Private conversations and profile answers are not available here.</p><div className={styles.list}>{data.users?.length ? data.users.map(u => <article className={styles.row} key={u.identity}><div><strong>{u.name}</strong><small>{u.identity.slice(0,12)}…{u.identity.slice(-6)} · Granted {date(u.grantedAt)}</small></div><span className={styles.status}>{u.revoked ? 'suspended' : 'active'}</span><button disabled={busy} onClick={() => { if (confirm(`${u.revoked ? 'Restore' : 'Suspend'} app access for ${u.name}?`)) void perform(async () => { await api('access', { identity: u.identity, revoked: !u.revoked }); await refresh(); }); }}>{u.revoked ? 'Restore' : 'Suspend'}</button></article>) : <p>No invitations have been redeemed yet.</p>}</div></section>}
      {tab === 'System health' && <><div className={styles.metrics}>{Object.entries(data.counts || {}).map(([status,count]) => <article className={styles.card} key={status}><p className={styles.eyebrow}>{words(status)}</p><strong>{count}</strong><span>jobs</span></article>)}</div><section className={styles.card}><h2>Recent job activity</h2><p>Read-only database snapshot · Updated {date(data.asOf)}. These records show job state, not a live worker uptime check.</p><div className={styles.tableWrap}><table><thead><tr><th>Job</th><th>Type</th><th>State</th><th>Attempt</th><th>Updated</th></tr></thead><tbody>{data.recentJobs?.map(j => <tr key={j.id}><td>#{j.id}</td><td>{words(j.kind)}</td><td>{words(j.status)}</td><td>{j.attempt}</td><td>{date(j.updatedAt)}</td></tr>)}</tbody></table>{!data.recentJobs?.length && <p>No jobs have been recorded.</p>}</div></section><section className={styles.card}><h2>Admin activity</h2><div className={styles.list}>{data.events?.map(e => <article className={styles.row} key={e.id}><div><strong>{words(e.action)}</strong><small>{e.email} · {date(e.at)}</small></div></article>)}</div></section></>}
    </>}
    <footer className={styles.footer}><a href="/">← Public homepage</a><span>Thoughtful access. Private conversations.</span></footer>
  </main>;
}
