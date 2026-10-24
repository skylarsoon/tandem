"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Focus } from "@/components/learn";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
type Snapshot = { tables: Record<string, Row[]> };
type View = { columns: string[]; rows: unknown[][] } | { error: string };
const PEOPLE = ["ava", "ben", "cleo"] as const;

const keyOf = (row: Row) => String(row.id ?? row.scheduled_id ?? row.key ?? row.job_id ?? row.owner ?? JSON.stringify(row));

export function diff(before: Snapshot | null, after: Snapshot) {
  const changes: { table: string; op: "new row" | "changed"; row: Row }[] = [];
  for (const [table, rows] of Object.entries(after.tables)) {
    const old = new Map((before?.tables[table] ?? []).map(r => [keyOf(r), JSON.stringify(r)]));
    for (const row of rows) {
      const prev = old.get(keyOf(row));
      if (prev === undefined) changes.push({ table, op: "new row", row });
      else if (prev !== JSON.stringify(row)) changes.push({ table, op: "changed", row });
    }
  }
  return changes;
}

function ActionButton({ label, action, person, args, hint, busy, run }: { label: string; action: string; person: string;
    args?: Record<string, unknown>; hint?: string; busy: boolean; run: (a: string, p?: string, x?: Record<string, unknown>) => void }) {
  return <button disabled={busy} title={hint} onClick={() => run(action, person, args)} className="mr-1 mb-1 rounded border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50">{label}</button>;
}

