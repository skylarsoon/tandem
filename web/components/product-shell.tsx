'use client';
import type { ReactNode } from 'react';

export function Icon({ name, size = 20 }: { name: 'trips' | 'person' | 'lock' | 'chat' | 'plan' | 'spark' | 'arrow' | 'plus' | 'close'; size?: number }) {
  const paths = {
    trips: <><rect x="4" y="7" width="16" height="14" rx="3"/><path d="M9 7V4h6v3M8 11v6m8-6v6"/></>,
    person: <><circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/></>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/></>,
    chat: <path d="M20 11a8 8 0 0 1-8 8H4l1.7-4A8 8 0 1 1 20 11Z"/>,
    plan: <><rect x="5" y="3" width="14" height="19" rx="3"/><path d="m8 9 1 1 2-3m2 2h3m-8 6 1 1 2-3m2 2h3"/></>,
    spark: <path d="m12 2 2.6 7.4L22 12l-7.4 2.6L12 22l-2.6-7.4L2 12l7.4-2.6Z"/>,
    arrow: <path d="M5 12h14m-6-6 6 6-6 6"/>,
    plus: <path d="M12 5v14M5 12h14"/>,
    close: <path d="m6 6 12 12M6 18 18 6"/>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
export function ProductHeader({ active, children }: { active?: 'trips' | 'profile' | 'account' | 'admin' | 'simulation'; children?: ReactNode }) {
  return <header className="product-header">
    <a className="product-logo" href="/" aria-label="Tandem home"><img src="/brand/tandem.svg" width="150" height="38" alt="tandem" /></a>
    <nav aria-label="App navigation"><a href="/app" aria-label="Your trips" aria-current={active === 'trips' ? 'page' : undefined}><Icon name="trips" size={17}/>Your trips</a><a href="/profile" aria-label="Your companion" aria-current={active === 'profile' ? 'page' : undefined}><Icon name="spark" size={17}/>Your companion</a><a href="/simulation" aria-label="Planning simulation" aria-current={active === 'simulation' ? 'page' : undefined}><Icon name="plan" size={17}/>Simulation</a></nav>
    <div className="product-header-end">{children}<a className="account-link" href="/account" aria-label="Your account" aria-current={active === 'account' ? 'page' : undefined}><Icon name="person" size={18}/></a></div>
  </header>;
}
export function EmptyState({ title, children, icon = 'spark' }: { title: string; children: ReactNode; icon?: Parameters<typeof Icon>[0]['name'] }) {
  return <div className="product-empty"><span className="empty-symbol"><Icon name={icon} size={28}/></span><h3>{title}</h3><p>{children}</p></div>;
}
