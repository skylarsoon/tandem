import { type Person, act, create, load, runWorker } from "@/lib/sandbox";

export async function GET() {
  const state = load();
  return Response.json(state ? { roomId: state.roomId, people: Object.fromEntries(Object.entries(state.people).map(([k, v]) => [k, v.identity])) } : null);
}

export async function POST(request: Request) {
  const { action, person, args } = (await request.json()) as { action: string; person?: Person; args?: Record<string, unknown> };
  try {
    if (action === "create") {
      const state = await create();
      return Response.json({ ok: true, result: `Room #${state.roomId} created by ava` });
    }
    if (action === "worker") {
      const state = load();
      if (!state) throw new Error("Create the sandbox room first");
      return Response.json({ ok: true, result: await runWorker(state.roomId) });
    }
    const result = await act(action, person ?? "ava", args ?? {});
    return Response.json({ ok: true, result });
  } catch (error) {
    return Response.json({ ok: false, error: String(error instanceof Error ? error.message : error) });
  }
}
