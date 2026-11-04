"""Fail-closed cloud entrypoint: never replace missing migrated usage with zero."""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import time
import tarfile
import tempfile

from .config import load_settings
from .contracts import digest
from .serve import ServeConfig, serve

STATE_FILES = ("providers.sqlite", "runs.sqlite", "checkpoints.sqlite")


def verify_state(directory: Path, settings):
    manifest_path = directory / "migration.json"
    if not manifest_path.is_file():
        raise RuntimeError("MIGRATED_STATE_REQUIRED")
    manifest = json.loads(manifest_path.read_text())
    expected = digest(["serve", settings.claude_model, settings.apify_actor_build])
    if manifest.get("schema_version") != 1 or manifest.get("budget_scope") != expected:
        raise RuntimeError("MIGRATION_SCOPE_MISMATCH")
    if manifest.get("database") != settings.stdb_database or manifest.get("backend_url") != settings.stdb_url:
        raise RuntimeError("MIGRATION_BACKEND_MISMATCH")
    for name in STATE_FILES:
        path = directory / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("MIGRATED_DATABASE_MISSING")
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("MIGRATED_DATABASE_INVALID")
    with sqlite3.connect(f"file:{directory / 'providers.sqlite'}?mode=ro", uri=True) as connection:
        current = {(s,k):n for s,k,n in connection.execute("SELECT scope,kind,used FROM provider_budget")}
        limits = {(s,k):n for s,k,n in connection.execute("SELECT scope,kind,ceiling FROM provider_daily_limits")}
    for scope, kind, used in manifest["usage"]:
        if current.get((scope, kind), 0) < used:
            raise RuntimeError("MIGRATED_USAGE_REGRESSED")
    for scope, kind, ceiling in manifest["daily_overrides"]:
        if limits.get((scope,kind)) != ceiling:
            raise RuntimeError("MIGRATED_LIMIT_MISMATCH")
    return manifest


def import_state(directory: Path, settings, encoded: Path, expected_sha256: str):
    """One-time private Render secret-file transfer, only while spending is disabled.

    The deployment removes the transfer secret and import flag before activation.
    It must never be retained as an automatic backup restore after a disk failure.
    """
    if directory.exists():
        verify_state(directory, settings)
        return  # Never overwrite a volume, including on a retried held deploy.
    raw = base64.b64decode(encoded.read_bytes(), validate=True)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RuntimeError("MIGRATION_CHECKSUM_MISMATCH")
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".tandem-import-", dir=directory.parent) as temp:
        staging = Path(temp) / "state"
        staging.mkdir(mode=0o700)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            entries = archive.getmembers()
            allowed = {*STATE_FILES, "migration.json"}
            if len(entries) != len(allowed) or {e.name for e in entries} != allowed:
                raise RuntimeError("MIGRATION_ARCHIVE_INVALID")
            if any(not e.isfile() or e.size > 32 * 1024 * 1024 for e in entries) or sum(e.size for e in entries) > 64 * 1024 * 1024:
                raise RuntimeError("MIGRATION_ARCHIVE_INVALID")
            for entry in entries:
                path = staging / entry.name
                with archive.extractfile(entry) as source, path.open("xb") as target:
                    target.write(source.read())
                path.chmod(0o600)
        verify_state(staging, settings)
        staging.rename(directory)


def main():
    directory = Path(os.environ.get("TANDEM_STATE_DIR", "/data/state"))
    enabled = os.environ.get("TANDEM_CLOUD_ENABLED") == "1"
    importing = os.environ.get("TANDEM_IMPORT_MIGRATION") == "1"
    if enabled and importing:
        raise RuntimeError("REMOVE_MIGRATION_IMPORT_BEFORE_ACTIVATION")
    if not enabled:
        if importing:
            settings = load_settings(Path(os.environ.get("TANDEM_ENV_FILE", "/nonexistent/tandem.env")))
            settings.require(); settings.require_backend()
            import_state(directory, settings, Path("/etc/secrets/tandem-migration.b64"),
                         os.environ.get("TANDEM_MIGRATION_SHA256", ""))
            print(json.dumps({"migration":"verified", "provider_calls_enabled":False}), flush=True)
        print(json.dumps({"service":"migration-hold", "provider_calls_enabled":False}), flush=True)
        signal.signal(signal.SIGTERM, lambda *_: exit(0))
        while True:
            time.sleep(5)
    settings = load_settings(Path(os.environ.get("TANDEM_ENV_FILE", "/nonexistent/tandem.env")))
    settings.require(); settings.require_backend()
    verify_state(directory, settings)
    # Keep provider ceilings in code/config, not mutable deployment variables.
    config = ServeConfig()
    if config.limits.model_calls != 500 or config.limits.actor_runs != 40 or config.limits.max_charge_usd != 1:
        raise RuntimeError("UNEXPECTED_CLOUD_LIMITS")
    print(json.dumps({"service":"verified-migrated-state", "daily_claude":500,
                      "daily_apify_default":40, "max_actor_charge_usd":1}), flush=True)
    serve(config, settings, directory)


if __name__ == "__main__":
    main()
