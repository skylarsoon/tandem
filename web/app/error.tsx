"use client";
import { startTransition } from 'react';
import { ProductHeader, Icon } from '../components/product-shell';
export default function ErrorPage({ retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return <main className="product-app"><ProductHeader/><section className="recovery-page" role="alert"><span className="recovery-mark" aria-hidden="true"><Icon name="spark" size={34}/></span><p className="eyebrow">A MOMENT TO REGROUP</p><h1>We couldn’t open this page.</h1><p>Try loading it again. If you were saving something, check its status before repeating the action.</p><div className="recovery-actions"><button className="primary-action" onClick={() => startTransition(retry)}>Try again <Icon name="arrow" size={18}/></button><a href="/app">Back to your trips</a></div></section></main>;
}
