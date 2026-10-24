"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Explain, type Focus, Learn, MapView, useModuleMap } from "@/components/learn";
import { Sandbox } from "@/components/sandbox";
import { Tour } from "@/components/tour";

// Rows come from whichever table the schema lists at runtime, so values are untyped here.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
type Snapshot = { roomId: number; fetchedAt: number; tables: Record<string, Row[]>; columns: Record<string, string[]>; unlinked: string[] };
type RoomSummary = { id: number; goal: string; status: string; members: string[] };

// Who can read a row, mirroring the module's views (views.ts): everyone in the
// room, one person, or only the worker and the database owner.
type Audience = { kind: "room" } | { kind: "person"; who: string } | { kind: "worker" };

const ROOM_TABLES = new Set(["room", "membership", "agent", "trip_brief", "candidate", "proposal", "milestone", "conversation_thread"]);
const PERSONAL_TABLES = new Set(["private_message", "constraint_patch", "participant_constraint", "intake", "question",
  "participation_preferences", "profile", "participant_impact", "consent"]);

function parse(json: unknown) {
  if (typeof json !== "string") return json;
  try { return JSON.parse(json); } catch { return json; }
}

function when(ms?: number) {
  return ms ? new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
}

function money(cents?: number | null) {
  return cents == null ? "?" : `$${(cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function Inspector() {
  const [rooms, setRooms] = useState<RoomSummary[]>([]);
  const [roomId, setRoomId] = useState<number | null>(null);
  const [data, setData] = useState<Snapshot | null>(null);
  const [lens, setLens] = useState<string>("owner");
  const [rawTable, setRawTable] = useState("room");
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"tour" | "inspect" | "learn" | "sandbox">("tour");
  const [focus, setFocus] = useState<Focus>(null);
  const map = useModuleMap();
  const busy = useRef(false);

  useEffect(() => {
    const load = () => fetch("/api/rooms").then(r => r.json()).then((list: RoomSummary[]) => {
      setRooms(list);
      setRoomId(current => current ?? list[0]?.id ?? null);
    }).catch(e => setError(String(e)));
    load();
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (roomId == null) return;
    const load = async () => {
      if (busy.current) return;
      busy.current = true;
      try {
        const res = await fetch(`/api/rooms/${roomId}`);
        const body = await res.json();
        if (body.error) throw new Error(body.error);
        setData(body);
        setError(null);
      } catch (e) {
        setError(String(e));
      } finally {
        busy.current = false;
      }
    };
    load();
    const t = setInterval(load, 2_000);
    return () => clearInterval(t);
  }, [roomId]);

  const model = useMemo(() => (data ? build(data) : null), [data]);

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <header className="flex items-center justify-between border-b px-4 py-2">
        <div className="flex items-center gap-3">
          <h1 className="text-base font-semibold">SpacetimeDB inspector</h1>
          <div className="flex rounded-md border p-0.5 text-sm">
            {([["tour", "Tour"], ["learn", "Replay"], ["sandbox", "Sandbox"], ["inspect", "Raw data"]] as const).map(([m, label]) => (
              <button key={m} onClick={() => setMode(m)} className={`rounded px-2.5 py-0.5 ${mode === m ? "bg-foreground text-background" : "text-muted-foreground"}`}>{label}</button>
            ))}
          </div>
          {mode !== "tour" && <span className="text-xs text-muted-foreground">tandem-dev · read as the database owner · refreshes every few seconds</span>}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className={`h-2 w-2 rounded-full ${error ? "bg-red-500" : "bg-emerald-500"}`} />
          {error ? <span className="text-red-600">{error.slice(0, 80)}</span> : data ? `updated ${when(data.fetchedAt)}` : "loading…"}
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        {(mode === "learn" || mode === "inspect") && <aside className="w-64 shrink-0 overflow-y-auto border-r">
          {rooms.map(r => (
            <button key={r.id} onClick={() => { setRoomId(r.id); setData(null); setLens("owner"); }}
              className={`block w-full border-b px-3 py-2 text-left text-sm hover:bg-muted ${r.id === roomId ? "bg-muted" : ""}`}>
              <div className="flex justify-between"><span className="font-medium">#{r.id} {r.goal}</span></div>
              <div className="text-xs text-muted-foreground">{r.status} · {r.members.join(", ")}</div>
            </button>
          ))}
        </aside>}
        {mode === "tour" && (
          <main className="min-w-0 flex-1 overflow-y-auto p-4">
            <Tour data={model?.data ?? null} name={model?.name ?? (id => id ?? "?")} roomId={roomId} rooms={rooms}
              onRoom={id => { setRoomId(id); setData(null); }} setFocus={setFocus} />
          </main>
        )}
        {mode === "sandbox" && <main className="min-w-0 flex-1 overflow-y-auto p-4"><Sandbox setFocus={setFocus} /></main>}
        {mode === "learn" && (
          <main className="min-w-0 flex-1 overflow-y-auto p-4">
            {!model || !map ? <p className="text-sm text-muted-foreground">Loading…</p> : (
              <Tabs defaultValue="replay">
                <TabsList><TabsTrigger value="replay">Replay this room, call by call</TabsTrigger><TabsTrigger value="map">Map of the module</TabsTrigger></TabsList>
                <TabsContent value="replay"><Learn data={model.data} map={map} name={model.name} setFocus={setFocus} /></TabsContent>
                <TabsContent value="map"><MapView map={map} setFocus={setFocus} /></TabsContent>
              </Tabs>
            )}
          </main>
        )}
        {(mode === "learn" || mode === "sandbox") && (
          <aside className="w-96 shrink-0 overflow-y-auto border-l p-4"><Explain focus={focus} map={map} setFocus={setFocus} /></aside>
        )}
        {mode === "tour" && focus && (
          <div className="fixed inset-0 z-20 flex justify-end bg-black/20" onClick={() => setFocus(null)}>
            <aside className="h-full w-[28rem] overflow-y-auto bg-background p-5 shadow-xl" onClick={e => e.stopPropagation()}>
              <button onClick={() => setFocus(null)} className="mb-3 text-sm text-muted-foreground hover:text-foreground">✕ Close</button>
              <Explain focus={focus} map={map} setFocus={setFocus} />
            </aside>
          </div>
        )}
        {mode === "inspect" && <main className="min-w-0 flex-1 overflow-y-auto p-4">
          {!model ? <p className="text-sm text-muted-foreground">Loading room…</p> : (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-3">
                <span className="text-sm font-medium">Looking at:</span>
                <select value={lens} onChange={e => setLens(e.target.value)} className="rounded-md border bg-background px-2 py-1 text-sm">
                  <option value="owner">Everything (database owner)</option>
                  {model.people.map(p => <option key={p.identity} value={p.identity}>What {p.name}&apos;s browser can read</option>)}
                </select>
                {lens !== "owner" && <span className="text-xs text-muted-foreground">Hidden items are private tables or other people&apos;s rows, per the module&apos;s views.</span>}
              </div>
              <Tabs defaultValue="story">
                <TabsList>
                  <TabsTrigger value="story">Story</TabsTrigger>
                  <TabsTrigger value="people">People</TabsTrigger>
                  <TabsTrigger value="work">Jobs</TabsTrigger>
                  <TabsTrigger value="agents">Agent chat</TabsTrigger>
                  <TabsTrigger value="evidence">Search results</TabsTrigger>
                  <TabsTrigger value="outcome">Plans &amp; events</TabsTrigger>
                  <TabsTrigger value="raw">Raw tables</TabsTrigger>
                </TabsList>
                <TabsContent value="story"><Story model={model} lens={lens} /></TabsContent>
                <TabsContent value="people"><People model={model} lens={lens} /></TabsContent>
                <TabsContent value="work"><Work model={model} lens={lens} /></TabsContent>
                <TabsContent value="agents"><AgentChat model={model} lens={lens} /></TabsContent>
                <TabsContent value="evidence"><Evidence model={model} /></TabsContent>
                <TabsContent value="outcome"><Outcome model={model} lens={lens} /></TabsContent>
                <TabsContent value="raw"><Raw model={model} table={rawTable} setTable={setRawTable} lens={lens} /></TabsContent>
              </Tabs>
            </>
          )}
        </main>}
      </div>
    </div>
  );
}

// ---------- model ----------

type Item = { at: number; table: string; who?: string; text: string; audience: Audience; row: Row };

function build(data: Snapshot) {
  const t = data.tables;
  const people = (t.membership ?? []).map(m => ({ id: m.id as number, identity: m.member as string, name: m.display_name as string, role: m.role as string }));
  const byIdentity = new Map(people.map(p => [p.identity, p.name]));
  const byMember = new Map(people.map(p => [p.id, p.name]));
  const agentName = new Map<number, string>((t.agent ?? []).map(a => [a.id, a.kind === "MEDIATOR" ? "mediator" : `${byMember.get(a.membership_id) ?? "?"}'s agent`]));
  const name = (identity?: string | null) => (identity ? byIdentity.get(identity) ?? identity.slice(0, 10) : "?");
  const person = (identity?: string | null): Audience => ({ kind: "person", who: name(identity) });
  const items: Item[] = [];
  const add = (table: string, at: unknown, text: string, audience: Audience, row: Row, who?: string) =>
    items.push({ at: Number(at) || 0, table, text, audience, row, who });

  for (const b of t.trip_brief ?? []) {
    const brief = parse(b.brief_json) as Row;
    add("trip_brief", b.updated_at, `Trip brief ${b.state}: ${brief?.destination ?? "—"} ${brief?.start_date ?? ""}→${brief?.end_date ?? ""} (${(brief?.components ?? []).join(", ")})`, { kind: "room" }, b);
  }
  for (const m of t.private_message ?? [])
    add("private_message", m.created_at, `${m.sender === "AGENT" ? `Agent → ${name(m.owner)}` : `${name(m.owner)} → agent`}: ${m.body}`, person(m.owner), m, name(m.owner));
  for (const p of t.constraint_patch ?? []) {
    const patch = parse(p.patch_json) as Row;
    add("constraint_patch", p.created_at, `Agent proposed for ${name(p.owner)}: ${patch.key} = ${JSON.stringify(patch.value)}${patch.source_quote ? ` (“${patch.source_quote}”)` : ""} · ${p.state}`, person(p.owner), p, name(p.owner));
  }
  for (const c of t.participant_constraint ?? [])
    add("participant_constraint", c.confirmed_at, `${name(c.owner)} confirmed ${c.key} = ${c.value_json} (${c.hardness})${c.private_rationale ? ` · private reason: “${c.private_rationale}”` : ""}`, person(c.owner), c, name(c.owner));
  for (const i of t.intake ?? [])
    add("intake", i.updated_at, `${name(i.owner)}'s onboarding: ${i.state}, ${i.question_count} questions · ${Object.entries(parse(i.coverage_json) as Row).map(([k, v]) => `${k} ${String(v).toLowerCase()}`).join(", ")}`, person(i.owner), i, name(i.owner));
  for (const q of t.question ?? [])
    add("question", q.created_at, `Asked ${name(q.owner)} (${q.kind}): ${q.prompt}${q.answer_json ? ` → answered ${q.answer_json}` : ` · ${q.state}`}`, person(q.owner), q, name(q.owner));
  for (const j of t.job ?? [])
    add("job", j.created_at, `Job #${j.id} ${j.kind} (${j.reason}) → ${j.status}${j.outcome ? `: ${j.outcome}` : ""} · attempt ${j.attempt}`, { kind: "worker" }, j);
  for (const m of t.agent_message ?? [])
    add("agent_message", m.created_at, `${agentName.get(m.sender_agent_id) ?? m.sender_agent_id} → ${agentName.get(m.recipient_agent_id) ?? m.recipient_agent_id} [${m.type}]: ${m.utterance}`,
      m.effective_audience === "ROOM" ? { kind: "room" } : m.effective_audience === "OWNER_PRIVATE" ? person(m.audience_owner) : { kind: "worker" }, m);
  for (const e of t.decision_event ?? []) {
    const payload = parse(e.safe_payload_json) as Row;
    add("decision_event", e.created_at, `Event ${e.type}${payload?.text ? `: ${payload.text}` : payload?.message ? `: ${payload.message}` : ""}`, e.audience === "ROOM" ? { kind: "room" } : person(e.owner), e);
  }
  for (const p of t.proposal ?? []) {
    const summary = parse(p.shared_summary_json) as Row;
    add("proposal", p.created_at, `Plan v${p.version} published (${p.state}): ${summary?.text ?? ""}`, { kind: "room" }, p);
  }
  for (const c of t.consent ?? []) add("consent", c.updated_at ?? c.created_at, `${name(c.member)} → ${c.response}`, person(c.member), c, name(c.member));
  // Search results arrive in batches; one line per batch instead of hundreds.
  const batches = new Map<string, Row[]>();
  for (const c of t.candidate ?? []) {
    const key = `${c.input_revision}:${Math.round(Number(c.recorded_at) / 2000)}`;
    batches.set(key, [...(batches.get(key) ?? []), c]);
  }
  for (const rows of batches.values()) {
    const counts = rows.reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.category]: (acc[r.category] ?? 0) + 1 }), {});
    add("candidate", rows[0].recorded_at, `Search results recorded: ${Object.entries(counts).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(", ")}`, { kind: "room" }, { count: rows.length, sample: rows[0] });
  }
  items.sort((a, b) => a.at - b.at);
  return { data, people, name, agentName, items, tables: t };
}

