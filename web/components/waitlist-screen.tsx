'use client';
import { useState, type FormEvent } from 'react';
import { AgentAvatar } from './agent-avatar';
import styles from './waitlist-screen.module.css';
export function WaitlistScreen() {
  const [busy, setBusy] = useState(false), [done, setDone] = useState(false), [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy) return;
    const form = new FormData(event.currentTarget);
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/waitlist', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: form.get('name'), email: form.get('email'), website: form.get('website'), consent: form.get('consent') === 'on' }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not save your request. Please try again.');
      setDone(true);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not connect. Please try again.'); }
    finally { setBusy(false); }
  }
  return <div className={styles.page}>
    <a href="#waitlist" className={styles.skip}>Skip to waitlist</a>
    <header className={styles.nav}><a href="/" aria-label="Tandem home"><img src="/brand/tandem.svg" alt="Tandem" width="172" height="37"/></a><a href="/account">Already invited? Sign in <span aria-hidden="true">↗</span></a></header>
    <main id="waitlist" className={styles.layout}>
      <section className={styles.story}><p className={styles.eyebrow}>A LITTLE COMMON GROUND</p><h1>Your people.<br/>Your possibilities.<br/><span>Your place here.</span></h1><p className={styles.lede}>Different budgets. Different ideas of a perfect getaway. A little companion to help you find a plan that feels right for everyone.</p>
        <div className={styles.scene} aria-hidden="true"><div className={styles.orbit}/><div className={styles.orbitTwo}/><span className={styles.note}>Good things happen together.</span><div className={styles.pipOne}><AgentAvatar variant="lavender" size="hero"/></div><div className={styles.pipTwo}><AgentAvatar variant="mint" size="hero"/></div><span className={styles.spark}>✳</span></div>
        <div className={styles.benefits}><span>Private preferences</span><i>·</i><span>Shared possibilities</span><i>·</i><span>Your final say</span></div>
      </section>
      <section className={styles.card} aria-labelledby="request-heading">
        {done ? <div className={styles.success} role="status"><span className={styles.check} aria-hidden="true">✓</span><p className={styles.eyebrow}>YOU’RE ON THE LIST</p><h2 id="request-heading">A little closer<br/>to together.</h2><p>Your invitation request is saved. If you’ve joined before, we’ve kept your original place.</p><p>The team will review your request and contact you when an invitation is available. No account has been created yet.</p><a className={styles.primary} href="/">Explore Tandem <span aria-hidden="true">↗</span></a><a className={styles.textLink} href="/account?register=1">Already have a code? Create your account →</a></div> : <>
          <div className={styles.badge}><span/>INVITATION-ONLY EARLY ACCESS</div><h2 id="request-heading">There’s room<br/>for your kind of trip.</h2><p>Join the waitlist for an invitation code. We’re welcoming new travellers a few at a time.</p>
          <form onSubmit={submit} aria-busy={busy}><label>Your name <small>(optional)</small><input name="name" autoComplete="given-name" maxLength={80} placeholder="What should we call you?"/></label><label>Email address<input name="email" type="email" autoComplete="email" required maxLength={254} placeholder="you@example.com"/></label>
            <div className={styles.honey} aria-hidden="true"><label>Website<input name="website" tabIndex={-1} autoComplete="off"/></label></div>
            <label className={styles.consent}><input name="consent" type="checkbox" required/><span>The Tandem team can contact me about my invitation.</span></label>
            {error && <p className={styles.error} role="alert">{error}</p>}<button className={styles.primary} disabled={busy}>{busy ? 'Saving your place…' : 'Join the waitlist'}<span aria-hidden="true">↗</span></button>
          </form><p className={styles.fine}>Your details are shared only with Tandem’s admins to manage your request. An invitation is required to create an account.</p><div className={styles.cardFooter}>Have an invitation code? <a href="/account?register=1">Create an account →</a></div>
        </>}
      </section>
    </main><footer className={styles.footer}><a href="/">← Back to Tandem</a><span>A little less planning. A lot more together.</span></footer>
  </div>;
}
