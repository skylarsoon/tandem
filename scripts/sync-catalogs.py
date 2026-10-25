#!/usr/bin/env python3
"""Copy the shared catalogs in contracts/ (the single sources) to where each part
reads them: typed constants for the database module and the web app, package
data for the worker. Run after editing any of them; tests fail if a copy is stale.

- profile_questions.json: the profile onboarding questions
- attributes.json, legacy_keys.json: the rule language's vocabulary and the old named limits
- decisions.json: how each kind of choice is made (who picks, who pays) and group rules
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
HEADER = "// Generated from contracts/ by scripts/sync-catalogs.py. Do not edit.\n"


def load(name: str):
    return json.loads((CONTRACTS / name).read_text())


def typescript(**constants) -> str:
    body = "\n".join(f"export const {name} = {json.dumps(value, indent=2, ensure_ascii=False)} as const;\n"
                     for name, value in constants.items())
    return f"{HEADER}\n{body}"


def main() -> None:
    profile, attributes, legacy = load("profile_questions.json"), load("attributes.json"), load("legacy_keys.json")
    decisions = load("decisions.json")
    worker = ROOT / "worker" / "src" / "tandem_worker"
    (ROOT / "spacetimedb" / "src" / "profile_catalog.ts").write_text(typescript(PROFILE_CATALOG=profile))
    (ROOT / "web" / "lib" / "profile_catalog.ts").write_text(typescript(PROFILE_CATALOG=profile))
    (ROOT / "spacetimedb" / "src" / "rule_catalog.ts").write_text(typescript(RULE_ATTRIBUTES=attributes, LEGACY_KEYS=legacy, DECISIONS=decisions))
    for name, value in (("profile_questions.json", profile), ("attributes.json", attributes), ("legacy_keys.json", legacy),
                        ("decisions.json", decisions)):
        (worker / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    print(f"synced: {len(profile['questions'])} profile questions, "
          f"{sum(len(a) for a in attributes['categories'].values())} attributes, {len(legacy['keys'])} named limits")


if __name__ == "__main__":
    main()
