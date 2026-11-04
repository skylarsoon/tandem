"use client";
import { useEffect, useRef, useState } from 'react';
import { PipCompanion } from './pip-companion';
import { WaterBackground } from './water-background';
import styles from './landing-page.module.css';
import type { Gaze } from '../lib/face-presence';
const crew = [
  { color: 'lavender' as const, name: 'Skylar’s Pip', note: 'A little more adventure.', className: 'one' },
  { color: 'peach' as const, name: 'Ronit’s Pip', note: 'A little room in the budget.', className: 'two' },
  { color: 'mint' as const, name: 'Anna’s Pip', note: 'A little less rushing.', className: 'three' },
  { color: 'yellow' as const, name: 'Elena’s Pip', note: 'A little time together.', className: 'four' },
];
const places = [
  { name: 'Kyoto', country: 'JAPAN', image: 'kyoto', theme: 'A little discovery', alt: 'A traditional Kyoto street and pagoda at sunset' },
  { name: 'The Dolomites', country: 'ITALY', image: 'dolomites', theme: 'A little fresh air', alt: 'A boat on a turquoise lake below the Dolomites' },
  { name: 'Bali', country: 'INDONESIA', image: 'bali', theme: 'A little slower', alt: 'A Balinese temple surrounded by tropical greenery' },
];
const chapters = [{ label: 'Meet your Pip', time: 0, stamp: '00:00' }, { label: 'Your preferences', time: 75.5, stamp: '01:15' }, { label: 'Live research', time: 93.5, stamp: '01:33' }, { label: 'Agent discussion', time: 157, stamp: '02:37' }, { label: 'Your approval', time: 178, stamp: '02:58' }];
const activitySteps = [
  { label: 'Research', title: 'The search, with the details.', description: 'See the route, dates and traveller count sent to the provider. Follow its actual status and open the source behind a result.', detail: 'SFO → SAN', meta: '20–22 NOV · ROUND TRIP', rows: ['Route & dates', 'Provider reference', 'Returned options'], icon: '⌕' },
  { label: 'Discuss', title: 'The give-and-take, out loud.', description: 'Follow shared messages between personal agents and the mediator. When a tradeoff needs your permission, your Pip comes back to you privately.', detail: 'Every traveller has a voice.', meta: 'PERSONAL AGENTS ↔ MEDIATOR', rows: ['Compare the actual options', 'Check everyone’s requirements', 'Ask before a compromise'], icon: '↔' },
  { label: 'Review', title: 'The final decision is yours.', description: 'Review the exact proposal and your share of the cost. Each traveller approves for themselves. Agreeing to a plan does not make a booking.', detail: 'Your yes. Their yes.', meta: 'SEPARATE HUMAN APPROVALS', rows: ['The exact itinerary', 'Your own cost breakdown', 'Everyone’s recorded decision'], icon: '✓' },
];
const Arrow = () => <svg className={styles.arrowIcon} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M5 19 19 5M5 5h14v14"/></svg>;
const Spark = () => <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" focusable="false"><path d="M12 2v20M2 12h20M5 5l14 14M5 19 19 5"/></svg>;
export function LandingPage() {
  const root = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const pendingSeek = useRef<number | null>(null);
  const [videoPlaying, setVideoPlaying] = useState(false);
  const [destination, setDestination] = useState(0), [activity, setActivity] = useState(0);
  const [videoTime, setVideoTime] = useState(0), [videoNotice, setVideoNotice] = useState('');
  const [pageVisible, setPageVisible] = useState(true);
  const place = places[destination], stage = activitySteps[activity];
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
    const visibility = () => setPageVisible(!document.hidden);
    document.addEventListener('visibilitychange', visibility); visibility();
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setMotion(!media.matches); update(); media.addEventListener('change', update);
    const observer = new IntersectionObserver(entries => entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add(styles.visible); observer.unobserve(e.target); } }), { threshold: .12 });
    root.current?.querySelectorAll('[data-reveal]').forEach(el => observer.observe(el));
    return () => { observer.disconnect(); media.removeEventListener('change', update); document.removeEventListener('visibilitychange', visibility); };
  }, []);
  function playChapter(time: number) {
    const player = video.current; if (!player) return;
    setVideoNotice(''); pendingSeek.current = time;
    if (player.readyState >= 1) {
      try { player.currentTime = time; pendingSeek.current = null; } catch { /* Seek after metadata loads. */ }
    }
    // Keep play() in the tap handler: iOS requires a direct user gesture.
    void player.play().catch(() => setVideoNotice('Tap Play demo to try again, or open the video directly below.'));
  }
  return <div ref={root} className={`${styles.landing} ${!motion ? styles.paused : ''}`}>
    <WaterBackground active={motion && pageVisible && !videoPlaying} className={styles.water}/><div className={styles.waterVeil} aria-hidden="true"/>
    <a href="#content" className={styles.skip}>Skip to content</a>
    <nav className={styles.nav} aria-label="Main navigation">
      <a href="/" aria-label="Tandem home" className={styles.logo}><img src="/brand/tandem.svg" alt="tandem" width="215" height="46" /></a>
      <div className={styles.navLinks}><a href="#how-it-works">How it works</a><a href="#meet-pip">Meet Pip</a><a href="#demo">The demo</a></div>
      <div className={styles.navActions}><a className={styles.signIn} href="/account">Sign in</a><a className={styles.navCta} href="/waitlist">Join the waitlist <Arrow /></a></div>
    </nav>
    <main id="content" className={styles.main}>
      <section className={styles.hero}>
        <div className={styles.heroCopy}>
          <div className={styles.eyebrow}><span className={styles.dot} /> A LITTLE COMMON GROUND</div>
          <h1>Good trips.<br />Different people.<br /><span>One happy plan.<svg viewBox="0 0 480 18" aria-hidden="true"><path d="M4 12 Q230 -2 475 8 M85 17 Q280 7 432 12"/></svg></span></h1>
          <p>Your budget. Their must-sees. Everyone’s little preferences. Meet the AI companions that bring it all together.</p>
          <div className={styles.actions}><a className={styles.primary} href="/waitlist">Join the waitlist <Arrow /></a><a className={styles.secondary} href="#demo"><span aria-hidden="true">▷</span> See it in action</a></div>
          <div className={styles.heroFoot}><span aria-hidden="true"><Spark /></span> A voice for everyone. The final say stays yours.</div>
        </div>
        <div className={styles.world} aria-label="Interactive illustration of four companions planning a trip" onPointerMove={event => { if (!motion || event.pointerType !== 'mouse') return; const box = event.currentTarget.getBoundingClientRect(); event.currentTarget.style.setProperty('--tilt-x', `${((event.clientX-box.left)/box.width-.5)*6}deg`); event.currentTarget.style.setProperty('--tilt-y', `${((event.clientY-box.top)/box.height-.5)*-4}deg`); }} onPointerLeave={event => { event.currentTarget.style.setProperty('--tilt-x','0deg'); event.currentTarget.style.setProperty('--tilt-y','0deg'); }}>
          <div className={styles.destinationPicker} aria-label="Choose an illustrative destination">{places.map((p,i) => <button key={p.name} aria-pressed={destination === i} onClick={() => setDestination(i)}>{p.name}</button>)}</div>
          <div className={styles.worldArt}>
          <div className={styles.orbit} /><div className={styles.orbitTwo} />
          <svg className={styles.connections} viewBox="0 0 620 580" aria-hidden="true">{['M150 130 Q310 180 310 300','M480 130 Q400 230 310 300','M140 450 Q230 350 310 300','M490 440 Q420 340 310 300'].map((d,i) => <path key={d} d={d} className={selected === i ? styles.activePath : ''}/>)}</svg>
          <div className={styles.tripCard}><div className={styles.postcardImage}><img key={place.image} src={`/destinations/${place.image}.jpg`} alt={place.alt} width="380" height="240" fetchPriority="high"/><span>{place.country} / WITH YOUR PEOPLE</span></div><div className={styles.postcardCopy} aria-live="polite"><div className={styles.ticketTop}><span>{place.theme}</span><span><Arrow /></span></div><h2>{place.name},<br/>together.</h2><div className={styles.ticketLine}/><div className={styles.ticketBottom}><span>4 PERSPECTIVES · 1 GETAWAY</span><span className={styles.check}><Spark /></span></div></div></div>
          {crew.map((p,i) => <button key={p.color} className={`${styles.pip} ${styles[p.className]} ${selected === i ? styles.selected : ''}`} onClick={() => { setSelected(i); setWave(v => v + 1); }} aria-pressed={selected === i} aria-label={`Meet ${p.name}`}><span className={styles.pipVisual}><PipCompanion active={motion && pageVisible && !videoPlaying} waveToken={selected === i ? wave : 0} variant={p.color} /></span><span className={styles.pipName}>{p.name}</span></button>)}
          <div key={selected} className={styles.bubble} aria-live="polite"><span className={styles.bubbleDot} />{crew[selected].note}</div>
          <span className={styles.sparkOne}><Spark /></span><span className={styles.sparkTwo}>✧</span>
          </div><div className={styles.sceneBottom}><span>ILLUSTRATIVE TRIP · TAP A PIP TO SAY HELLO</span><button onClick={() => setMotion(m => !m)} aria-label={motion ? 'Pause animations' : 'Play animations'}>{motion ? 'Ⅱ' : '▷'} Motion {motion ? 'on' : 'off'}</button></div>
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
          <article><div className={`${styles.stepArt} ${styles.chatArt}`}><div className={styles.smallPip}><PipCompanion active={motion && pageVisible && !videoPlaying} variant="lavender" /></div><div className={styles.privateNote}><span>PRIVATE CHAT · EXAMPLE</span>“Let’s keep it<br />under ${budget}.”<i>✓ Only you and Pip</i></div><div className={styles.budgetChoices} aria-label="Try a private budget preference"><button aria-pressed={budget === 350} onClick={() => setBudget(350)}>$350</button><button aria-pressed={budget === 500} onClick={() => setBudget(500)}>$500</button><span aria-live="polite">Pip remembers.</span></div></div><span className={styles.stepNumber}>01 / TRY A PRIVATE CONVERSATION</span><h3>A companion who gets you.</h3><p>Tell Pip what matters, by voice or text. Your personal reasons stay private; you decide what your agent can act on.</p></article>
          <article><div className={`${styles.stepArt} ${styles.discussionArt}`}><span className={styles.exampleLabel}>AGENT DISCUSSION · EXAMPLE</span><div className={styles.turnHead}><div className={styles.turnPip}><PipCompanion active={motion && pageVisible && !videoPlaying} variant={turns[discussion].color} /></div><span>{turns[discussion].name}</span><i>•••</i></div><div className={styles.turnText} aria-live="polite">{turns[discussion].line}</div><div className={styles.turnReply}>{turns[discussion].reply}</div><button className={styles.nextTurn} onClick={() => setDiscussion(v => (v + 1) % turns.length)}>Follow the conversation <span>→</span></button></div><span className={styles.stepNumber}>02 / FOLLOW THE GIVE-AND-TAKE</span><h3>Many voices. Common ground.</h3><p>Your companions research options and compare needs together. If a compromise needs your say, Pip asks you privately.</p></article>
          <article><div className={`${styles.stepArt} ${styles.approvalArt}`}><div className={styles.approvalCard}><span>YOUR FINAL SAY · EXAMPLE</span><strong>{approvals === 4 ? 'Everyone is on board.' : 'A plan for all of us.'}</strong><div className={styles.approvalDots}>{colors().map((c,i)=><i key={c} style={{background:i < approvals ? c : '#efeee7',color:i < approvals ? '#354f34' : '#b9beb0'}}>{i < approvals ? '✓' : '·'}</i>)}</div><small aria-live="polite">{approvals} of 4 people approved.</small><button className={styles.approveExample} onClick={() => setApprovals(v => v === 4 ? 0 : v + 1)}>{approvals === 4 ? 'Try again ↺' : 'Add an approval ✓'}</button></div></div><span className={styles.stepNumber}>03 / GIVE EVERYONE A SAY</span><h3>Nothing agreed without you.</h3><p>Review the exact plan and give your own approval. Your agent helps you decide. It never makes the final decision for you.</p></article>
        </div>
      </section>
      <section className={styles.transparency} data-reveal>
        <div className={styles.transparencyCopy}><div className={styles.eyebrow}>A PLAN YOU CAN FOLLOW</div><h2>See the work.<br/><span>Not just the wait.</span></h2><p>Follow your companions from the first search to the final decision. Open the activity panel to see provider updates, results, and what needs attention.</p><a href="/app">Step inside your trip <Arrow /></a></div>
        <div className={styles.activityExample} aria-label="Interactive activity preview, not a live search"><div className={styles.activityExampleTop}><span>BEHIND THE PLAN</span><small>INTERACTIVE EXAMPLE</small></div><div className={styles.activityTabs} aria-label="Explore planning stages">{activitySteps.map((s,i) => <button key={s.label} aria-pressed={activity === i} onClick={() => setActivity(i)}><span>0{i+1}</span>{s.label}</button>)}</div><div key={activity} className={styles.activityPreview} aria-live="polite"><div className={styles.previewDetail}><span className={styles.previewIcon} aria-hidden="true">{stage.icon}</span><div><small>{stage.meta}</small><strong>{stage.detail}</strong></div></div><h3>{stage.title}</h3><p>{stage.description}</p><ul>{stage.rows.map(row => <li key={row}><span><Arrow /></span>{row}</li>)}</ul></div><p className={styles.exampleFoot}>A guided preview. The app shows actual searches and agent messages.</p></div>
      </section>
      <section id="meet-pip" className={styles.manifesto} data-reveal>
        <PipShowcase motion={motion && pageVisible && !videoPlaying}/>
        <div className={styles.manifestoCopy}><div className={styles.eyebrow}>SMALL COMPANION. BIG DIFFERENCE.</div><h2>For the things<br />you don’t want<br />to say in the<br /><span>group chat.</span></h2><p>Maybe the budget’s a stretch. Maybe you need a slower pace. Pip helps your needs be heard, without making you explain yourself to everyone.</p><a href="/profile">Meet your Pip <Arrow /></a></div>
      </section>
      <section id="demo" className={styles.demo} data-reveal>
        <div className={styles.sectionHead}><div><div className={styles.eyebrow}>REAL PEOPLE’S NEEDS. A REAL WORKFLOW.</div><h2>Watch a little<br />common ground happen.</h2></div><p>From the first hello to everyone’s yes.<br />A real trip: flights, stays, activities, and dinner.</p></div>
        <video ref={video} onPlay={() => { setVideoPlaying(true); setVideoNotice(''); }} onPause={() => setVideoPlaying(false)} onEnded={() => setVideoPlaying(false)} onError={() => { setVideoPlaying(false); setVideoNotice('The video could not load. Try opening it directly below.'); }} onLoadedMetadata={event => { if (pendingSeek.current !== null) { event.currentTarget.currentTime = pendingSeek.current; pendingSeek.current = null; } }} onTimeUpdate={event => setVideoTime(event.currentTarget.currentTime)} controls playsInline preload="metadata" poster="/media/demo-poster.png" aria-label="Tandem end-to-end product demonstration"><source src="/media/tandem-demo-compatible.mp4" media="(max-width: 760px)" type="video/mp4" /><source src="/media/tandem-demo.mp4" type="video/mp4" /><track kind="captions" src="/media/tandem-demo-narration.vtt" srcLang="en" label="English narration" />Your browser does not support video. <a href="/media/tandem-demo.mp4">Download the demo.</a></video>
        <div className={styles.videoActions}><button onClick={() => { if (videoPlaying) video.current?.pause(); else playChapter(video.current?.currentTime || 0); }}>{videoPlaying ? 'Pause demo' : 'Play demo with sound'}</button><a href="/media/tandem-demo-compatible.mp4" target="_blank" rel="noopener noreferrer">Open video directly <Arrow /></a></div>
        <div className={styles.chapters} aria-label="Video chapters">{chapters.map((chapter,i) => <button key={chapter.time} aria-current={videoTime >= chapter.time && (i === chapters.length-1 || videoTime < chapters[i+1].time) ? 'true' : undefined} onClick={() => playChapter(chapter.time)}><span>{chapter.stamp}</span>{chapter.label}<i aria-hidden="true"><Arrow /></i></button>)}</div>{videoNotice && <p role="status">{videoNotice}</p>}
        <div className={styles.demoCaption}><span>05:26 / REAL END-TO-END WALKTHROUGH</span><span>Edited production recording · added narration · <a href="/media/tandem-demo-transcript.txt">Narration transcript <Arrow /></a></span></div>
      </section>
      <section id="questions" className={styles.faq} data-reveal><div><p className={styles.eyebrow}>A FEW THINGS YOU MIGHT WONDER</p><h2>Good questions.<br/><span>Clear answers.</span></h2><p className={styles.faqIntro}>Getting started, finding common ground, and staying in control.</p></div><div className={styles.questions}>
        <details name="landing-faq"><summary>What does Tandem help us plan?<span aria-hidden="true">+</span></summary><p>Tandem helps a group coordinate flights, accommodation, activities, and meals around each traveller’s needs. Your personal companion, Pip, gathers your preferences and works with the other agents to put options in front of you.</p></details>
        <details name="landing-faq"><summary>How do I get an invitation?<span aria-hidden="true">+</span></summary><p><a href="/waitlist">Join the waitlist</a> with your email. The team reviews requests and shares a single-use invitation when a place is available. Joining the waitlist does not immediately unlock the app. If your invitation has expired or was already used, ask the person who invited you for help.</p></details>
        <details name="landing-faq"><summary>I’m invited. How do I start a trip?<span aria-hidden="true">+</span></summary><p>Open your invitation link and create an account with your email and password. Introduce yourself to Pip, then create or join a trip. Share your destination, dates, and what you want help planning; each traveller can tell their own Pip about their budget and preferences.</p></details>
        <details name="landing-faq"><summary>Can I use my account on another device?<span aria-hidden="true">+</span></summary><p>Yes. <a href="/account">Sign in</a> with the same email and password to access your profile, companion, and trips on another device. You don’t need another invitation for an existing account.</p></details>
        <details name="landing-faq"><summary>Is a trip invitation the same as app access?<span aria-hidden="true">+</span></summary><p>No. An account invitation lets you register for Tandem. A trip invitation lets someone join a particular trip. Your friends need access to Tandem as well as the invitation to your trip.</p></details>
        <details name="landing-faq"><summary>What stays private, and what does the group see?<span aria-hidden="true">+</span></summary><p>Your private chat with Pip is separate from the shared agent discussion. The group sees shared coordination messages and proposals. Pip can represent your requirements without adding your personal explanations to the group chat.</p></details>
        <details name="landing-faq"><summary>What if our budgets or preferences don’t match?<span aria-hidden="true">+</span></summary><p>The agents compare the available options against everyone’s requirements. If a compromise needs your decision, Pip asks you privately. You can decline a proposal, explain why, or consider a change. A plan may remain unresolved if no suitable option is available.</p></details>
        <details name="landing-faq"><summary>Can I see what the agents are actually doing?<span aria-hidden="true">+</span></summary><p>Yes. Open the trip’s activity panel to see what is being searched, provider updates, returned results, and anything that needs attention. Agent discussion shows the shared messages, while Your agent is your private conversation with Pip. A search that is waiting, failed, or limited should appear as such rather than as a finished result.</p></details>
        <details name="landing-faq"><summary>Does approving a plan book or charge anything?<span aria-hidden="true">+</span></summary><p>No. Each traveller reviews the exact proposal and their own costs before approving. Approval records agreement on those terms; it does not reserve flights, book a stay, or make a payment. Use the source links to review and book directly when you’re ready.</p></details>
        <details name="landing-faq"><summary>Are prices and availability guaranteed?<span aria-hidden="true">+</span></summary><p>No. Search results reflect what the provider returned at that time. Prices, availability, baggage, and other terms can change. Check the source before booking, including taxes and fees. Estimates or details marked unconfirmed should not be treated as a final quote.</p></details>
        <details name="landing-faq"><summary>Do I have to use voice or turn on my camera?<span aria-hidden="true">+</span></summary><p>No. You can use text throughout. Voice is optional; the current version asks for a Cartesia key under Voice settings to enable it. Microphone access is only needed when you choose to speak. Camera-based eye contact is an optional companion feature, not a requirement for planning.</p></details>
        <details name="landing-faq"><summary>How do I change or reset my password?<span aria-hidden="true">+</span></summary><p>When signed in, open <a href="/account">your account</a> and choose Change password. Admins can change theirs in the admin workspace. If you can’t sign in, contact the person who invited you for a reset; there isn’t a self-service reset email flow yet.</p></details>
      </div></section>
      <section className={styles.finalCta} data-reveal><div className={styles.eyebrow}>YOUR NEXT CHAPTER STARTS HERE</div><h2>A little less planning.<br /><span>A lot more together.</span></h2><a href="/waitlist" className={styles.primary}>Join the waitlist <Arrow /></a><div className={styles.finalSprout}><Spark /></div></section>
    </main>
    <footer className={styles.footer}><a href="/" aria-label="Tandem home"><img src="/brand/tandem.svg" alt="Tandem" width="178" height="38" /></a><span>A little common ground.</span><div><a href="#questions">FAQs <Arrow /></a><a href="/waitlist">Join the waitlist <Arrow /></a><a href="/app">Open the app <Arrow /></a></div><small>Made together at Cal Hacks 13.</small></footer>
  </div>;
}
function colors(){return ['#dfd2f3','#f1d8c6','#c5e4d6','#e4e5bc'];}

