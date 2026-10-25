import { isTableName, sql, tables } from "@/lib/stdb";

// Raw rows of any table (filtered to a room when the table has room_id).
export async function GET(request: Request, { params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const known = await tables();
  if (!isTableName(name, known)) return Response.json({ error: "unknown table" }, { status: 404 });
  const room = new URL(request.url).searchParams.get("room");
  const hasRoom = known.find(t => t.name === name)!.columns.includes("room_id");
  const where = room && /^\d+$/.test(room) && hasRoom ? ` WHERE room_id = ${room}` : "";
  return Response.json(await sql(`SELECT * FROM ${name}${where} LIMIT 500`));
}
