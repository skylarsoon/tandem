"use client";

import { useEffect, useState } from "react";
import type { Focus } from "@/components/learn";
import { diff } from "@/components/sandbox";
import { CHAPTERS, TOUR } from "@/lib/tour";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
type Snapshot = { tables: Record<string, Row[]> };
type Result = { ok: boolean; text: string; changes: ReturnType<typeof diff> } | null;

function cell(column: string, value: unknown, name: (id?: string) => string) {
  if ((column === "owner" || column === "member") && typeof value === "string") return name(value);
  if (value == null) return "—";
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  return text.length > 60 ? `${text.slice(0, 60)}…` : text;
}

// Everyday names for tables in one-line summaries (the folded rows keep the real names).
const PLAIN: Record<string, string> = { participant_constraint: "limit", decision_event: "event", constraint_patch: "proposed change",
  private_message: "private message", trip_brief: "trip brief", membership: "member", participation_preferences: "settings",
  intake: "onboarding", agent_message: "agent message", job_subject: "job link", lease_timer: "timer" };

// "room changed · limit changed · 2 events added"
function summary(changes: NonNullable<Result>["changes"]) {
  const counts = new Map<string, number>();
  for (const c of changes) {
    const key = `${PLAIN[c.table] ?? c.table.replace(/_/g, " ")}|${c.op === "new row" ? "added" : "changed"}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].map(([k, n]) => { const [what, op] = k.split("|"); return n > 1 ? `${n} ${what}s ${op}` : `${what} ${op}`; }).join(" · ");
}

function Fold({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="group rounded-lg border">
      <summary className="cursor-pointer list-none px-4 py-2.5 text-sm text-muted-foreground hover:text-foreground">
        <span className="mr-2 inline-block transition-transform group-open:rotate-90">›</span>{title}
      </summary>
      <div className="border-t px-4 py-3 text-sm">{children}</div>
    </details>
  );
}

export function Tour({ data, name, roomId, rooms, onRoom, setFocus }: { data: Snapshot | null; name: (id?: string) => string; roomId: number | null;
    rooms: { id: number; goal: string }[]; onRoom: (id: number) => void; setFocus: (f: Focus) => void }) {
  const [step, setStep] = useState(0);
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState(false);
  const [sandbox, setSandbox] = useState<{ roomId: number } | null>(null);

  useEffect(() => { fetch("/api/sandbox").then(r => r.json()).then(setSandbox); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "ArrowRight") { setStep(s => Math.min(TOUR.length - 1, s + 1)); setResult(null); }
      if (e.key === "ArrowLeft") { setStep(s => Math.max(0, s - 1)); setResult(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const s = TOUR[step];
  const go = (n: number) => { setStep(n); setResult(null); };

  async function tryIt() {
    if (!s.try) return;
    setBusy(true);
    let room = sandbox?.roomId;
    if (!room) {
      await fetch("/api/sandbox", { method: "POST", body: JSON.stringify({ action: "create" }) });
      for (const p of ["ben", "cleo"]) await fetch("/api/sandbox", { method: "POST", body: JSON.stringify({ action: "join", person: p }) });
      const created = await fetch("/api/sandbox").then(r => r.json());
      setSandbox(created);
      room = created?.roomId;
    }
    const before: Snapshot = await fetch(`/api/rooms/${room}?fresh=1`).then(r => r.json());
    const res = await fetch("/api/sandbox", { method: "POST", body: JSON.stringify({ action: s.try.action, person: s.try.person, args: s.try.args }) }).then(r => r.json());
    const after: Snapshot = await fetch(`/api/rooms/${room}?fresh=1`).then(r => r.json());
    setResult({ ok: res.ok, text: res.ok ? String(res.result ?? "") : res.error, changes: diff(before, after) });
    setBusy(false);
  }

  const rows = s.look ? (data?.tables[s.look.table] ?? []) : [];
  const chapterIndex = CHAPTERS.indexOf(s.chapter);
  const firstOf = (chapter: string) => TOUR.findIndex(t => t.chapter === chapter);
  const inChapter = TOUR.filter(t => t.chapter === s.chapter);
  const position = inChapter.indexOf(s);

  return (
    <div className="mx-auto max-w-2xl space-y-6 py-4">
      {/* Chapter stepper */}
      <div className="flex flex-wrap items-center gap-1 text-xs">
        {CHAPTERS.map((c, i) => (
          <button key={c} onClick={() => go(firstOf(c))}
            className={`rounded-full px-3 py-1 ${i === chapterIndex ? "bg-foreground text-background" : i < chapterIndex ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted"}`}>
            {i + 1}. {c}
          </button>
        ))}
      </div>

      <div className="space-y-3">
        <div className="flex items-center gap-1.5">
          {inChapter.map((_, i) => <span key={i} className={`h-1.5 w-6 rounded-full ${i <= position ? "bg-foreground" : "bg-muted"}`} />)}
        </div>
        <h2 className="text-2xl font-semibold tracking-tight">{s.title}</h2>
        <p className="text-lg">{s.takeaway}</p>
      </div>

      {/* Tiny diagram */}
      <div className="flex flex-wrap items-center gap-2">
        {s.diagram.map((box, i) => (
          <div key={i} className="flex items-center gap-2">
            {i > 0 && <span className="text-muted-foreground">→</span>}
            <span className="rounded-md border bg-muted/50 px-3 py-1.5 text-sm">{box}</span>
          </div>
        ))}
      </div>

      <p className="leading-relaxed text-muted-foreground">{s.plain}</p>

      {s.try && (
        <div className="space-y-2 rounded-lg border border-sky-300 p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm"><span className="font-medium">Try it.</span> <span className="text-muted-foreground">{s.try.expect}</span></div>
            <button disabled={busy} onClick={tryIt} className="shrink-0 rounded-md bg-sky-600 px-3 py-1.5 text-sm text-white disabled:opacity-50">{busy ? "Running…" : s.try.label}</button>
          </div>
          {result && (
            <div className="text-sm">
              {result.ok ? (
                result.changes.length
                  ? <p>✓ <b>{result.changes.length} rows in one transaction:</b> {summary(result.changes)}</p>
                  : <p>✓ {result.text || "Done; nothing was written."}</p>
              ) : <p>✕ <b>Refused, nothing was written.</b> <span className="text-muted-foreground">{result.text.slice(0, 160)}</span></p>}
              {result.changes.length > 0 && (
                <details className="mt-1 text-xs text-muted-foreground"><summary className="cursor-pointer">show the rows</summary>
                  {result.changes.map((c, j) => <div key={j} className="font-mono">{c.op === "new row" ? "+" : "~"} {c.table}: {Object.entries(c.row).filter(([k]) => !k.endsWith("_at")).slice(0, 4).map(([k, v]) => `${k}=${cell(k, v, name)}`).join(" · ")}</div>)}
                </details>
              )}
            </div>
          )}
        </div>
      )}

      <div className="space-y-2">
        <Fold title="How Tandem uses it"><p className="leading-relaxed">{s.tandem}</p></Fold>
        {s.look && (
          <Fold title={`See the real rows (${s.look.table})`}>
            <div className="mb-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>{s.look.note}</span>
              <select value={roomId ?? ""} onChange={e => onRoom(Number(e.target.value))} className="rounded border bg-background px-1 py-0.5">
                {rooms.map(r => <option key={r.id} value={r.id}>#{r.id} {r.goal}</option>)}
              </select>
            </div>
            {rows.length === 0 ? <p className="text-xs text-muted-foreground">None in this room.</p> : (
              <table className="w-full text-xs">
                <thead><tr className="text-left text-muted-foreground">{s.look.columns.map(c => <th key={c} className="py-1 pr-3 font-mono font-normal">{c}</th>)}</tr></thead>
                <tbody>{rows.slice(0, 5).map((r, i) => <tr key={i} className="border-t">{s.look!.columns.map(c => <td key={c} className="py-1 pr-3">{cell(c, r[c], name)}</td>)}</tr>)}</tbody>
              </table>
            )}
            {rows.length > 5 && <p className="mt-1 text-xs text-muted-foreground">Showing 5 of {rows.length}.</p>}
          </Fold>
        )}
        <Fold title="Go deeper">
          <div className="flex flex-wrap gap-2">
            {s.deeper.map(f => (
              <button key={`${f.kind}:${f.name}`} onClick={() => setFocus(f)} className="rounded-md border px-2 py-1 text-xs hover:bg-muted">
                {f.kind === "concept" ? `What is: ${f.name}` : f.name}
              </button>
            ))}
          </div>
        </Fold>
      </div>

      <div className="flex items-center justify-between border-t pt-4">
        <button disabled={step === 0} onClick={() => go(step - 1)} className="rounded-md px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-30">← Back</button>
        <span className="text-xs text-muted-foreground">{step + 1} / {TOUR.length} · arrow keys work</span>
        <button disabled={step === TOUR.length - 1} onClick={() => go(step + 1)} className="rounded-md bg-foreground px-4 py-1.5 text-sm text-background disabled:opacity-30">Next →</button>
      </div>
    </div>
  );
}
