"use client";

import type { Character } from '../lib/agents';
import type { Gaze } from '../lib/face-presence';
import { useEffect, useId, useRef } from 'react';

const artwork = '/agents/pip-resting.png';

/** Animates the edited Figma illustration itself, preserving its rendered face. */
export function PipCompanion({ active, waveToken = 0, gaze, variant = 'lavender', speechTarget, onArtworkError }: { speechTarget?: string; onArtworkError?: () => void; variant?: Character; active: boolean; waveToken?: number; gaze?: Gaze }) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const stage = useRef<SVGSVGElement>(null);
  const turn = useRef<HTMLSpanElement>(null);
  const arm = useRef<SVGGElement>(null);
  const lids = useRef<SVGGElement>(null);
  const wave = useRef<() => void>(() => {});
  const lastWaveToken = useRef(waveToken);
  const target = useRef(gaze);
  useEffect(() => {
    const speech = (event: Event) => {
      const detail = (event as CustomEvent<{ variant?: Character; target?: string; open: number; width: number }>).detail;
      const open = (speechTarget ? detail.target === speechTarget : !detail.target && detail.variant === variant) ? detail.open : 0;
      stage.current?.style.setProperty('--pip-speech', String(open));
      stage.current?.style.setProperty('--pip-mouth-width', String(detail.width || 1));
      stage.current?.style.setProperty('--pip-smile-visible', open > .05 ? '0' : '1');
    };
    window.addEventListener('pip-speech', speech);
    return () => window.removeEventListener('pip-speech', speech);
  }, [variant, speechTarget]);

  useEffect(() => { target.current = gaze; }, [gaze]);

  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    let timers: ReturnType<typeof setTimeout>[] = [];
    const animations = new Set<Animation>();
    const later = (fn: () => void, ms: number) => {
      const timer = setTimeout(() => { timers = timers.filter(t => t !== timer); fn(); }, ms);
      timers.push(timer);
    };
    const animate = (element: Element | null, frames: Keyframe[], options: KeyframeAnimationOptions) => {
      if (!element) return;
      const animation = element.animate(frames, options);
      animations.add(animation);
      animation.onfinish = () => { animations.delete(animation); animation.cancel(); };
      return animation;
    };
    const stop = () => {
      timers.forEach(clearTimeout); timers = [];
      animations.forEach(a => a.cancel()); animations.clear();
      wave.current = () => {};
      stage.current?.style.setProperty('--pip-look-x', '0px');
      stage.current?.style.setProperty('--pip-look-y', '0px');
      stage.current?.style.setProperty('--pip-lean', '0deg');
    };
    const start = () => {
      stop();
      if (!active || media.matches) return;
      let waving = false;
      wave.current = () => {
        if (waving) return;
        waving = true;
        animate(arm.current, [
          { transform: 'rotate(0deg)', offset: 0 },
          { transform: 'rotate(-105deg)', offset: .24 },
          { transform: 'rotate(-92deg)', offset: .39 },
          { transform: 'rotate(-112deg)', offset: .53 },
          { transform: 'rotate(-94deg)', offset: .67 },
          { transform: 'rotate(-103deg)', offset: .78 },
          { transform: 'rotate(0deg)', offset: 1 },
        ], { duration: 2300, easing: 'ease-in-out' });
        later(() => { waving = false; }, 2400);
      };
      const blink = () => {
        animate(lids.current, [{ opacity: 0 }, { opacity: 1, offset: .35 }, { opacity: 1, offset: .57 }, { opacity: 0 }], { duration: 190 });
        later(blink, 2800 + Math.random() * 3500);
      };
      later(blink, 1200 + Math.random() * 2000);
      animate(stage.current?.querySelector('.pip-body') ?? null, [
        { transform: 'translateY(0px) scaleY(1)' },
        { transform: 'translateY(-6px) scaleY(1.008)' },
        { transform: 'translateY(0px) scaleY(1)' },
      ], { duration: 4300, iterations: Infinity, easing: 'ease-in-out' });
    };
    start(); media.addEventListener('change', start);
    return () => { stop(); media.removeEventListener('change', start); };
  }, [active]);

  useEffect(() => {
    if (waveToken !== lastWaveToken.current) {
      lastWaveToken.current = waveToken;
      wave.current();
    }
  }, [waveToken]);

  // Eyes respond first; the heavier body follows with a damped spring.
  // Run at display cadence so camera samples never produce visible steps.
  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0, previous = 0;
    const eye = { x: 0, y: 0 }, body = { x: 0, y: 0 }, velocity = { x: 0, y: 0 };
    const paint = () => {
      const style = stage.current?.style;
      // Give everyday head movement a visible response without requiring the
      // person to travel to the edge of the camera frame. Bound extreme poses.
      const bx = Math.tanh(body.x * 2.2) / Math.tanh(2.2);
      const by = Math.tanh(body.y * 1.6) / Math.tanh(1.6);
      if (turn.current) turn.current.style.transform = `perspective(700px) rotateY(${bx * 24}deg)`;
      style?.setProperty('--pip-look-x', `${eye.x * 16}px`);
      style?.setProperty('--pip-face-x', `${bx * 22}px`);
      style?.setProperty('--pip-face-y', `${by * 10}px`);
      style?.setProperty('--pip-face-tilt', '0deg');
      style?.setProperty('--pip-arm-sway', `${bx * -8}deg`);
      style?.setProperty('--pip-look-y', `${eye.y * 8}px`);
      style?.setProperty('--pip-lean', '0deg');
      style?.setProperty('--pip-shift-x', `${bx * 70}px`);
      style?.setProperty('--pip-shift-y', `${by * 38}px`);
    };
    const tick = (now: number) => {
      const dt = Math.min((now - (previous || now)) / 1000, .032);
      previous = now;
      const tracking = target.current?.present;
      const x = tracking ? target.current!.x : 0, y = tracking ? target.current!.y : 0;
      const response = 1 - Math.exp(-18 * dt);
      eye.x += (x - eye.x) * response; eye.y += (y - eye.y) * response;
      for (const axis of ['x', 'y'] as const) {
        const destination = axis === 'x' ? x : y;
        velocity[axis] += ((destination - body[axis]) * 80 - velocity[axis] * 17) * dt;
        body[axis] += velocity[axis] * dt;
      }
      stage.current?.style.setProperty('--pip-smile', `${1 + (tracking ? .12 : .025) + Math.sin(now / 1200) * .035}`);
      paint(); frame = requestAnimationFrame(tick);
    };
    const restart = () => {
      cancelAnimationFrame(frame); previous = 0;
      stage.current?.style.setProperty('--pip-smile', '1');
      eye.x = eye.y = body.x = body.y = velocity.x = velocity.y = 0; paint();
      if (active && !media.matches) frame = requestAnimationFrame(tick);
    };
    restart(); media.addEventListener('change', restart);
    return () => { cancelAnimationFrame(frame); media.removeEventListener('change', restart); };
  }, [active]);

  return <span ref={turn} className="pip-turn"><svg ref={stage} className="pip-companion" viewBox="0 0 1254 1254" aria-hidden="true" focusable="false">
    <defs>
      <filter id={`${id}-tint`} colorInterpolationFilters="sRGB"><feColorMatrix type="hueRotate" values={String({lavender: 0, peach: 115, mint: 240, yellow: 165}[variant])} /></filter>
      <filter id={`${id}-untint`} colorInterpolationFilters="sRGB"><feColorMatrix type="hueRotate" values={String(-{lavender: 0, peach: 115, mint: 240, yellow: 165}[variant])} /></filter>
      <clipPath id={`${id}-leaves`}><path d="M400 0H800V254Q650 215 480 261H400Z" /></clipPath>
      <radialGradient id={`${id}-soft-feature`}><stop offset="78%" stopColor="white" /><stop offset="100%" stopColor="black" /></radialGradient>
      <mask id={`${id}-smile-cover`} maskUnits="userSpaceOnUse" x="655" y="510" width="145" height="90">
        <ellipse cx="727" cy="553" rx="69" ry="40" fill={`url(#${id}-soft-feature)`} />
      </mask>
      <clipPath id={`${id}-smile`}><ellipse cx="727" cy="553" rx="52" ry="23" /></clipPath>
      <mask id={`${id}-cheeks`} maskUnits="userSpaceOnUse" x="480" y="510" width="440" height="135">
        <ellipse cx="559" cy="580" rx="72" ry="57" fill={`url(#${id}-soft-feature)`} />
        <ellipse cx="866" cy="565" rx="43" ry="49" fill={`url(#${id}-soft-feature)`} />
      </mask>
      <clipPath id={`${id}-eye-shapes`}>
        <ellipse cx="601" cy="523" rx="32" ry="33" />
        <ellipse cx="836" cy="509" rx="25" ry="32" />
      </clipPath>
      <mask id={`${id}-body`} maskUnits="userSpaceOnUse" x="0" y="0" width="1254" height="1254">
        <rect width="1254" height="1254" fill="white" /><path d="M931 645 C941 710 954 789 954 861 Q955 885 953 902 L953 1254 H1254 V550 Z" fill="black" transform="translate(2 0)" />
      </mask>
      <radialGradient id={`${id}-feather`}><stop offset="60%" stopColor="white" /><stop offset="100%" stopColor="black" /></radialGradient>
      <mask id={`${id}-eyes`} maskUnits="userSpaceOnUse" x="540" y="450" width="350" height="130">
        <ellipse cx="601" cy="523" rx="46" ry="46" fill={`url(#${id}-feather)`} />
        <ellipse cx="836" cy="509" rx="41" ry="44" fill={`url(#${id}-feather)`} />
      </mask>
    </defs>
    <ellipse className="pip-ground" cx="633" cy="1147" rx="240" ry="23" fill="#594370" opacity=".12" />
    <g className="pip-look"><g className="pip-body"><g filter={variant === 'lavender' ? undefined : `url(#${id}-tint)`}>
      <g ref={arm} className="pip-arm"><image href="/agents/pip-arm.png" x="725" y="555" width="438.9" height="438.9" /></g>
      <image onError={onArtworkError} data-pip-base="true" href={artwork} width="1254" height="1254" mask={`url(#${id}-body)`} />
      <g mask={`url(#${id}-eyes)`}><image href={artwork} y="90" width="1254" height="1254" style={{ filter: 'brightness(.97)' }} /></g>
      <g mask={`url(#${id}-smile-cover)`}><image href={artwork} y="90" width="1254" height="1254" /></g>
      <g mask={`url(#${id}-cheeks)`}><image href={artwork} y="150" width="1254" height="1254" /></g>
      <g className="pip-face">
      <image href={artwork} width="1254" height="1254" mask={`url(#${id}-cheeks)`} />
      <g className="pip-speaking-mouth"><ellipse cx="727" cy="553" rx="33" ry="28" fill="#4a2d3e" /><ellipse cx="727" cy="568" rx="20" ry="8" fill="#cf8293" /></g>
      <g className="pip-smile"><image href={artwork} width="1254" height="1254" clipPath={`url(#${id}-smile)`} /></g>
      <g className="pip-gaze" filter={variant === 'lavender' ? undefined : `url(#${id}-untint)`}><image href={artwork} width="1254" height="1254" clipPath={`url(#${id}-eye-shapes)`} />
      <g ref={lids} className="pip-eyelids" opacity="0">
        <g mask={`url(#${id}-eyes)`}><image href={artwork} y="90" width="1254" height="1254" style={{ filter: 'brightness(.97)' }} /></g>
        <path d="M581 523Q601 536 621 523M820 509Q836 520 852 509" fill="none" stroke="#604743" strokeWidth="5" strokeLinecap="round" />
      </g>
    </g></g></g><image href={artwork} width="1254" height="1254" clipPath={`url(#${id}-leaves)`} /></g></g>
  </svg></span>;
}
