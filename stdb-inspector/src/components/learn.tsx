"use client";

import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CONCEPTS, ENTRIES, TABLES, audienceOf } from "@/lib/knowledge";
import type { Entry, ModuleMap } from "@/lib/module-map";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
type Snapshot = { tables: Record<string, Row[]> };
type Change = { table: string; op: "insert" | "update"; row: Row };
type Tx = { at: number; changes: Change[]; guess: Entry | null; alternatives: Entry[]; concepts: string[] };
export type Focus = { kind: "table" | "entry" | "concept"; name: string } | null;

// Columns that record when a row was written. Every write in one reducer call
// carries the same ctx.timestamp, so rows sharing a stamp came from one call.
const STAMPS: Record<string, "insert" | "update"> = { created_at: "insert", recorded_at: "insert", updated_at: "update", confirmed_at: "update", answered_at: "update" };

export function useModuleMap() {
  const [map, setMap] = useState<ModuleMap | null>(null);
  useEffect(() => { fetch("/api/learn/map").then(r => r.json()).then(setMap); }, []);
  return map;
}

function transactions(data: Snapshot, map: ModuleMap): Tx[] {
  const groups = new Map<number, Change[]>();
  for (const [table, rows] of Object.entries(data.tables)) {
    for (const row of rows) {
      const seen = new Set<number>();
      for (const [column, op] of Object.entries(STAMPS)) {
        const at = row[column];
        if (typeof at !== "number" || seen.has(at)) continue;
        seen.add(at);
        // A row created and last updated in the same call counts once, as an insert.
        const kind = op === "update" && row.created_at === at ? "insert" : op;
        groups.set(at, [...(groups.get(at) ?? []), { table, op: kind, row }]);
      }
    }
  }
  const callable = map.entries.filter(e => e.kind !== "view");
  return [...groups.entries()].sort(([a], [b]) => a - b).map(([at, changes]) => {
    // Which reducers write every table this call touched? The most specific wins.
    const fits = callable.filter(e => changes.every(c => (e.tables[c.table] ?? []).some(op => op === c.op || (c.op === "update" && op === "insert"))))
      .sort((a, b) => Object.keys(a.tables).length - Object.keys(b.tables).length);
    const human = changes.find(c => c.table === "private_message")?.row.sender;
    const ranked = human === "AGENT" ? [...fits.filter(e => e.name === "reply_to_owner"), ...fits.filter(e => e.name !== "reply_to_owner")]
      : human === "HUMAN" ? [...fits.filter(e => e.name === "submit_private_message"), ...fits.filter(e => e.name !== "submit_private_message")] : fits;
    const tables = new Set(changes.map(c => c.table));
    const concepts = ["reducer"];
    if (tables.has("room") && tables.has("job")) concepts.push("revision");
    if (changes.some(c => c.table === "job" && c.op === "update")) concepts.push("lease");
    if ([...tables].some(t => ["private_message", "participant_constraint", "constraint_patch", "question", "participant_impact"].includes(t))) concepts.push("view");
    if (tables.has("lease_timer") || tables.has("review_reminder")) concepts.push("schedule");
    return { at, changes, guess: ranked[0] ?? null, alternatives: ranked.slice(1, 4), concepts };
  });
}

function describe(c: Change, name: (id?: string) => string) {
  const r = c.row;
  switch (c.table) {
    case "private_message": return `${r.sender === "AGENT" ? `agent → ${name(r.owner)}` : `${name(r.owner)} → agent`}: “${String(r.body).slice(0, 140)}”`;
    case "constraint_patch": { const p = JSON.parse(r.patch_json); return `${name(r.owner)}: ${p.key} = ${JSON.stringify(p.value)} · ${r.state}`; }
    case "participant_constraint": return `${name(r.owner)}: ${r.key} = ${r.value_json} (${r.hardness})`;
    case "job": return `job #${r.id} ${r.kind} → ${r.status}${r.outcome ? ` (${r.outcome})` : ""}, attempt ${r.attempt}`;
    case "decision_event": return `${r.type}${r.audience !== "ROOM" ? ` (private to ${name(r.owner)})` : ""}`;
    case "intake": return `${name(r.owner)}: ${r.state}, ${r.question_count} questions`;
    case "question": return `${name(r.owner)}: ${String(r.prompt).slice(0, 100)} · ${r.state}`;
    case "room": return `revisions input ${r.input_revision} · members ${r.membership_revision} · permissions ${r.permission_revision}`;
    case "candidate": return `${r.category} found`;
    case "membership": return `${r.display_name} (${r.role})`;
    default: return Object.entries(r).filter(([k]) => !k.endsWith("_json")).slice(0, 4).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ");
  }
}

