// Runs the database module's rule evaluator (spacetimedb/src/rules.ts) against
// contracts/rule_cases.json and prints its verdicts and legacy conversions as
// JSON, for the worker's tests to compare with the Python side. Needs Node 22.18+ (runs .ts directly).
import { readFileSync } from "node:fs";
import { evaluateRule, fromLegacy } from "../spacetimedb/src/rules.ts";

const root = new URL("../", import.meta.url);
const catalog = JSON.parse(readFileSync(new URL("contracts/attributes.json", root)));
const legacy = JSON.parse(readFileSync(new URL("contracts/legacy_keys.json", root)));
const { cases, legacy_cases } = JSON.parse(readFileSync(new URL("contracts/rule_cases.json", root)));
const verdicts = cases.map(({ rule, facts }) => {
  try {
    return evaluateRule(rule, facts, catalog);
  } catch (error) {
    if (error?.code === "INVALID_RULE") return "INVALID_RULE";
    throw error;
  }
});
const conversions = legacy_cases.map(({ key, row }) => fromLegacy(key, row, legacy, catalog));
console.log(JSON.stringify({ verdicts, conversions }));
