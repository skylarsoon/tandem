import { PERSON_VIEWS, type Person, load, viewAs } from "@/lib/sandbox";

// What one sandbox person's browser actually receives: each view, queried with their token.
export async function GET(request: Request) {
  const person = new URL(request.url).searchParams.get("person") as Person | null;
  const state = load();
  if (!state || !person || !(person in state.people)) return Response.json({ error: "no such person" }, { status: 404 });
  const token = state.people[person].token;
  const results = await Promise.all(PERSON_VIEWS.map(async v => [v, await viewAs(token, v)] as const));
  return Response.json(Object.fromEntries(results));
}
