"use client";
import { useEffect, useRef, useState } from 'react';
import { PipCompanion } from './pip-companion';
import styles from './landing-page.module.css';
const crew = [
  { color: 'lavender' as const, name: 'Skylar’s Pip', note: 'A little more adventure.', className: 'one' },
  { color: 'peach' as const, name: 'Ronit’s Pip', note: 'A little room in the budget.', className: 'two' },
  { color: 'mint' as const, name: 'Anna’s Pip', note: 'A little less rushing.', className: 'three' },
  { color: 'yellow' as const, name: 'Elena’s Pip', note: 'A little time together.', className: 'four' },
];
const Arrow = () => <span aria-hidden="true">↗</span>;
export function LandingPage() {
  const root = useRef<HTMLDivElement>(null);
  const [motion, setMotion] = useState(true);
  const [selected, setSelected] = useState(0);
  const [wave, setWave] = useState(0);
  const [budget, setBudget] = useState(500);
  const [discussion, setDiscussion] = useState(0);
  const [approvals, setApprovals] = useState(0);
  const turns = [
    { name: 'Ronit’s Pip', line: 'Can we keep a little more room in the budget?', reply: 'Anna’s Pip: I can be flexible on departure time.', color: 'peach' as const },
    { name: 'Anna’s Pip', line: 'An earlier flight could work. Let’s check with everyone.', reply: 'Skylar’s Pip: I’ll ask privately before agreeing.', color: 'mint' as const },
    { name: 'Skylar’s Pip', line: 'That compromise works. Ready for everyone to review?', reply: 'Elena’s Pip: One shared plan. Four final decisions.', color: 'lavender' as const },
  ];
  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setMotion(!media.matches); update(); media.addEventListener('change', update);
    const observer = new IntersectionObserver(entries => entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add(styles.visible); observer.unobserve(e.target); } }), { threshold: .12 });
    root.current?.querySelectorAll('[data-reveal]').forEach(el => observer.observe(el));
    return () => { observer.disconnect(); media.removeEventListener('change', update); };
  }, []);
  return <div ref={root} className={`${styles.landing} ${!motion ? styles.paused : ''}`}>
    <a href="#content" className={styles.skip}>Skip to content</a>
    <nav className={styles.nav} aria-label="Main navigation">
      <a href="/" aria-label="Tandem home" className={styles.logo}><img src="/brand/tandem.svg" alt="tandem" width="215" height="46" /></a>
      <div className={styles.navLinks}><a href="#how-it-works">How it works</a><a href="#meet-pip">Meet Pip</a><a href="#demo">The demo</a></div>
      <a className={styles.navCta} href="/app">Open Tandem <Arrow /></a>
    </nav>
    <main id="content" className={styles.main}>
      <section className={styles.hero}>
        <div className={styles.heroCopy}>
          <div className={styles.eyebrow}><span className={styles.dot} /> A LITTLE COMMON GROUND</div>
          <h1>Good trips.<br />Different people.<br /><span>One happy plan.</span></h1>
          <p>Your budget. Their must-sees. Everyone’s little preferences. Meet the AI companions that bring it all together.</p>
          <div className={styles.actions}><a className={styles.primary} href="/profile">Find your common ground <Arrow /></a><a className={styles.secondary} href="#demo"><span aria-hidden="true">▷</span> See it in action</a></div>
          <div className={styles.heroFoot}><span aria-hidden="true">✳</span> A voice for everyone. The final say stays yours.</div>
        </div>
        <div className={styles.world} aria-label="Four personal companions coming together around one trip">
          <div className={styles.orbit} /><div className={styles.orbitTwo} />
          <svg className={styles.connections} viewBox="0 0 620 580" aria-hidden="true"><path d="M150 130 Q310 180 310 300 M480 130 Q400 230 310 300 M140 450 Q230 350 310 300 M490 440 Q420 340 310 300" /></svg>
          <div className={styles.tripCard}><div className={styles.ticketTop}><span>OUR NEXT CHAPTER</span><span>↗</span></div><h2>San Diego,<br />together.</h2><div className={styles.ticketLine} /><div className={styles.ticketBottom}><span>4 PEOPLE · 1 GETAWAY</span><span className={styles.check}>✓</span></div></div>
          {crew.map((p,i) => <button key={p.color} className={`${styles.pip} ${styles[p.className]} ${selected === i ? styles.selected : ''}`} onClick={() => { setSelected(i); setWave(v => v + 1); }} aria-pressed={selected === i} aria-label={`Meet ${p.name}`}><span className={styles.pipVisual}><PipCompanion active={motion} waveToken={selected === i ? wave : 0} variant={p.color} /></span><span className={styles.pipName}>{p.name}</span></button>)}
          <div key={selected} className={styles.bubble}><span className={styles.bubbleDot} />{crew[selected].note}</div>
          <span className={styles.sparkOne}>✳</span><span className={styles.sparkTwo}>✧</span>
          <div className={styles.sceneBottom}><span>ILLUSTRATIVE TRIP</span><button onClick={() => setMotion(m => !m)} aria-label={motion ? 'Pause animations' : 'Play animations'}>{motion ? 'Ⅱ' : '▷'} Motion {motion ? 'on' : 'off'}</button></div>
        </div>
      </section>
      <section className={styles.sponsors} aria-label="Sponsors"><div className={styles.sponsorCaption}>BUILT AT CAL HACKS 13 <span>WITH THANKS TO OUR SPONSORS</span></div><div className={styles.sponsorLogos}>
        <a href="https://about.meta.com/" aria-label="Meta"><img src="/brand/meta.svg" alt="" /><span className={styles.metaWord}>Meta</span></a>
        <a href="https://apify.com/"><img className={styles.fullLogo} src="/brand/apify.svg" alt="Apify" /></a>
        <a href="https://spacetimedb.com/"><img className={styles.fullLogo} src="/brand/spacetimedb.svg" alt="SpacetimeDB" /></a>
        <a href="https://cartesia.ai/"><img className={styles.fullLogo} src="/brand/cartesia.svg" alt="Cartesia" /></a>
        <a href="https://microsoft.com/"><img className={styles.fullLogo} src="/brand/microsoft.png" alt="Microsoft" /></a>
      </div></section>
      <section id="how-it-works" className={styles.how} data-reveal>
        <div className={styles.sectionHead}><div><div className={styles.eyebrow}>LESS BACK-AND-FORTH. MORE LOOKING FORWARD.</div><h2>From “we should go”<br />to “see you there.”</h2></div><p>Let your companions handle the coordination.<br />Keep the part you actually came for.</p></div>
        <div className={styles.steps}>
          <article><div className={`${styles.stepArt} ${styles.chatArt}`}><div className={styles.smallPip}><PipCompanion active={motion} variant="lavender" /></div><div className={styles.privateNote}><span>PRIVATE CHAT · EXAMPLE</span>“Let’s keep it<br />under ${budget}.”<i>✓ Only you and Pip</i></div><div className={styles.budgetChoices} aria-label="Try a private budget preference"><button aria-pressed={budget === 350} onClick={() => setBudget(350)}>$350</button><button aria-pressed={budget === 500} onClick={() => setBudget(500)}>$500</button><span aria-live="polite">Pip remembers.</span></div></div><span className={styles.stepNumber}>01 / TRY A PRIVATE CONVERSATION</span><h3>A companion who gets you.</h3><p>Tell Pip what matters, by voice or text. Your personal reasons stay private; you decide what your agent can act on.</p></article>
          <article><div className={`${styles.stepArt} ${styles.discussionArt}`}><span className={styles.exampleLabel}>AGENT DISCUSSION · EXAMPLE</span><div className={styles.turnHead}><div className={styles.turnPip}><PipCompanion active={motion} variant={turns[discussion].color} /></div><span>{turns[discussion].name}</span><i>•••</i></div><div className={styles.turnText} aria-live="polite">{turns[discussion].line}</div><div className={styles.turnReply}>{turns[discussion].reply}</div><button className={styles.nextTurn} onClick={() => setDiscussion(v => (v + 1) % turns.length)}>Follow the conversation <span>→</span></button></div><span className={styles.stepNumber}>02 / FOLLOW THE GIVE-AND-TAKE</span><h3>Many voices. Common ground.</h3><p>Your companions research options and compare needs together. If a compromise needs your say, Pip asks you privately.</p></article>
          <article><div className={`${styles.stepArt} ${styles.approvalArt}`}><div className={styles.approvalCard}><span>YOUR FINAL SAY · EXAMPLE</span><strong>{approvals === 4 ? 'Everyone is on board.' : 'A plan for all of us.'}</strong><div className={styles.approvalDots}>{colors().map((c,i)=><i key={c} style={{background:i < approvals ? c : '#efeee7',color:i < approvals ? '#354f34' : '#b9beb0'}}>{i < approvals ? '✓' : '·'}</i>)}</div><small aria-live="polite">{approvals} of 4 people approved.</small><button className={styles.approveExample} onClick={() => setApprovals(v => v === 4 ? 0 : v + 1)}>{approvals === 4 ? 'Try again ↺' : 'Add an approval ✓'}</button></div></div><span className={styles.stepNumber}>03 / GIVE EVERYONE A SAY</span><h3>Nothing agreed without you.</h3><p>Review the exact plan and give your own approval. Your agent helps you decide. It never makes the final decision for you.</p></article>
        </div>
      </section>
      <section id="meet-pip" className={styles.manifesto} data-reveal>
        <div className={styles.manifestoArt}><div className={styles.pipHalo}/><PipCompanion active={motion} variant="lavender" /><span className={styles.hello}>Hi. I’m in your corner.</span><div className={styles.privateBadge}>⌁ A LITTLE TRUST GOES A LONG WAY</div></div>
        <div className={styles.manifestoCopy}><div className={styles.eyebrow}>SMALL COMPANION. BIG DIFFERENCE.</div><h2>For the things<br />you don’t want<br />to say in the<br /><span>group chat.</span></h2><p>Maybe the budget’s a stretch. Maybe you need a slower pace. Pip helps your needs be heard, without making you explain yourself to everyone.</p><a href="/profile">Meet your Pip <Arrow /></a></div>
      </section>
      <section id="demo" className={styles.demo} data-reveal>
        <div className={styles.sectionHead}><div><div className={styles.eyebrow}>REAL PEOPLE’S NEEDS. A REAL WORKFLOW.</div><h2>Watch a little<br />common ground happen.</h2></div><p>From the first hello to everyone’s yes.<br />A complete walkthrough of Tandem.</p></div>
        <video controls playsInline preload="none" poster="/media/demo-poster.png" aria-label="Tandem end-to-end product demonstration"><source src="/media/tandem-demo.mp4" type="video/mp4" />Your browser does not support video. <a href="/media/tandem-demo.mp4">Download the demo.</a></video>
        <div className={styles.demoCaption}><span>05:10 / PRODUCT WALKTHROUGH</span><span>Demo travellers · Live backend · Actual agent voices</span></div>
      </section>
      <section className={styles.finalCta} data-reveal><div className={styles.eyebrow}>YOUR NEXT CHAPTER STARTS HERE</div><h2>A little less planning.<br /><span>A lot more together.</span></h2><a href="/profile" className={styles.primary}>Let’s go somewhere <Arrow /></a><div className={styles.finalSprout}>✳</div></section>
    </main>
    <footer className={styles.footer}><a href="/" aria-label="Tandem home"><img src="/brand/tandem.svg" alt="Tandem" width="178" height="38" /></a><span>A little common ground.</span><div><a href="https://github.com/skylarsoon/tandem">GitHub <Arrow /></a><a href="/app">Open the app <Arrow /></a></div><small>Made together at Cal Hacks 13.</small></footer>
  </div>;
}
function colors(){return ['#dfd2f3','#f1d8c6','#c5e4d6','#e4e5bc'];}
