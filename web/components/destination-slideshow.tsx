"use client";

import { useEffect, useRef, useState } from "react";

const destinations = [
  { name: "The Dolomites", country: "Italy", note: "A little fresh air. A whole new perspective.", image: "dolomites", alt: "A wooden boat on a turquoise lake beneath forested mountains" },
  { name: "Bali", country: "Indonesia", note: "Slow mornings, somewhere far from the everyday.", image: "bali", alt: "A Balinese temple framed by tropical greenery" },
  { name: "Kyoto", country: "Japan", note: "Take the scenic route. See where it leads.", image: "kyoto", alt: "A traditional Japanese street leading toward a pagoda at sunset" },
];

export function DestinationSlideshow() {
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(true);
  const [visible, setVisible] = useState(true);
  const rotationIntent = useRef<boolean | null>(null);
  const rotating = !paused && !hovered && !reducedMotion && visible;

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updateMotion = () => setReducedMotion(media.matches);
    const updateVisibility = () => setVisible(!document.hidden);
    updateMotion();
    updateVisibility();
    media.addEventListener("change", updateMotion);
    document.addEventListener("visibilitychange", updateVisibility);
    return () => {
      media.removeEventListener("change", updateMotion);
      document.removeEventListener("visibilitychange", updateVisibility);
    };
  }, []);

  useEffect(() => {
    if (!rotating) return;
    const timer = window.setTimeout(() => setActive(index => (index + 1) % destinations.length), 6000);
    return () => window.clearTimeout(timer);
  }, [active, rotating]);

  function move(step: number) {
    setPaused(true);
    setActive(index => (index + step + destinations.length) % destinations.length);
  }

  return <section className="conversation-panel welcome-panel" aria-label="Travel inspiration">
    <div className="welcome-copy">
      <p className="eyebrow">LESS ORGANIZING. MORE GOING.</p>
      <h2>Where should we begin?</h2>
      <p>A short drive, a long weekend, a place you’ve always wanted to see. Start a trip on the left and make room for the possibilities.</p>
    </div>
    <div className="destination-carousel" role="region" aria-roledescription="carousel" aria-label="Places to imagine"
      onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
      onFocusCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) setPaused(true); }}>
      <div className="destination-slides" aria-live={rotating ? "off" : "polite"}>
        {destinations.map((destination, index) => <div key={destination.image}
          className={`destination-slide${index === active ? " is-active" : ""}`}
          role="group" aria-roledescription="slide" aria-label={`${index + 1} of ${destinations.length}: ${destination.name}`} aria-hidden={index !== active}>
          <img src={`/destinations/${destination.image}.jpg`} alt={destination.alt} width={1400} height={933} loading={index === 0 ? "eager" : "lazy"} />
          <div className="destination-caption">
            <p className="eyebrow">{destination.country}</p>
            <h3>{destination.name}</h3>
            <p>{destination.note}</p>
          </div>
        </div>)}
      </div>
      <span className="destination-stamp" aria-hidden="true">A LITTLE INSPIRATION ↗</span>
      <div className="destination-toolbar">
        <div className="destination-pagination" aria-label="Choose a destination">
          {destinations.map((destination, index) => <button key={destination.image} type="button"
            aria-label={`Show ${destination.name}`} aria-current={index === active ? "true" : undefined}
            onClick={() => { setPaused(true); setActive(index); }}><span /></button>)}
        </div>
        <div className="destination-actions">
          <span className="destination-count" aria-hidden="true">0{active + 1} / 0{destinations.length}</span>
          {!reducedMotion && <button type="button" aria-label={paused ? "Play slideshow" : "Pause slideshow"}
            // Preserve the requested action when pointer focus also pauses rotation.
            onPointerDown={() => { rotationIntent.current = !paused; }}
            onPointerCancel={() => { rotationIntent.current = null; }}
            onBlur={() => { rotationIntent.current = null; }}
            onClick={() => { setPaused(rotationIntent.current ?? !paused); rotationIntent.current = null; }}><span aria-hidden="true">{paused ? "▷" : "Ⅱ"}</span></button>}
          <button type="button" aria-label="Previous destination" onClick={() => move(-1)}><span aria-hidden="true">←</span></button>
          <button type="button" aria-label="Next destination" onClick={() => move(1)}><span aria-hidden="true">→</span></button>
        </div>
      </div>
    </div>
    <p className="welcome-footnote">Good trips start with “what if…”</p>
  </section>;
}
