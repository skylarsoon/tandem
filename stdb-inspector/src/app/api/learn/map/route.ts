import { moduleMap } from "@/lib/module-map";

export async function GET() {
  return Response.json(moduleMap());
}
