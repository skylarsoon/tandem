/** Presentation shared by the room and profile views. Assets and tokens come
 * from the Product Screens page in the Tandem Figma file. */
export function AppNavigation() {
  return (
    <>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <nav className="app-nav" aria-label="Main navigation">
        <a className="wordmark" href="/" aria-label="Tandem home">tandem</a>
        <a className="nav-plans" href="/">Your plans</a>
        <a className="nav-profile" href="/profile">Your profile</a>
        <span className="nav-note">YOUR OWN SPACE</span>
      </nav>
    </>
  );
}

export function HomeWelcome({ available = true }: { available?: boolean }) {
  return (
    <section className="home-welcome" aria-labelledby="welcome-title">
      <p className="eyebrow">A LITTLE LESS PLANNING. A LOT MORE TOGETHER.</p>
      <div className="welcome-panel">
        <div className="welcome-copy">
          <h1 id="welcome-title">Good things happen<br />when we go together.</h1>
          <p>Your people. Your own companion. A plan everyone gets a say in.</p>
          <div className="actions hero-actions">
            {available ? (
              <>
                <a className="button primary" href="#create-plan">Start a new plan</a>
                <a className="button secondary" href="#join-plan">Join your friends</a>
              </>
            ) : (
              <>
                <button className="primary" disabled>Start a new plan</button>
                <button className="secondary" disabled>Join your friends</button>
              </>
            )}
          </div>
        </div>
        <img className="welcome-art" src="/artwork/companions.png" alt="Four pastel companions gathered around a shared plan" width={548} height={300} />
      </div>
    </section>
  );
}

export function ProfileCompanion() {
  return (
    <aside className="companion-panel" aria-label="Your private companion">
      <p className="eyebrow">YOUR COMPANION / JUST FOR YOU</p>
      <h2>I want to get<br />you right.</h2>
      <img className="pip-art" src="/artwork/pip.png" alt="Pip, a lavender companion with a little green sprout" width={368} height={300} />
      <p>This is how I’ll get to know you over time. You can change your answers whenever you like.</p>
      <div className="companion-note">
        <strong>Your profile is a starting point</strong>
        <p>You confirm what applies to each trip before it becomes part of the plan.</p>
      </div>
    </aside>
  );
}