export function Sandbox({ setFocus }: { setFocus: (f: Focus) => void }) {
  const [state, setState] = useState<{ roomId: number; people: Record<string, string> } | null | undefined>(undefined);
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<{ action: string; ok: boolean; text: string; changes: ReturnType<typeof diff> }[]>([]);
  const [viewer, setViewer] = useState<(typeof PEOPLE)[number]>("ben");
  const [views, setViews] = useState<Record<string, View> | null>(null);
  const [text, setText] = useState("Flying from SFO. I'd like to keep the whole trip under $800, that's firm.");
  const snapshot = useRef<Snapshot | null>(null);

  const refreshViews = useCallback(async (who: string) => {
    const res = await fetch(`/api/sandbox/views?person=${who}`);
    if (res.ok) setViews(await res.json());
  }, []);

  useEffect(() => {
    fetch("/api/sandbox").then(r => r.json()).then(setState);
  }, []);
  useEffect(() => {
    if (!state) return;
    fetch(`/api/rooms/${state.roomId}?fresh=1`).then(r => r.json()).then(s => { snapshot.current = s; });
    fetch(`/api/sandbox/views?person=${viewer}`).then(r => (r.ok ? r.json() : null)).then(v => v && setViews(v));
  }, [state, viewer]);

  async function run(action: string, person?: string, args?: Record<string, unknown>) {
    setBusy(`${person ?? ""} ${action}`);
    const res = await fetch("/api/sandbox", { method: "POST", body: JSON.stringify({ action, person, args }) }).then(r => r.json());
    let current = state;
    if (action === "create" && res.ok) {
      current = await fetch("/api/sandbox").then(r => r.json());
      setState(current);
      snapshot.current = null;
    }
    let changes: ReturnType<typeof diff> = [];
    if (current) {
      const after: Snapshot = await fetch(`/api/rooms/${current.roomId}?fresh=1`).then(r => r.json());
      changes = diff(snapshot.current, after);
      snapshot.current = after;
      refreshViews(viewer);
    }
    setLog(l => [{ action: `${person ? `${person}: ` : ""}${action}`, ok: res.ok, text: res.ok ? String(res.result ?? "").slice(0, 600) : res.error, changes }, ...l]);
    setBusy(null);
  }

  if (state === undefined) return <p className="mt-2 text-sm text-muted-foreground">Loading…</p>;
  if (state === null) return (
    <Card className="mt-2"><CardContent className="space-y-2 pt-4 text-sm">
      <p>The sandbox is a real room on the shared database with three simulated people: <b>ava</b> (organizer), <b>ben</b> and <b>cleo</b>. Every button calls a real reducer as that person, then shows exactly which rows it wrote and what each person&apos;s browser can now read.</p>
      <button disabled={!!busy} onClick={() => run("create")} className="rounded bg-foreground px-3 py-1.5 text-background">{busy ? "Creating…" : "Create the sandbox room"}</button>
    </CardContent></Card>
  );

  const act = { busy: !!busy, run };
  return (
    <div className="mt-2 grid gap-3 xl:grid-cols-[1.1fr_1fr]">
      <div className="space-y-3">
        <Card>
          <CardHeader><CardTitle className="text-sm">Do something as a person · room #{state.roomId}</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div><span className="mr-2 font-medium">ben &amp; cleo</span>
              <ActionButton {...act} label="ben joins" action="join" person="ben" /><ActionButton {...act} label="cleo joins" action="join" person="cleo" /></div>
            <div><span className="mr-2 font-medium">settings</span>
              {PEOPLE.map(p => <ActionButton {...act} key={p} label={`${p} confirms settings`} action="confirm_settings" person={p} args={{ speakUp: p !== "cleo" }} />)}</div>
            <div><span className="mr-2 font-medium">form path</span>
              <ActionButton {...act} label="ben sets a $700 firm budget (private reason)" action="set_budget" person="ben" args={{ dollars: 700, firm: true, reason: "saving for a car" }} />
              <ActionButton {...act} label="ava: everyone lands within 1 h" action="arrival_window" person="ava" args={{ hours: 1 }} hint="Organizer changes a shared rule: watch revisions bump and jobs get superseded" /></div>
            <div><span className="mr-2 font-medium">onboarding</span>
              <ActionButton {...act} label="ava describes the trip" action="describe_trip" person="ava" args={{ text: "Three of us, Honolulu Feb 12 to 16 2027, beach house together and an airport ride." }} />
              <ActionButton {...act} label="ava confirms the brief" action="confirm_brief" person="ava" />
              <ActionButton {...act} label="▶ run the AI worker once" action="worker" person="ava" hint="One sweep of the real worker for this room: onboarding only, uses Claude, no paid searches" /></div>
            <div className="flex items-center gap-2">
              <select value={viewer} onChange={e => setViewer(e.target.value as (typeof PEOPLE)[number])} className="rounded border bg-background px-1 py-0.5 text-xs">{PEOPLE.map(p => <option key={p}>{p}</option>)}</select>
              <input value={text} onChange={e => setText(e.target.value)} className="flex-1 rounded border bg-background px-2 py-1 text-xs" />
              <ActionButton {...act} label="says this to their agent" action="say" person={viewer} args={{ text }} />
            </div>
            <div><span className="mr-2 font-medium">as {viewer}</span>
              <ActionButton {...act} label="confirm airport" action="confirm_origin" person={viewer} /><ActionButton {...act} label="confirm summary" action="confirm_summary" person={viewer} />
              <ActionButton {...act} label="answer yes" action="answer" person={viewer} args={{ yes: true }} /><ActionButton {...act} label="answer no" action="answer" person={viewer} args={{ yes: false }} /></div>
            <div><span className="mr-2 font-medium">privacy test</span>
              <ActionButton {...act} label={`${viewer} tries to read the raw limits table`} action="read_private_table" person={viewer} /></div>
            {busy && <p className="text-xs text-muted-foreground">Running {busy}…</p>}
          </CardContent>
        </Card>
        {log.map((entry, i) => (
          <Card key={i} className={i === 0 ? "border-sky-400" : "opacity-80"}>
            <CardHeader><CardTitle className="text-sm"><span className="font-mono">{entry.action}</span> <Badge variant={entry.ok ? "secondary" : "destructive"}>{!entry.ok ? "refused (nothing changed)" : entry.changes.length ? "committed" : "done, nothing written"}</Badge></CardTitle></CardHeader>
            <CardContent className="space-y-1 text-xs">
              {entry.text && <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded bg-muted p-2">{entry.text}</pre>}
              {entry.ok && entry.changes.length > 0 && <p className="text-muted-foreground">{entry.changes.length} row{entry.changes.length === 1 ? "" : "s"} written in this one transaction:</p>}
              {entry.changes.map((c, j) => (
                <div key={j}><Badge variant={c.op === "new row" ? "default" : "secondary"} className="mr-1">{c.op}</Badge>
                  <button onClick={() => setFocus({ kind: "table", name: c.table })} className="font-mono text-sky-700 underline decoration-dotted dark:text-sky-300">{c.table}</button>
                  <span className="ml-1 text-muted-foreground">{Object.entries(c.row).filter(([k]) => !k.endsWith("_at")).slice(0, 5).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v).slice(0, 60)}`).join(" · ")}</span></div>
              ))}
            </CardContent>
          </Card>
        ))}
      </div>
      <Card className="h-fit">
        <CardHeader><CardTitle className="text-sm">What {viewer}&apos;s browser actually receives</CardTitle>
          <p className="text-xs text-muted-foreground">Each view queried with {viewer}&apos;s own token, live, exactly as the app would subscribe. Switch person with the dropdown on the left.</p></CardHeader>
        <CardContent className="space-y-2 text-xs">
          {views && Object.entries(views).map(([name, v]) => (
            <details key={name} open={"rows" in v && v.rows.length > 0 && ["my_private_messages", "my_constraints", "room_events"].includes(name)}>
              <summary className="cursor-pointer"><button onClick={() => setFocus({ kind: "entry", name })} className="font-mono underline decoration-dotted">{name}</button>
                <span className="ml-2 text-muted-foreground">{"error" in v ? v.error : `${v.rows.length} row${v.rows.length === 1 ? "" : "s"}`}</span></summary>
              {"rows" in v && v.rows.length > 0 && (
                <div className="mt-1 overflow-x-auto"><table><thead><tr>{v.columns.map(c => <th key={c} className="px-1 text-left font-mono text-muted-foreground">{c}</th>)}</tr></thead>
                  <tbody>{v.rows.slice(-8).map((r, i) => <tr key={i} className="border-t">{r.map((cell, j) => <td key={j} className="max-w-[16rem] truncate px-1 font-mono">{typeof cell === "object" ? JSON.stringify(cell) : String(cell)}</td>)}</tr>)}</tbody></table></div>
              )}
            </details>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
