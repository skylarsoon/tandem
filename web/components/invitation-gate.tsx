"use client";
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import type { connect } from "../lib/client";
import styles from "./invitation-gate.module.css";

export function InvitationGate({ client, children }: { client: ReturnType<typeof connect>; children: ReactNode }) {
  const state = useSyncExternalStore(client.session.subscribe, client.session.getSnapshot, client.session.getSnapshot);
  const attemptedAdminAccess = useRef(false);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [submitted, setSubmitted] = useState(false);
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
    if (allowed || state.status !== 'ready' || attemptedAdminAccess.current) return;
    attemptedAdminAccess.current = true;
    let active = true;
    void import('../lib/admin-app').then(({ grantAdminAppAccess }) => grantAdminAppAccess(client)).catch(e => {
      if (active) setError(e instanceof Error ? e.message : 'Could not check admin access.');
    });
    return () => { active = false; };
  }, [allowed, state.status, client]);
  if (allowed) return <>{children}</>;
  const connected = state.status === "ready";
  return <section className={styles.gate} aria-labelledby="invite-title">
    <div className={styles.icon} aria-hidden="true">✳</div>
    <p className={styles.eyebrow}>A LITTLE COMMON GROUND / PRIVATE PREVIEW</p>
    <h2 id="invite-title">Good company.<br />By invitation.</h2>
    <p>Tandem is opening a few places at a time. Use your personal invitation to meet your companion and start planning together.</p>
    <form onSubmit={async event => {
      event.preventDefault(); setError(""); setPending(true); setSubmitted(false);
      try { await client.connection.reducers.redeemAppInvitation({ token: token.trim() }); setSubmitted(true); }
      catch { setError("This invitation could not be accepted. It may be expired, already used, or revoked. Check your connection or ask the team for a new link."); }
      finally { setPending(false); }
    }}>
      <label htmlFor="app-invitation">Your invitation code</label>
      <input id="app-invitation" type="password" autoComplete="off" spellCheck={false} value={token} onChange={e => setToken(e.target.value)} maxLength={128} placeholder="Open your invitation link or paste its code" disabled={pending} />
      <button disabled={!connected || pending || !/^[A-Za-z0-9_-]{43}$/.test(token.trim())}>{pending ? "Accepting invitation…" : "Accept invitation →"}</button>
    </form>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {submitted && <p role="status">Invitation accepted. Opening your space…</p>}
    {!connected && <p role="status">{state.error ? "We couldn’t connect. Refresh to try again." : "Connecting securely…"}</p>}
    <p className={styles.fine}>Each link can be used once. Access stays in this browser. For another device or a cleared browser, ask the team for a new invitation.</p>
    <a href="/#demo">Watch the public demo ↗</a>
  </section>;
}
