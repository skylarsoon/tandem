import json
import os
import sqlite3
from types import SimpleNamespace
import pytest
from tandem_worker.cloud import verify_state, STATE_FILES
from tandem_worker.contracts import digest


def seeded(tmp_path):
    settings=SimpleNamespace(claude_model='model',apify_actor_build='build',stdb_database='tandem',stdb_url='https://backend')
    scope=digest(['serve','model','build']);day=scope+':2026-11-01'
    for name in STATE_FILES:
        with sqlite3.connect(tmp_path/name) as c:
            c.execute('CREATE TABLE marker (n INTEGER)')
    with sqlite3.connect(tmp_path/'providers.sqlite') as c:
        c.execute('CREATE TABLE provider_budget (scope TEXT,kind TEXT,used INTEGER)')
        c.execute('CREATE TABLE provider_daily_limits (scope TEXT,kind TEXT,ceiling INTEGER)')
        c.execute('INSERT INTO provider_budget VALUES (?,?,?)',(day,'apify',49))
        c.execute('INSERT INTO provider_daily_limits VALUES (?,?,?)',(day,'apify',52))
    manifest={'schema_version':1,'budget_scope':scope,'database':'tandem','backend_url':'https://backend','usage':[[day,'apify',49]],'daily_overrides':[[day,'apify',52]]}
    (tmp_path/'migration.json').write_text(json.dumps(manifest))
    return settings


def test_missing_volume_fails_closed(tmp_path):
    with pytest.raises(RuntimeError,match='MIGRATED_STATE_REQUIRED'):
        verify_state(tmp_path,SimpleNamespace())


def test_migrated_usage_and_exact_date_override_are_preserved(tmp_path):
    settings=seeded(tmp_path)
    assert verify_state(tmp_path,settings)['usage'][0][2]==49
    with sqlite3.connect(tmp_path/'providers.sqlite') as c:
        c.execute('UPDATE provider_budget SET used=50')
    verify_state(tmp_path,settings)


def test_reset_usage_and_changed_scope_are_rejected(tmp_path):
    settings=seeded(tmp_path)
    settings.claude_model='different'
    with pytest.raises(RuntimeError,match='SCOPE_MISMATCH'): verify_state(tmp_path,settings)
    settings.claude_model='model'
    with sqlite3.connect(tmp_path/'providers.sqlite') as c:
        c.execute('UPDATE provider_budget SET used=0')
    with pytest.raises(RuntimeError,match='USAGE_REGRESSED'): verify_state(tmp_path,settings)


def test_missing_ledger_or_changed_limits_are_rejected(tmp_path):
    settings=seeded(tmp_path)
    with sqlite3.connect(tmp_path/'providers.sqlite') as c:
        c.execute('DELETE FROM provider_daily_limits')
    with pytest.raises(RuntimeError,match='LIMIT_MISMATCH'): verify_state(tmp_path,settings)
    (tmp_path/'runs.sqlite').unlink()
    with pytest.raises(RuntimeError,match='DATABASE_MISSING'): verify_state(tmp_path,settings)


def test_private_import_is_atomic_and_never_rewinds_existing_state(tmp_path):
    import base64, hashlib, io, tarfile
    from tandem_worker.cloud import import_state
    source=tmp_path/'source';source.mkdir();settings=seeded(source)
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        for name in (*STATE_FILES,'migration.json'): archive.add(source/name,arcname=name)
    raw=buffer.getvalue();encoded=tmp_path/'migration.b64';encoded.write_bytes(base64.b64encode(raw))
    destination=tmp_path/'volume'/'state'
    with pytest.raises(RuntimeError,match='CHECKSUM'): import_state(destination,settings,encoded,'wrong')
    assert not destination.exists()
    import_state(destination,settings,encoded,hashlib.sha256(raw).hexdigest())
    with sqlite3.connect(destination/'providers.sqlite') as c:c.execute('UPDATE provider_budget SET used=51')
    import_state(destination,settings,encoded,hashlib.sha256(raw).hexdigest())
    with sqlite3.connect(destination/'providers.sqlite') as c:assert c.execute('SELECT used FROM provider_budget').fetchone()[0]==51


def test_import_rejects_archive_paths(tmp_path):
    import base64,hashlib,io,tarfile
    from tandem_worker.cloud import import_state
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        entry=tarfile.TarInfo('../escape');entry.size=1;archive.addfile(entry,io.BytesIO(b'x'))
    raw=buffer.getvalue();encoded=tmp_path/'input';encoded.write_bytes(base64.b64encode(raw))
    with pytest.raises(RuntimeError,match='ARCHIVE_INVALID'):
        import_state(tmp_path/'destination',SimpleNamespace(),encoded,hashlib.sha256(raw).hexdigest())
    assert not (tmp_path/'escape').exists()


def test_export_refuses_a_running_worker_and_preserves_usage(tmp_path):
    import importlib.util
    from pathlib import Path
    from unittest.mock import patch
    script=Path(__file__).resolve().parents[3]/'deploy/worker/export_state.py'
    spec=importlib.util.spec_from_file_location('export_state',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    source=tmp_path/'source';source.mkdir();settings=seeded(source)
    destination=tmp_path/'snapshot'
    with pytest.raises(RuntimeError,match='SOURCE_WORKER_STILL_RUNNING'):
        module.export(source,destination,settings,os.getpid())
    assert not destination.exists()
    with patch.object(module.os,'kill',side_effect=ProcessLookupError):
        module.export(source,destination,settings,123456)
    manifest=verify_state(destination,settings)
    assert manifest['usage'][0][2]==49
    assert manifest['daily_overrides'][0][2]==52
    assert (destination/'providers.sqlite').stat().st_mode & 0o777 == 0o600
    with patch.object(module.os,'kill',side_effect=ProcessLookupError):
        with pytest.raises(RuntimeError,match='EXPORT_DESTINATION_EXISTS'):
            module.export(source,destination,settings,123456)


def test_vm_loads_private_environment_file_before_serving(tmp_path,monkeypatch):
    from tandem_worker import cloud
    from unittest.mock import Mock
    settings=Mock()
    loader=Mock(return_value=settings);verify=Mock();serve=Mock()
    monkeypatch.setenv('TANDEM_CLOUD_ENABLED','1')
    monkeypatch.delenv('TANDEM_IMPORT_MIGRATION',raising=False)
    monkeypatch.setenv('TANDEM_ENV_FILE',str(tmp_path/'worker.env'))
    monkeypatch.setenv('TANDEM_STATE_DIR',str(tmp_path/'state'))
    monkeypatch.setattr(cloud,'load_settings',loader)
    monkeypatch.setattr(cloud,'verify_state',verify)
    monkeypatch.setattr(cloud,'serve',serve)
    cloud.main()
    loader.assert_called_once_with(tmp_path/'worker.env')
    verify.assert_called_once_with(tmp_path/'state',settings)
    config,actual_settings,directory=serve.call_args.args
    assert (config.limits.model_calls,config.limits.actor_runs,config.limits.max_charge_usd)==(500,40,1)
    assert actual_settings is settings and directory==tmp_path/'state'
