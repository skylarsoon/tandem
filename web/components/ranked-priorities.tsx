"use client";

import { useRef, useState, type PointerEvent } from "react";
import { move, type Question } from "../lib/profile";

type Ranking = { ranked: string[]; confirmed?: boolean };

export function RankedPriorities({ q, value, disabled, onChange }: {
  q: Question; value?: Ranking; disabled: boolean; onChange: (value: Ranking) => void;
}) {
  const options = q.options ?? [];
  // Preserve the selected order. The catalog determines how many can be ranked.
  const saved = (value?.ranked ?? []).filter(id => options.some(option => option.id === id));
  const limit = Math.min(q.max ?? options.length, options.length);
  const ranked = [...new Set(value ? saved : options.slice(0, limit).map(option => option.id))].slice(0, limit);
  const list = useRef<HTMLOListElement>(null);
  const gesture = useRef<{ id: string; y: number; moved: boolean; target: string } | null>(null);
  const [dragging, setDragging] = useState<string>();
  const [target, setTarget] = useState<string>();
  const [announcement, setAnnouncement] = useState("");
  const label = (id: string) => options.find(option => option.id === id)?.label ?? id;

  function update(next: string[], message: string) {
    onChange({ ranked: next, confirmed: false });
    setAnnouncement(message);
  }

  function moveOne(id: string, direction: -1 | 1) {
    const next = move(ranked, id, direction);
    if (next !== ranked) update(next, `${label(id)} moved to priority ${next.indexOf(id) + 1}.`);
  }

  function start(event: PointerEvent<HTMLButtonElement>, id: string) {
    if (disabled || event.button !== 0) return;
    gesture.current = { id, y: event.clientY, moved: false, target: id };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function drag(event: PointerEvent<HTMLButtonElement>) {
    const current = gesture.current;
    if (!current || disabled) return;
    if (!current.moved && Math.abs(event.clientY - current.y) < 6) return;
    current.moved = true;
    setDragging(current.id);
    let distance = Infinity;
    for (const row of list.current?.querySelectorAll<HTMLElement>("[data-priority]") ?? []) {
      const rect = row.getBoundingClientRect();
      const delta = Math.abs(event.clientY - (rect.top + rect.height / 2));
      if (delta < distance) { distance = delta; current.target = row.dataset.priority!; }
    }
    setTarget(current.target);
  }

  function finish(cancelled = false) {
    const current = gesture.current;
    if (!cancelled && !disabled && current?.moved && current.target !== current.id) {
      const next = ranked.filter(id => id !== current.id);
      next.splice(ranked.indexOf(current.target), 0, current.id);
      update(next, `${label(current.id)} moved to priority ${next.indexOf(current.id) + 1}.`);
    }
    gesture.current = null;
    setDragging(undefined);
    setTarget(undefined);
  }

  return <div className="priority-editor">
    <p className="hint">Choose up to {limit} priorities. Unselected choices stay unranked.</p><div className="chips">{options.map(option => <button type="button" key={option.id} disabled={disabled || (!ranked.includes(option.id) && ranked.length >= limit)} aria-pressed={ranked.includes(option.id)} onClick={() => update(ranked.includes(option.id) ? ranked.filter(id => id !== option.id) : [...ranked, option.id], `${option.label} updated`)}>{option.label}</button>)}</div>
    <div className="priority-heading"><strong>Your priorities</strong><span>Most → least important</span></div>
    <p className="hint" id={`rank-help-${q.id}`}>Most important at the top. Drag the handles to reorder, or use the arrow buttons.</p>
    <ol className="priority-list" ref={list} aria-label="Ranked priorities" aria-describedby={`rank-help-${q.id}`}>
      {ranked.map((id, index) => {
        const option = options.find(item => item.id === id)!;
        return <li key={id} data-priority={id}
          className={`priority-card${dragging === id ? " is-dragging" : ""}${dragging && target === id && target !== dragging ? " is-drop-target" : ""}`}>
          <button type="button" className="priority-handle" aria-label={`Reorder ${option.label}`} disabled={disabled}
            aria-describedby={`rank-help-${q.id}`} onPointerDown={event => start(event, id)} onPointerMove={drag}
            onPointerUp={() => finish()} onPointerCancel={() => finish(true)} onLostPointerCapture={() => finish(true)}
            onKeyDown={event => {
              if (event.key === "ArrowUp" || event.key === "ArrowDown") {
                event.preventDefault(); moveOne(id, event.key === "ArrowUp" ? -1 : 1);
              }
            }}><span aria-hidden="true">⠿</span></button>
          <span className="priority-number" aria-hidden="true">{index + 1}</span>
          <div className="priority-copy"><strong>{option.label}</strong><span>{option.hint}</span></div>
          <div className="priority-controls">
            <button type="button" aria-label={`Move ${option.label} up`} disabled={disabled || index === 0} onClick={() => moveOne(id, -1)}>↑</button>
            <button type="button" aria-label={`Move ${option.label} down`} disabled={disabled || index === ranked.length - 1} onClick={() => moveOne(id, 1)}>↓</button>
          </div>
        </li>;
      })}
    </ol>
    <p className="sr-only" role="status">{announcement}</p>
    {ranked.length > 0 && <label className="check priority-confirm">
      <input type="checkbox" disabled={disabled} checked={!!value?.confirmed && value.ranked.length === ranked.length} onChange={event => onChange({ ranked, confirmed: event.target.checked })} />
      This order is right
    </label>}
  </div>;
}
