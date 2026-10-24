// Server-side only: reads every table as the database owner. The owner token is
// read here and never sent to the browser; the page only talks to /api routes.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const STDB_URL = process.env.STDB_URL ?? "https://maincloud.spacetimedb.com";
export const STDB_DB = process.env.STDB_DB ?? "tandem-dev";

type AlgebraicType = Record<string, unknown>;
type Element = { name: { some?: string }; algebraic_type: AlgebraicType };
export type Row = Record<string, unknown>;

function ownerToken(): string {
  if (process.env.STDB_OWNER_TOKEN) return process.env.STDB_OWNER_TOKEN;
  // Same login the `spacetime` CLI uses (the identity that published the module).
  const config = path.join(os.homedir(), ".config", "spacetime", "cli.toml");
  const match = fs.readFileSync(config, "utf8").match(/^spacetimedb_token\s*=\s*"([^"]+)"/m);
  if (!match) throw new Error("No owner token: run `spacetime login` or set STDB_OWNER_TOKEN");
  return match[1];
}

async function post(pathname: string, body?: string) {
  const res = await fetch(`${STDB_URL}/v1/database/${STDB_DB}${pathname}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${ownerToken()}` },
    body,
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`SpacetimeDB ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

// SATS JSON → plain values: identities as hex, timestamps as epoch ms,
// options as value-or-null, products as objects, arrays as arrays.
export function decode(value: unknown, type: AlgebraicType): unknown {
  if ("Product" in type) {
    const elements = (type.Product as { elements: Element[] }).elements;
    const names = elements.map(e => e.name.some ?? "");
    const parts = value as unknown[];
    if (names.length === 1 && names[0] === "__identity__") return parts[0];
    if (names.length === 1 && names[0] === "__timestamp_micros_since_unix_epoch__") return Number(parts[0]) / 1000;
    return Object.fromEntries(elements.map((e, i) => [names[i], decode(parts[i], e.algebraic_type)]));
  }
  if ("Sum" in type) {
    const variants = (type.Sum as { variants: Element[] }).variants;
    const [tag, inner] = value as [number, unknown];
    const variant = variants[tag];
    if (variant?.name.some === "none") return null;
    if (variant?.name.some === "some") return decode(inner, variant.algebraic_type);
    return { [variant?.name.some ?? String(tag)]: decode(inner, variant?.algebraic_type ?? {}) };
  }
  if ("Array" in type) return (value as unknown[]).map(v => decode(v, type.Array as AlgebraicType));
  return value;
}

export async function sql(query: string): Promise<Row[]> {
  const [result] = (await post("/sql", query)) as { schema: { elements: Element[] }; rows: unknown[][] }[];
  const elements = result.schema.elements;
  return result.rows.map(row => Object.fromEntries(elements.map((e, i) => [e.name.some ?? String(i), decode(row[i], e.algebraic_type)])));
}

export type TableInfo = { name: string; columns: string[]; access: string };
let schemaCache: { at: number; tables: TableInfo[] } | null = null;

// Every table in the module, read from its schema, so new tables appear here
// without changing this tool.
export async function tables(): Promise<TableInfo[]> {
  if (schemaCache && Date.now() - schemaCache.at < 60_000) return schemaCache.tables;
  const schema = await post("/schema?version=9");
  const types = schema.typespace.types as { Product: { elements: Element[] } }[];
  const found = (schema.tables as { name: string; product_type_ref: number; table_access: Record<string, unknown> }[]).map(t => ({
    name: t.name,
    columns: types[t.product_type_ref].Product.elements.map(e => e.name.some ?? ""),
    access: Object.keys(t.table_access)[0],
  }));
  schemaCache = { at: Date.now(), tables: found };
  return found;
}

export function isTableName(name: string, known: TableInfo[]): boolean {
  return /^[a-z_]+$/.test(name) && known.some(t => t.name === name);
}
