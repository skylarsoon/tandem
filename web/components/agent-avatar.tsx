"use client";

import { useEffect, useRef, useState } from 'react';
import type { Gaze } from '../lib/face-presence';
import { PipCompanion } from './pip-companion';
import { character, type AgentActivity } from '../lib/agents';

export function AgentAvatar({ variant, size = 'room', activity = 'IDLE', greeting = false, mediator = false, animated = true, waveToken = 0, gaze, speechTarget }: {
  variant?: unknown; size?: 'hero' | 'room' | 'message' | 'picker'; activity?: AgentActivity;
  greeting?: boolean; mediator?: boolean; animated?: boolean; waveToken?: number; gaze?: Gaze; speechTarget?: string;
}) {
  const selected = character(variant);
  const animateSize = size !== 'message' && size !== 'picker';
  const [failed, setFailed] = useState<string>();
  const [visible, setVisible] = useState(true);
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    let intersecting = true;
    const update = () => setVisible(intersecting && !document.hidden);
    const observer = typeof IntersectionObserver === 'undefined' ? undefined : new IntersectionObserver(entries => {
      intersecting = entries[0]?.isIntersecting ?? true; update();
    });
    if (root.current) observer?.observe(root.current);
    document.addEventListener('visibilitychange', update); update();
    return () => { observer?.disconnect(); document.removeEventListener('visibilitychange', update); };
  }, []);
  return <span ref={root} aria-hidden="true" className={`agent-avatar agent-avatar--${size} agent-avatar--${selected}${mediator ? ' agent-avatar--mediator' : ''}`}
    data-artwork="figma-pip" data-activity={activity} data-motion={animated && animateSize && visible && activity !== 'DISCONNECTED'} data-greeting={greeting}>
    {failed === selected ? <span className="agent-avatar-fallback">✿</span> : <PipCompanion speechTarget={speechTarget} variant={selected} onArtworkError={() => setFailed(selected)} active={animated && animateSize && visible && activity !== 'DISCONNECTED'} waveToken={waveToken} gaze={gaze} />}
    {mediator && <span className="agent-mediator-emblem">✦</span>}
    {activity !== 'IDLE' && activity !== 'DISCONNECTED' && <span className="agent-activity-cue">{activity === 'WAITING_FOR_OWNER' ? '?' : activity === 'SEARCHING' ? '⌕' : activity === 'CHECKING' ? '✓' : '···'}</span>}
  </span>;
}
