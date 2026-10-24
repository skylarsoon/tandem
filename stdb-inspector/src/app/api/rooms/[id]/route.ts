import { type Row, type TableInfo, sql, tables } from "@/lib/stdb";

// Everything the database holds about one room, from every table, as the owner
// sees it. Each table is filtered by SpacetimeDB itself through whichever link
// column it has: the room, a job or proposal of the room, a membership, or a person.
const LINKS = ["room_id", "job_id", "proposal_id", "membership_id", "owner", "member"] as const;

function anyOf(column: string, values: unknown[], quote = false) {
  if (values.length === 0) return null;
  return values.map(v => `${column} = ${quote ? v : Number(v)}`).join(" OR ");
}

// Stored model/tool results can be megabytes; the snapshot skips them (the raw
// table endpoint still returns them).
const HEAVY = (column: string) => column === "result_json";

type Snapshot = Awaited<ReturnType<typeof snapshot>>;
const cache = new Map<number, { data?: Snapshot; error?: string; refreshing?: Promise<void>; at: number }>();

// Answer from the latest snapshot at once and refresh in the background, so the
// page stays responsive while Maincloud answers each query in 0.5-5 s.
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) return Response.json({ error: "bad room id" }, { status: 400 });
  const roomId = Number(id);
  const entry = cache.get(roomId) ?? { at: 0 };
  cache.set(roomId, entry);
  if (!entry.refreshing && Date.now() - entry.at > 1500) {
    entry.refreshing = snapshot(roomId)
      .then(data => { entry.data = data; entry.error = undefined; })
      .catch(e => { entry.error = String(e); })
      .finally(() => { entry.at = Date.now(); entry.refreshing = undefined; });
  }
  if (!entry.data) await entry.refreshing;
  if (!entry.data) return Response.json({ error: entry.error ?? "no data" }, { status: 502 });
  return Response.json({ ...entry.data, error: entry.error });
}

async function snapshot(roomId: number) {
  const known = await tables();
  const [room, members, jobs, proposals] = await Promise.all([
    sql(`SELECT * FROM room WHERE id = ${roomId}`),
    sql(`SELECT * FROM membership WHERE room_id = ${roomId}`),
    sql(`SELECT id FROM job WHERE room_id = ${roomId}`),
    sql(`SELECT id FROM proposal WHERE room_id = ${roomId}`),
  ]);
  const identities = [...new Set(members.map(m => String(m.member)))].filter(i => /^0x[0-9a-f]+$/.test(i));
  const where = (t: TableInfo) => {
    const link = LINKS.find(c => t.columns.includes(c));
    if (link === "room_id") return `room_id = ${roomId}`;
    if (link === "job_id") return anyOf("job_id", jobs.map(j => j.id));
    if (link === "proposal_id") return anyOf("proposal_id", proposals.map(p => p.id));
    if (link === "membership_id") return anyOf("membership_id", members.map(m => m.id));
    if (link) return anyOf(link, identities, true);
    return undefined;
  };
  const linked = known.filter(t => where(t) !== undefined && t.name !== "room");
  const rows: Record<string, Row[]> = { room };
  await Promise.all(linked.map(async t => {
    const condition = where(t);
    const columns = t.columns.some(HEAVY) ? t.columns.filter(c => !HEAVY(c)).join(", ") : "*";
    rows[t.name] = condition ? await sql(`SELECT ${columns} FROM ${t.name} WHERE ${condition}`) : [];
  }));
  return {
    roomId,
    fetchedAt: Date.now(),
    tables: rows,
    columns: Object.fromEntries(known.map(t => [t.name, t.columns])),
    unlinked: known.filter(t => !linked.includes(t) && t.name !== "room").map(t => t.name),
  };
}
