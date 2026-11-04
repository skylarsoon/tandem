"""Run only after the original worker process has fully stopped.

Exports SQLite backups and a non-secret verification manifest. The databases
contain private trip data: upload only to the private worker volume, never Git.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3

from tandem_worker.cloud import STATE_FILES
from tandem_worker.config import load_settings
from tandem_worker.contracts import digest


def export(source, destination, settings, source_pid):
    if source_pid < 1:
        raise RuntimeError("INVALID_SOURCE_PID")
    try:
        os.kill(source_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise RuntimeError("SOURCE_WORKER_STILL_RUNNING")
    if destination.exists():
        raise RuntimeError("EXPORT_DESTINATION_EXISTS")
    for name in STATE_FILES:
        if not (source / name).is_file():
            raise RuntimeError("SOURCE_DATABASE_MISSING")
    destination.mkdir(mode=0o700, parents=True)
    with (source / "worker-service.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name in STATE_FILES:
            with sqlite3.connect(f"file:{source / name}?mode=ro", uri=True) as src, sqlite3.connect(destination / name) as dst:
                src.backup(dst)
            (destination / name).chmod(0o600)
        with sqlite3.connect(destination / "providers.sqlite") as conn:
            usage = conn.execute("SELECT scope,kind,used FROM provider_budget ORDER BY scope,kind").fetchall()
            overrides = conn.execute("SELECT scope,kind,ceiling FROM provider_daily_limits ORDER BY scope,kind").fetchall()
        manifest = {"schema_version":1,"exported_at":datetime.now(timezone.utc).isoformat(),
                    "budget_scope":digest(["serve",settings.claude_model,settings.apify_actor_build]),
                    "database":settings.stdb_database,"backend_url":settings.stdb_url,
                    "usage":usage,"daily_overrides":overrides}
        (destination / "migration.json").write_text(json.dumps(manifest,indent=2))
        (destination / "migration.json").chmod(0o600)
    print(json.dumps({"exported":True,"files":list(STATE_FILES),"usage_rows":len(usage)}))


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--source-pid',type=int,required=True)
    args=parser.parse_args()
    export(args.source,args.destination,load_settings(args.env_file),args.source_pid)
