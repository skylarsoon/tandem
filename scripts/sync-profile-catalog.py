#!/usr/bin/env python3
"""Copy contracts/profile_questions.json (the single source) to where each part
reads it: the database module and the web app (typed constants) and the worker
(package data). Run after editing the catalog; tests fail if a copy is stale."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts" / "profile_questions.json"
HEADER = "// Generated from contracts/profile_questions.json by scripts/sync-profile-catalog.py. Do not edit.\n"


def main() -> None:
    catalog = json.loads(SOURCE.read_text())
    body = json.dumps(catalog, indent=2, ensure_ascii=False)
    typescript = f"{HEADER}\nexport const PROFILE_CATALOG = {body} as const;\n"
    (ROOT / "spacetimedb" / "src" / "profile_catalog.ts").write_text(typescript)
    (ROOT / "web" / "lib" / "profile_catalog.ts").write_text(typescript)
    (ROOT / "worker" / "src" / "tandem_worker" / "profile_questions.json").write_text(body + "\n")
    print(f"profile catalog v{catalog['version']}: {len(catalog['questions'])} questions synced")


if __name__ == "__main__":
    main()