type Model = ReturnType<typeof build>;

function canSee(audience: Audience, lens: string, model: Model) {
  if (lens === "owner") return true;
  if (audience.kind === "room") return true;
  if (audience.kind === "worker") return false;
  return audience.who === model.name(lens);
}

function tableVisible(table: string, row: Row, lens: string, model: Model) {
  if (lens === "owner") return true;
  if (ROOM_TABLES.has(table)) return true;
  if (PERSONAL_TABLES.has(table)) return (row.owner ?? row.member) === lens;
  if (table === "agent_message") return row.effective_audience === "ROOM" || row.audience_owner === lens;
  if (table === "decision_event") return row.audience === "ROOM" || row.owner === lens;
  return false;
}

function AudienceBadge({ audience }: { audience: Audience }) {
  if (audience.kind === "room") return <Badge variant="secondary">everyone in the room</Badge>;
  if (audience.kind === "worker") return <Badge variant="outline">worker &amp; owner only</Badge>;
  return <Badge variant="default">only {audience.who}</Badge>;
}

// ---------- tabs ----------

function Story({ model, lens }: { model: Model; lens: string }) {
  const visible = model.items.filter(i => canSee(i.audience, lens, model));
  return (
    <div className="mt-2 space-y-1">
      <p className="text-xs text-muted-foreground">{visible.length} of {model.items.length} entries, oldest first. Each line is a row in the table shown on the left.</p>
      {visible.map((i, n) => (
        <details key={n} className="group rounded-md border px-3 py-1.5 text-sm open:bg-muted/40">
          <summary className="flex cursor-pointer list-none items-start gap-2">
            <span className="w-24 shrink-0 font-mono text-xs text-muted-foreground">{when(i.at)}</span>
            <span className="w-40 shrink-0 font-mono text-xs text-sky-700 dark:text-sky-300">{i.table}</span>
            <span className="min-w-0 flex-1 whitespace-pre-wrap">{i.text}</span>
            <AudienceBadge audience={i.audience} />
          </summary>
          <pre className="mt-2 overflow-x-auto rounded bg-muted p-2 text-xs">{JSON.stringify(i.row, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

function People({ model, lens }: { model: Model; lens: string }) {
  const t = model.tables;
  return (
    <div className="mt-2 grid gap-3 lg:grid-cols-2">
      {model.people.filter(p => lens === "owner" || p.identity === lens).map(p => {
        const prefs = (t.participation_preferences ?? []).find(x => x.owner === p.identity);
        const intake = (t.intake ?? []).find(x => x.owner === p.identity);
        const limits = (t.participant_constraint ?? []).filter(x => x.owner === p.identity);
        const chat = (t.private_message ?? []).filter(x => x.owner === p.identity).sort((a, b) => a.id - b.id);
        return (
          <Card key={p.identity}>
            <CardHeader>
              <CardTitle>{p.name} <span className="text-xs font-normal text-muted-foreground">{p.role.toLowerCase()} · member #{p.id}</span></CardTitle>
              <CardDescription className="font-mono text-[11px]">{p.identity}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div>
                <div className="font-medium">How their agent acts</div>
                {prefs ? <div className="text-muted-foreground">{prefs.confirmed ? "confirmed" : "NOT confirmed"} · speak up {prefs.speak_up_for_me ? "on" : "off"} · ask me first {prefs.ask_me_first ? "on" : "off"} · give me time {prefs.give_me_time ? "on" : "off"}</div> : "—"}
              </div>
              <div>
                <div className="font-medium">Onboarding</div>
                <div className="text-muted-foreground">{intake ? `${intake.state}, ${intake.question_count} questions asked` : "no onboarding row (room built without a brief)"}</div>
              </div>
              <div>
                <div className="font-medium">Confirmed private limits <span className="text-xs font-normal text-muted-foreground">(participant_constraint)</span></div>
                {limits.length === 0 ? <div className="text-muted-foreground">none</div> : (
                  <ul className="list-disc pl-5">{limits.map(l => <li key={l.id}><span className="font-mono">{l.key}</span> = {l.value_json} <span className="text-muted-foreground">{l.hardness}{l.private_rationale ? ` · reason: “${l.private_rationale}”` : ""}</span></li>)}</ul>
                )}
              </div>
              <div>
                <div className="font-medium">Private chat with their agent <span className="text-xs font-normal text-muted-foreground">(private_message)</span></div>
                <div className="mt-1 max-h-72 space-y-1 overflow-y-auto rounded border p-2">
                  {chat.map(m => (
                    <div key={m.id} className={`rounded px-2 py-1 ${m.sender === "AGENT" ? "bg-muted" : "ml-8 bg-sky-50 dark:bg-sky-950"}`}>
                      <span className="text-xs text-muted-foreground">{m.sender === "AGENT" ? "agent" : p.name} · {when(m.created_at)}</span>
                      <div className="whitespace-pre-wrap">{m.body}</div>
                    </div>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

function Work({ model, lens }: { model: Model; lens: string }) {
  if (lens !== "owner") return <p className="mt-2 text-sm text-muted-foreground">Jobs, leases and receipts are worker-only: no traveller&apos;s browser can read them.</p>;
  const jobs = [...(model.tables.job ?? [])].sort((a, b) => b.id - a.id);
  const subjects = new Map((model.tables.job_subject ?? []).map(s => [s.job_id, s]));
  const ops = model.tables.operation_record ?? [];
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-sm">
        <thead><tr className="border-b text-left text-xs text-muted-foreground">
          <th className="p-1">job</th><th>kind</th><th>for</th><th>reason</th><th>status</th><th>outcome</th><th>attempt</th><th>revisions (input/members/permissions)</th><th>ops</th><th>created</th>
        </tr></thead>
        <tbody>{jobs.map(j => {
          const s = subjects.get(j.id);
          return (
            <tr key={j.id} className="border-b align-top">
              <td className="p-1 font-mono">#{j.id}</td><td>{j.kind}</td>
              <td>{s ? `${model.people.find(p => p.id === s.member_id)?.name ?? s.member_id} (${s.purpose})` : ""}</td>
              <td>{j.reason}</td><td>{j.status}</td><td className="font-mono text-xs">{j.outcome ?? ""}</td><td>{j.attempt}</td>
              <td className="font-mono text-xs">{j.input_revision}/{j.membership_revision}/{j.permission_revision}</td>
              <td>{ops.filter(o => o.job_id === j.id).length}</td><td className="text-xs">{when(j.created_at)}</td>
            </tr>
          );
        })}</tbody>
      </table>
    </div>
  );
}

function AgentChat({ model, lens }: { model: Model; lens: string }) {
  const messages = (model.tables.agent_message ?? []).filter(m => tableVisible("agent_message", m, lens, model)).sort((a, b) => a.id - b.id);
  if (!messages.length) return <p className="mt-2 text-sm text-muted-foreground">No agent messages in this room yet (agents talk once a plan exists).</p>;
  return (
    <div className="mt-2 space-y-1 text-sm">
      {messages.map(m => (
        <div key={m.id} className="rounded border px-3 py-1.5">
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>{model.agentName.get(m.sender_agent_id)} → {model.agentName.get(m.recipient_agent_id)} · {m.type} · thread {m.thread_id} · round {m.round_id}</span>
            <span>{m.effective_audience} · {m.sanitization_status}</span>
          </div>
          <div>{m.utterance}</div>
          <div className="font-mono text-[11px] text-muted-foreground">cites {(m.claim_refs ?? []).join(", ")}</div>
        </div>
      ))}
    </div>
  );
}

function Evidence({ model }: { model: Model }) {
  const rows: Row[] = (model.tables.candidate ?? []).map(c => ({ ...c, facts: parse(c.facts_json) as Row }));
  const groups = new Map<string, Row[]>();
  for (const r of rows) {
    const owner = r.facts.owner_member_id ? model.people.find(p => p.id === r.facts.owner_member_id)?.name : null;
    const key = `${r.category}${owner ? ` · ${owner}` : ""}`;
    groups.set(key, [...(groups.get(key) ?? []), r]);
  }
  return (
    <div className="mt-2 grid gap-3 lg:grid-cols-2">
      {[...groups.entries()].map(([key, list]) => {
        const sorted = [...list].sort((a, b) => (a.facts.total_cents ?? 1e12) - (b.facts.total_cents ?? 1e12));
        const complete = list.filter(r => String(r.facts.cost_basis ?? "").startsWith("COMPLETE")).length;
        return (
          <Card key={key}>
            <CardHeader><CardTitle className="text-sm">{key}</CardTitle>
              <CardDescription>{list.length} found · {complete} with a complete price · {list[0].mode}</CardDescription></CardHeader>
            <CardContent>
              <table className="w-full text-xs"><tbody>{sorted.slice(0, 6).map(r => (
                <tr key={r.id} className="border-b">
                  <td className="py-0.5 pr-2 font-mono">{money(r.facts.total_cents)}</td>
                  <td className="pr-2">{r.facts.title ?? `${r.facts.arrival_local ? `lands ${r.facts.arrival_local}` : ""}${r.facts.stops != null ? ` · ${r.facts.stops} stops` : ""}${r.facts.baggage_included === false ? " · no bag" : ""}`}</td>
                  <td className="text-muted-foreground">{r.facts.cost_basis}</td>
                </tr>))}</tbody></table>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

function Outcome({ model, lens }: { model: Model; lens: string }) {
  const t = model.tables;
  const proposals = t.proposal ?? [];
  const events = (t.decision_event ?? []).filter(e => tableVisible("decision_event", e, lens, model)).sort((a, b) => b.id - a.id);
  const impacts = (t.participant_impact ?? []).filter(i => tableVisible("participant_impact", i, lens, model));
  return (
    <div className="mt-2 space-y-4 text-sm">
      <section>
        <h3 className="mb-1 font-medium">Plans <span className="text-xs font-normal text-muted-foreground">(proposal, shared with the room)</span></h3>
        {proposals.length === 0 ? <p className="text-muted-foreground">None published.</p> : proposals.map(p => (
          <Card key={p.id} className="mb-2"><CardContent className="space-y-1 pt-4">
            <div className="text-xs text-muted-foreground">#{p.id} v{p.version} · {p.state} · terms digest {String(p.terms_digest).slice(0, 12)}…</div>
            <div>{(parse(p.shared_summary_json) as Row)?.text}</div>
          </CardContent></Card>
        ))}
      </section>
      <section>
        <h3 className="mb-1 font-medium">Private impacts <span className="text-xs font-normal text-muted-foreground">(participant_impact, each visible only to its person)</span></h3>
        {impacts.length === 0 ? <p className="text-muted-foreground">None.</p> : impacts.map(i => (
          <div key={i.id}>{model.name(i.owner)}: {(parse(i.impact_json) as Row)?.text ?? i.impact_json}</div>
        ))}
      </section>
      <section>
        <h3 className="mb-1 font-medium">Events <span className="text-xs font-normal text-muted-foreground">(decision_event)</span></h3>
        {events.map(e => (
          <div key={e.id} className="flex gap-2 border-b py-0.5">
            <span className="w-16 shrink-0 font-mono text-xs text-muted-foreground">{when(e.created_at)}</span>
            <span className="w-56 shrink-0 font-mono text-xs">{e.type}</span>
            <span className="flex-1">{(parse(e.safe_payload_json) as Row)?.text ?? (parse(e.safe_payload_json) as Row)?.message ?? ""}</span>
            <AudienceBadge audience={e.audience === "ROOM" ? { kind: "room" } : { kind: "person", who: model.name(e.owner) }} />
          </div>
        ))}
      </section>
    </div>
  );
}

function Raw({ model, table, setTable, lens }: { model: Model; table: string; setTable: (t: string) => void; lens: string }) {
  const names = Object.keys(model.data.columns).sort();
  const rows = (model.tables[table] ?? []).filter(r => tableVisible(table, r, lens, model));
  const columns = model.data.columns[table] ?? [];
  const unlinked = model.data.unlinked.includes(table) && table !== "room";
  return (
    <div className="mt-2 space-y-2">
      <div className="flex items-center gap-2 text-sm">
        <select value={table} onChange={e => setTable(e.target.value)} className="rounded-md border bg-background px-2 py-1 font-mono text-sm">
          {names.map(n => <option key={n} value={n}>{n} ({(model.tables[n] ?? []).length})</option>)}
        </select>
        <span className="text-xs text-muted-foreground">{unlinked ? "Not tied to a room (global table); not shown here." : `${rows.length} rows in this room · all 35 tables are private; browsers only see them through views`}</span>
      </div>
      <div className="overflow-x-auto rounded border">
        <table className="text-xs">
          <thead><tr className="border-b bg-muted text-left">{columns.map(c => <th key={c} className="whitespace-nowrap px-2 py-1 font-mono">{c}</th>)}</tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i} className="border-b align-top">{columns.map(c => (
              <td key={c} className="max-w-xs truncate px-2 py-1 font-mono" title={typeof r[c] === "string" ? r[c] : JSON.stringify(r[c])}>
                {c.endsWith("_at") && typeof r[c] === "number" ? when(r[c]) : typeof r[c] === "string" ? r[c] : JSON.stringify(r[c])}
              </td>))}</tr>))}</tbody>
        </table>
      </div>
    </div>
  );
}
