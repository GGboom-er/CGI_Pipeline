"""Transport startup must not own the shared runtime or write diagnostics to stdout."""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgi_pipeline.core import service_manager as sm


def test_shared_spawn_detaches_from_client_process_group(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sm.subprocess, 'Popen', lambda *args, **kwargs: calls.append(kwargs))
    sm._spawn_shared_process(['python', 'worker.py'], cwd=tmp_path, env={'X': 'test'})
    assert calls[0]['env'] == {'X': 'test'}
    assert calls[0]['stdout'] == sm.subprocess.DEVNULL
    if sm.sys.platform == 'win32':
        assert calls[0]['creationflags'] & sm.subprocess.CREATE_BREAKAWAY_FROM_JOB
        assert calls[0]['creationflags'] & sm.subprocess.CREATE_NO_WINDOW
    else:
        assert calls[0]['start_new_session'] is True


def test_shared_spawn_propagates_missing_executable(monkeypatch, tmp_path):
    def missing(*args, **kwargs):
        raise FileNotFoundError('missing executable')
    monkeypatch.setattr(sm.subprocess, 'Popen', missing)
    with pytest.raises(FileNotFoundError):
        sm._spawn_shared_process(['missing'], cwd=tmp_path)


def test_legacy_http_owner_loses_shutdown_rights_without_stopping_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, 'RUNTIME_DIR', tmp_path)
    monkeypatch.setattr(sm, '_is_pid_alive', lambda *args: True)
    monkeypatch.setattr(sm, 'stop_worker', lambda *args: pytest.fail('shared Worker must survive'))
    path = sm._worker_owner_path()
    old = {'owner_pid': 123}
    path.write_text(json.dumps(old), encoding='utf-8')
    sm.share_worker_runtime()
    sm.share_worker_runtime()
    assert json.loads(path.read_text(encoding='utf-8'))['previous_owner'] == old
    assert sm.get_worker_owner() is None
    assert not sm.shutdown_worker_owned_by(123)


def test_cold_worker_diagnostics_leave_stdout_clean(tmp_path, monkeypatch, capsys, caplog):
    monkeypatch.setattr(sm, 'RUNTIME_DIR', tmp_path / 'runtime')
    monkeypatch.setattr(sm, 'LOGS_DIR', tmp_path / 'logs')
    monkeypatch.setattr(sm, 'is_worker_alive', lambda *args: (False, None))
    monkeypatch.setattr(sm, '_await_worker_ready', lambda *args: True)
    monkeypatch.setattr(sm.subprocess, 'Popen', lambda *args, **kwargs: SimpleNamespace(pid=123))
    monkeypatch.setattr(sm, '_managed_procs', [])
    with caplog.at_level(logging.INFO):
        assert sm.start_worker('cgi')
    assert capsys.readouterr().out == ''
    assert '123' in caplog.text


def test_redis_start_failure_leaves_stdout_clean(tmp_path, monkeypatch, capsys, caplog):
    monkeypatch.setattr(sm, 'PROJECT_ROOT', tmp_path / 'empty')
    monkeypatch.setattr(sm, 'is_redis_alive', lambda: False)
    monkeypatch.delenv('REDIS_SERVER_PATH', raising=False)
    with caplog.at_level(logging.WARNING):
        assert not sm.start_redis()
    assert capsys.readouterr().out == ''
    assert caplog.text


@pytest.mark.parametrize('argv', [['server.py'], ['server.py', '--http']])
def test_frontend_lifecycle_never_stops_shared_worker(monkeypatch, tmp_path, argv):
    from cgi_pipeline.server import server
    calls = []
    monkeypatch.setattr(sm, 'RUNTIME_DIR', tmp_path)
    monkeypatch.setattr(server.sys, 'argv', argv)
    monkeypatch.setattr(sm, 'is_worker_alive', lambda *args: (True, 123))
    monkeypatch.setattr(sm, 'ensure_worker_healthy', lambda *args: (True, 'ready'))
    monkeypatch.setattr(sm, 'get_worker_owner', lambda: None)
    monkeypatch.setattr(sm, 'claim_worker_owner', lambda *args: calls.append('claim'))
    monkeypatch.setattr(sm, 'shutdown_worker_owned_by', lambda *args: calls.append('stop'))
    async def exercise():
        async with server._server_lifespan(server.mcp):
            pass
    asyncio.run(exercise())
    assert calls == []


def test_server_audit_root_is_not_inherited_from_client(monkeypatch, tmp_path):
    import importlib
    from cgi_pipeline.server import server
    from cgi_pipeline.paths import REPOSITORY_ROOT
    monkeypatch.setenv('PROJECT_ROOT', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    module = importlib.reload(server)
    assert module.PROJECT_ROOT == REPOSITORY_ROOT
