"use client";
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import type { connect } from "../lib/client";
import styles from "./invitation-gate.module.css";

export function InvitationGate({ client, children }: { client: ReturnType<typeof connect>; children: ReactNode }) {
  const state = useSyncExternalStore(client.session.subscribe, client.session.getSnapshot, client.session.getSnapshot);
  const attemptedAccountAccess = useRef(false);
  const admitted = useRef(false);
  const owner = useRef<string | undefined>(undefined);
  const accountEpoch = useRef(0);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const allowed = state.status === "ready" && state.tables.my_app_access?.[0]?.allowed === true;
  useEffect(() => {
    // URL fragments never reach Vercel access logs or HTTP referrers.
    const value = new URLSearchParams(window.location.hash.slice(1)).get("invite");
    if (value) {
      setToken(value);
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }, []);
  useEffect(() => { if (allowed) setToken(""); }, [allowed]);
  useEffect(() => {
    if (allowed || state.status !== 'ready' || attemptedAccountAccess.current) return;
    attemptedAccountAccess.current = true;
    let active = true;
    void import('../lib/account-app').then(({ restoreSignedInAccount }) => restoreSignedInAccount(client)).catch(e => {
      if (active) setError(e instanceof Error ? e.message : 'Could not restore your account.');
    });
    return () => { active = false; };
  }, [allowed, state.status, client]);
  // Selecting a room resubscribes and briefly enters synchronizing. Keep the
  // dashboard mounted (but hidden) so its selected trip/setup state survives.
  // An authoritative denial unmounts it and clears private component drafts.
  if (state.status === 'ready') {
    admitted.current = allowed;
    if (allowed) {
      const nextOwner = state.tables.my_profile?.[0]?.owner?.toHexString?.();
      if (owner.current !== undefined && nextOwner !== owner.current) accountEpoch.current++;
      owner.current = nextOwner;
    }
  }
  if (admitted.current) return <>
    {!allowed && <p role="status">Synchronizing your trip…</p>}
    <div key={accountEpoch.current} style={{ display: allowed ? 'contents' : 'none' }}>{children}</div>
  </>;
  const connected = state.status === "ready";
  return <section className={styles.gate} aria-labelledby="invite-title">
    <div className={styles.icon} aria-hidden="true">✳</div>
    <p className={styles.eyebrow}>A LITTLE COMMON GROUND / PRIVATE PREVIEW</p>
    <h2 id="invite-title">Good company.<br />By invitation.</h2>
    <p>Tandem is opening a few places at a time. Use your personal invitation to meet your companion and start planning together.</p>
    <p>Already have an account? <a href="/account">Sign in to restore your profile and trips →</a></p>
    <form onSubmit={async event => {
      event.preventDefault(); setError(""); setPending(true);
      try { window.location.assign(`/invite#invite=${encodeURIComponent(token.trim())}`); }
      catch { setError("This invitation could not be accepted. It may be expired, already used, or revoked. Check your connection or ask the team for a new link."); }
      finally { setPending(false); }
    }}>
      <label htmlFor="app-invitation">Your invitation code</label>
      <input id="app-invitation" type="password" autoComplete="off" spellCheck={false} value={token} onChange={e => setToken(e.target.value)} maxLength={128} placeholder="Open your invitation link or paste its code" disabled={pending} />
      <button disabled={!connected || pending || !/^[A-Za-z0-9_-]{43}$/.test(token.trim())}>{pending ? "Opening registration…" : "Create your account →"}</button>
    </form>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {!connected && <p role="status">{state.error ? "We couldn’t connect. Refresh to try again." : "Connecting securely…"}</p>}
    <p className={styles.fine}>Each invitation creates one account. Sign in with your email and password to access your profile and trips from any device.</p>
    <a href="/#demo">Watch the public demo ↗</a>
  </section>;
}
