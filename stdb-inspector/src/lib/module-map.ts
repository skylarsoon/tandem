// Reads the SpacetimeDB module's own source (../spacetimedb/src) and works out,
// for every reducer, procedure and view, which tables it reads and writes, and
// who may call it. Derived from the code, so it stays true as the module changes.
import fs from "node:fs";
import path from "node:path";

export type Op = "insert" | "update" | "delete" | "read";
export type Entry = {
  name: string; // snake_case, as clients call it
  export: string; // camelCase export in the module
  kind: "reducer" | "procedure" | "view" | "init" | "clientConnected" | "clientDisconnected";
  file: string;
  caller: "person" | "worker" | "owner" | "scheduler" | "anyone" | "system";
  tables: Record<string, Op[]>;
  helpers: string[];
  source: string;
};
export type ModuleMap = { entries: Entry[]; tables: Record<string, { accessor: string; schedule?: string }> };

const MODULE_DIR = process.env.STDB_MODULE_DIR ?? path.resolve(process.cwd(), "..", "spacetimedb", "src");
const snake = (s: string) => s.replace(/[A-Z]/g, c => `_${c.toLowerCase()}`);

function blocks(text: string, start: string) {
  // Top-level definitions: from each match to the next top-level `export`/`function`/`const`.
  const found: { name: string; body: string; groups: RegExpExecArray }[] = [];
  const starts = [...text.matchAll(new RegExp(start, "gm"))];
  const boundaries = [...text.matchAll(/^(export |function |const |async function )/gm)].map(m => m.index!);
  for (const m of starts) {
    const next = boundaries.find(b => b > m.index!) ?? text.length;
    found.push({ name: m[1], body: text.slice(m.index!, next), groups: m as RegExpExecArray });
  }
  return found;
}

function accesses(body: string, accessorToTable: Map<string, string>) {
  const result: Record<string, Set<Op>> = {};
  for (const m of body.matchAll(/\.db\.(\w+)\.(?:\w+\.)?(insert|update|delete|find|filter|iter|count)\(/g)) {
    const table = accessorToTable.get(m[1]);
    if (!table) continue;
    const op: Op = m[2] === "insert" || m[2] === "update" || m[2] === "delete" ? m[2] : "read";
    (result[table] ??= new Set()).add(op);
  }
  return result;
}

let cached: { at: number; map: ModuleMap } | null = null;

export function moduleMap(): ModuleMap {
  if (cached && Date.now() - cached.at < 10_000) return cached.map;
  const files = fs.readdirSync(MODULE_DIR).filter(f => f.endsWith(".ts"));
  const sources = Object.fromEntries(files.map(f => [f, fs.readFileSync(path.join(MODULE_DIR, f), "utf8")]));

  // const privateMessage = table({ name: 'private_message', scheduled: ... }, ...)
  const accessorToTable = new Map<string, string>();
  const tables: ModuleMap["tables"] = {};
  for (const m of sources["schema.ts"].matchAll(/(?:export )?const (\w+) = table\(\s*\{\s*name: '(\w+)'/g)) {
    accessorToTable.set(m[1], m[2]);
    tables[m[2]] = { accessor: m[1] };
  }

  // Helper functions anywhere in the module, with their direct table accesses.
  const helpers = new Map<string, { body: string; direct: Record<string, Set<Op>> }>();
  for (const [, text] of Object.entries(sources)) {
    for (const b of blocks(text, /^(?:export )?(?:async )?function (\w+)\(/.source)) {
      helpers.set(b.name, { body: b.body, direct: accesses(b.body, accessorToTable) });
    }
  }
  // Transitive closure through helper calls.
  const closure = (body: string, seen = new Set<string>()) => {
    const total = accesses(body, accessorToTable);
    const called: string[] = [];
    for (const [name, h] of helpers) {
      if (seen.has(name) || !new RegExp(`\\b${name}\\(`).test(body) || body.includes(`function ${name}(`)) continue;
      seen.add(name);
      called.push(name);
      const inner = closure(h.body, seen);
      called.push(...inner.called);
      for (const [t, ops] of Object.entries(inner.total)) for (const op of ops) (total[t] ??= new Set()).add(op);
    }
    return { total, called };
  };

  const entries: Entry[] = [];
  for (const [file, text] of Object.entries(sources)) {
    for (const b of blocks(text, /^export const (\w+) = spacetimedb\.(reducer|procedure|view|init|clientConnected|clientDisconnected)\(/.source)) {
      const kind = b.groups[2] as Entry["kind"];
      const { total, called } = closure(b.body);
      const name = kind === "view" ? (b.body.match(/name: '(\w+)'/)?.[1] ?? snake(b.name)) : snake(b.name);
      // reducer({ onSchedule: leaseTimer }, ...) runs when a row in that table comes due.
      const scheduleTable = accessorToTable.get(b.body.match(/onSchedule: (\w+)/)?.[1] ?? "");
      if (scheduleTable) tables[scheduleTable].schedule = name;
      const isScheduled = Boolean(scheduleTable);
      const caller: Entry["caller"] =
        kind === "init" || kind === "clientConnected" || kind === "clientDisconnected" ? "system"
        : isScheduled ? "scheduler"
        : /requireOwner|config\.id\.find|not the database owner|database owner/.test(b.body) ? "owner"
        : /requireWorker\(|requireLease\(/.test(b.body) ? "worker"
        : /requireHuman\(|requireMember\(/.test(b.body) ? "person"
        : kind === "view" ? "anyone" : "anyone";
      entries.push({
        name, export: b.name, kind, file, caller,
        tables: Object.fromEntries(Object.entries(total).map(([t, ops]) => [t, [...ops].sort()])),
        helpers: [...new Set(called)],
        source: b.body.slice(0, 4000),
      });
    }
  }
  const map = { entries, tables };
  cached = { at: Date.now(), map };
  return map;
}
