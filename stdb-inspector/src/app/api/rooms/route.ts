import { sql } from "@/lib/stdb";

// Every room with its people and planning revisions.
export async function GET() {
  const [rooms, members] = await Promise.all([sql("SELECT * FROM room"), sql("SELECT * FROM membership")]);
  return Response.json(
    rooms
      .map(r => ({ ...r, id: r.id as number, members: members.filter(m => m.room_id === r.id && m.active).map(m => m.display_name) }))
      .sort((a, b) => Number(b.id) - Number(a.id))
  );
}