const CALLER: Record<string, string> = { person: "a person (browser)", worker: "the AI worker", owner: "the database owner", scheduler: "a scheduled timer", system: "SpacetimeDB itself", anyone: "any caller" };

export function Learn({ data, map, name, setFocus }: { data: Snapshot; map: ModuleMap; name: (id?: string) => string; setFocus: (f: Focus) => void }) {
  const txs = useMemo(() => transactions(data, map), [data, map]);
  const [step, setStep] = useState(0);
  const at = Math.min(step, Math.max(0, txs.length - 1));
  const tx = txs[at];
  if (!tx) return <p className="mt-2 text-sm text-muted-foreground">No timestamped rows in this room yet.</p>;
  const byTable = tx.changes.reduce<Record<string, Change[]>>((acc, c) => ({ ...acc, [c.table]: [...(acc[c.table] ?? []), c] }), {});
  return (
    <div className="mt-2 space-y-3">
      <div className="flex items-center gap-3">
        <button onClick={() => setStep(Math.max(0, at - 1))} className="rounded border px-2 py-1 text-sm hover:bg-muted">◀ previous</button>
        <input type="range" min={0} max={txs.length - 1} value={at} onChange={e => setStep(Number(e.target.value))} className="flex-1" />
        <button onClick={() => setStep(Math.min(txs.length - 1, at + 1))} className="rounded border px-2 py-1 text-sm hover:bg-muted">next ▶</button>
        <span className="w-28 text-right text-xs text-muted-foreground">call {at + 1} of {txs.length}</span>
      </div>
      <Card>
        <CardHeader>
          <CardDescription>{new Date(tx.at).toLocaleString()} · rows written in this call: {tx.changes.length}</CardDescription>
          <CardTitle className="flex flex-wrap items-center gap-2">
            {tx.guess ? (
              <button onClick={() => setFocus({ kind: "entry", name: tx.guess!.name })} className="font-mono underline decoration-dotted underline-offset-4">{tx.guess.name}</button>
            ) : <span>unrecognised call</span>}
            {tx.guess && <Badge variant="outline">{tx.guess.kind} · called by {CALLER[tx.guess.caller]}</Badge>}
          </CardTitle>
          {tx.guess && <p className="text-sm">{ENTRIES[tx.guess.name] ?? ""}</p>}
          {tx.alternatives.length > 0 && (
            <p className="text-xs text-muted-foreground">Could also be: {tx.alternatives.map(a => (
              <button key={a.name} onClick={() => setFocus({ kind: "entry", name: a.name })} className="mr-2 font-mono underline decoration-dotted">{a.name}</button>))}
              (the guess matches the tables this call wrote against what each reducer can write)</p>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          {Object.entries(byTable).map(([table, changes]) => (
            <div key={table}>
              <button onClick={() => setFocus({ kind: "table", name: table })} className="font-mono text-sm text-sky-700 underline decoration-dotted dark:text-sky-300">{table}</button>
              <span className="ml-2 text-xs text-muted-foreground">{TABLES[table]?.split(".")[0]}</span>
              <ul className="mt-1 space-y-0.5 pl-4 text-sm">{changes.map((c, i) => (
                <li key={i}><Badge variant={c.op === "insert" ? "default" : "secondary"} className="mr-2">{c.op === "insert" ? "new row" : "changed"}</Badge>{describe(c, name)}</li>
              ))}</ul>
            </div>
          ))}
          <div className="flex flex-wrap gap-2 border-t pt-2">
            <span className="text-xs text-muted-foreground">SpacetimeDB ideas at work here:</span>
            {tx.concepts.map(c => <button key={c} onClick={() => setFocus({ kind: "concept", name: c })}><Badge variant="outline">{CONCEPTS[c].title}</Badge></button>)}
          </div>
        </CardContent>
      </Card>
      <p className="text-xs text-muted-foreground">
        How this works: SpacetimeDB keeps current rows, not a history log. Every row carries the timestamp of the call that created it and the one that last changed it, and all writes in one call share a stamp, so calls can be rebuilt from the rows. A row changed again later only shows its latest change.
      </p>
    </div>
  );
}

export function Explain({ focus, map, setFocus }: { focus: Focus; map: ModuleMap | null; setFocus: (f: Focus) => void }) {
  if (!focus || !map) return (
    <div className="space-y-2 text-sm">
      <p className="font-medium">Click any underlined name to see what it is.</p>
      <p className="text-muted-foreground">Start with the basics:</p>
      {Object.entries(CONCEPTS).map(([k, c]) => <button key={k} onClick={() => setFocus({ kind: "concept", name: k })} className="mr-1"><Badge variant="outline">{c.title}</Badge></button>)}
    </div>
  );
  const link = (kind: "table" | "entry", n: string) => (
    <button key={n} onClick={() => setFocus({ kind, name: n })} className="mr-2 font-mono text-xs underline decoration-dotted">{n}</button>
  );
  if (focus.kind === "concept") {
    const c = CONCEPTS[focus.name];
    return <div className="space-y-2 text-sm"><h3 className="font-semibold">{c.title}</h3><p>{c.text}</p><Back setFocus={setFocus} /></div>;
  }
  if (focus.kind === "table") {
    const writers = map.entries.filter(e => e.kind !== "view" && (e.tables[focus.name] ?? []).some(op => op !== "read"));
    const views = map.entries.filter(e => e.kind === "view" && e.tables[focus.name]);
    const schedule = map.tables[focus.name]?.schedule;
    return (
      <div className="space-y-3 text-sm">
        <h3 className="font-mono font-semibold">{focus.name} <Badge variant="outline">table · private</Badge></h3>
        <p>{TABLES[focus.name] ?? "No description yet."}</p>
        {schedule && <p className="rounded bg-muted p-2">Schedule table: when one of its rows comes due, SpacetimeDB calls {link("entry", schedule)}.</p>}
        <div><div className="font-medium">Written by ({writers.length})</div>{writers.map(e => link("entry", e.name))}</div>
        <div><div className="font-medium">Readable through views</div>{views.length ? views.map(v => (
          <div key={v.name} className="text-xs">{link("entry", v.name)} <span className="text-muted-foreground">{audienceOf(v.name)}</span></div>
        )) : <span className="text-xs text-muted-foreground">No view: no browser can ever read it. Only reducers and the database owner.</span>}</div>
        <Back setFocus={setFocus} />
      </div>
    );
  }
  const e = map.entries.find(x => x.name === focus.name);
  if (!e) return <div className="text-sm">Unknown: {focus.name} <Back setFocus={setFocus} /></div>;
  const writes = Object.entries(e.tables).filter(([, ops]) => ops.some(op => op !== "read"));
  const reads = Object.entries(e.tables).filter(([, ops]) => ops.every(op => op === "read"));
  return (
    <div className="space-y-3 text-sm">
      <h3 className="font-mono font-semibold">{e.name} <Badge variant="outline">{e.kind}</Badge></h3>
      <p>{e.kind === "view" ? audienceOf(e.name) : ENTRIES[e.name] ?? ""}</p>
      {e.kind !== "view" && <p className="text-xs text-muted-foreground">Called by {CALLER[e.caller]} · defined in spacetimedb/src/{e.file}</p>}
      {writes.length > 0 && <div><div className="font-medium">Writes, all in one transaction</div>{writes.map(([t, ops]) => (
        <div key={t} className="text-xs">{link("table", t)} <span className="text-muted-foreground">{ops.filter(o => o !== "read").join(", ")}</span></div>))}</div>}
      {reads.length > 0 && <div><div className="font-medium">{e.kind === "view" ? "Reads" : "Checks / reads"}</div>{reads.map(([t]) => link("table", t))}</div>}
      {e.helpers.length > 0 && <div className="text-xs text-muted-foreground">Through helpers: {e.helpers.join(", ")}</div>}
      <details><summary className="cursor-pointer text-xs text-muted-foreground">Show the code</summary>
        <pre className="mt-1 max-h-80 overflow-auto rounded bg-muted p-2 text-[11px]">{e.source}</pre></details>
      <Back setFocus={setFocus} />
    </div>
  );
}

function Back({ setFocus }: { setFocus: (f: Focus) => void }) {
  return <button onClick={() => setFocus(null)} className="text-xs text-muted-foreground underline">back to basics</button>;
}

export function MapView({ map, setFocus }: { map: ModuleMap; setFocus: (f: Focus) => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const entries = map.entries.filter(e => e.kind !== "view");
  const views = map.entries.filter(e => e.kind === "view");
  const tables = Object.keys(map.tables).sort();
  const sel = map.entries.find(e => e.name === selected);
  const lit = (t: string) => (sel ? Boolean(sel.tables[t]) : false);
  const groups: [string, Entry[]][] = (["person", "worker", "scheduler", "owner", "system"] as const).map(c => [CALLER[c], entries.filter(e => e.caller === c)]);
  return (
    <div className="mt-2 grid grid-cols-[1fr_1fr_1fr] gap-4 text-xs">
      <div>
        <div className="mb-1 font-medium">Reducers &amp; procedures (writes) · grouped by who calls them</div>
        {groups.map(([who, list]) => (
          <div key={who} className="mb-2"><div className="text-muted-foreground">{who}</div>
            {list.map(e => (
              <button key={e.name} onMouseEnter={() => setSelected(e.name)} onClick={() => setFocus({ kind: "entry", name: e.name })}
                className={`mr-1 mb-1 rounded border px-1.5 py-0.5 font-mono ${selected === e.name ? "bg-sky-100 dark:bg-sky-900" : ""}`}>{e.name}</button>))}
          </div>))}
      </div>
      <div>
        <div className="mb-1 font-medium">Tables (all private) {sel && <span className="text-muted-foreground">· touched by {sel.name}</span>}</div>
        {tables.map(t => (
          <button key={t} onMouseEnter={() => setSelected(null)} onClick={() => setFocus({ kind: "table", name: t })}
            className={`mr-1 mb-1 rounded border px-1.5 py-0.5 font-mono ${lit(t) ? ((sel!.tables[t] ?? []).some(o => o !== "read") ? "border-sky-500 bg-sky-100 dark:bg-sky-900" : "border-dashed bg-muted") : ""}`}>
            {t}{map.tables[t]?.schedule ? " ⏱" : ""}</button>))}
        <p className="mt-2 text-muted-foreground">Hover a reducer: solid = it writes the table, dashed = it only reads it. ⏱ = schedule table.</p>
      </div>
      <div>
        <div className="mb-1 font-medium">Views (what browsers can read)</div>
        {views.map(v => (
          <button key={v.name} onMouseEnter={() => setSelected(v.name)} onClick={() => setFocus({ kind: "entry", name: v.name })}
            className={`mr-1 mb-1 rounded border px-1.5 py-0.5 font-mono ${selected === v.name ? "bg-emerald-100 dark:bg-emerald-900" : ""}`}>{v.name}</button>))}
        <p className="mt-2 text-muted-foreground">my_… views return only the caller&apos;s rows; room_… only rooms they belong to.</p>
      </div>
    </div>
  );
}