function PipShowcase({ motion }: { motion: boolean }) {
  const [color, setColor] = useState(0), [greeting, setGreeting] = useState(0);
  const [gaze, setGaze] = useState<Gaze>({x:0,y:0,present:false});
  return <div className={styles.manifestoArt} onPointerMove={event => { if (!motion || event.pointerType !== 'mouse') return; const box=event.currentTarget.getBoundingClientRect(); setGaze({x:((event.clientX-box.left)/box.width-.5)*.7,y:((event.clientY-box.top)/box.height-.5)*.5,present:true}); }} onPointerLeave={() => setGaze({x:0,y:0,present:false})}>
    <div className={styles.pipHalo}/><PipCompanion active={motion} variant={crew[color].color} waveToken={greeting} gaze={gaze}/><span key={greeting} className={styles.hello}>{greeting ? 'Good to see you, too.' : 'Hi. I’m in your corner.'}</span>
    <div className={styles.pipControls}><div aria-label="Preview companion colours">{crew.map((p,i) => <button key={p.color} aria-label={`Preview ${p.color} Pip`} aria-pressed={color===i} onClick={() => setColor(i)} style={{background:colors()[i]}} />)}</div><button className={styles.sayHello} onClick={() => setGreeting(v => v+1)}>Say hello <span aria-hidden="true"><Arrow /></span></button></div><div className={styles.privateBadge}>TRY A COLOUR. SAY HELLO. MOVE YOUR POINTER.</div>
  </div>;
}
