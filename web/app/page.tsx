import { LandingPage } from "../components/landing-page";
// Run before hydration/fragment restoration. Reloading the landing page starts
// at the top; a fresh navigation to a section still follows its explicit link.
const scrollSetup = `(function(){if('scrollRestoration' in history)history.scrollRestoration='manual';var n=performance.getEntriesByType('navigation')[0];if(n&&n.type==='reload'){history.replaceState(history.state,'',location.pathname+location.search);window.scrollTo(0,0)}})();`;
export default function Page() { return <><script dangerouslySetInnerHTML={{ __html: scrollSetup }}/><LandingPage /></>; }
