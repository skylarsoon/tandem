// Runs the database module's rule evaluator (spacetimedb/src/rules.ts) against
// contracts/rule_cases.json and prints one verdict per case as JSON, for the
// worker's tests to compare with the Python evaluator. Needs Node 22.18+ (runs .ts directly).
import { readFileSync } from "node:fs";
import { evaluateRule } from "../spacetimedb/src/rules.ts";

const root = new URL("../", import.meta.url);
const catalog = JSON.parse(readFileSync(new URL("contracts/attributes.json", root)));
const { cases } = JSON.parse(readFileSync(new URL("contracts/rule_cases.json", root)));
const verdicts = cases.map(({ rule, facts }) => {
  try {
    return evaluateRule(rule, facts, catalog);
  } catch (error) {
    if (error?.code === "INVALID_RULE") return "INVALID_RULE";
    throw error;
  }
});
console.log(JSON.stringify(verdicts));
