import { ProductHeader, Icon } from '../components/product-shell';
export default function NotFound() {
  return <main className="product-app"><ProductHeader/><section className="recovery-page"><span className="recovery-mark" aria-hidden="true"><Icon name="trips" size={34}/></span><p className="eyebrow">A SMALL DETOUR / 404</p><h1>Let’s find your way back.</h1><p>This page isn’t here. Your trips and companion are one step away.</p><div className="recovery-actions"><a className="primary-action" href="/app">Go to your trips <Icon name="arrow" size={18}/></a><a href="/">Back to Tandem</a></div></section></main>;
}
