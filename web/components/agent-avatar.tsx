"use client";

import { useEffect, useRef, useState } from 'react';
import { character, type AgentActivity } from '../lib/agents';

export function AgentAvatar({ variant, size = 'room', activity = 'IDLE', greeting = false, mediator = false, animated = true }: {
  variant?: unknown; size?: 'hero' | 'room' | 'message' | 'picker'; activity?: AgentActivity;
  greeting?: boolean; mediator?: boolean; animated?: boolean;
}) {
  const selected = character(variant);
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
    data-activity={activity} data-motion={animated && visible && activity !== 'DISCONNECTED'} data-greeting={greeting}>
    <span className="agent-avatar-shadow" />
    {failed === selected ? <span className="agent-avatar-fallback">✿</span> : <img className="agent-avatar-image" src={`/agents/${selected}.webp`}
      width={512} height={512} alt="" draggable={false} decoding="async" onError={() => setFailed(selected)} />}
    {mediator && <span className="agent-mediator-emblem">✦</span>}
    {activity !== 'IDLE' && activity !== 'DISCONNECTED' && <span className="agent-activity-cue">{activity === 'WAITING_FOR_OWNER' ? '?' : activity === 'SEARCHING' ? '⌕' : activity === 'CHECKING' ? '✓' : '···'}</span>}
  </span>;
}
