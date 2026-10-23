from __future__ import annotations

import importlib
import builtins
import json
import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgi_pipeline.hosts.runtime import build_host_env, host_runtime, record_host_process
from cgi_pipeline.paths import REPOSITORY_ROOT, SOURCE_ROOT


RUNTIME_KEYS = ('CGI_RUNTIME_ID', 'CGI_RUNTIME_GENERATION', 'CGI_RUNTIME_ROOT',
                'CGI_RUNTIME_THREADS')


def runtime_env(root, runtime_id='slot-a', threads='2'):
    return {'CGI_RUNTIME_ID': runtime_id, 'CGI_RUNTIME_GENERATION': 'generation-a',
            'CGI_RUNTIME_ROOT': str(root), 'CGI_RUNTIME_THREADS': threads}


@pytest.fixture(autouse=True)
def clear_runtime(monkeypatch):
    for key in RUNTIME_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_legacy_namespace_changes_no_environment_or_directories(tmp_path):
    original = {'TEMP': 'legacy-temp', 'CGI_RUNTIME_THREADS': '4'}
    runtime = host_runtime(tmp_path, original)
    assert not runtime.enabled and runtime.root == tmp_path.resolve()
    assert runtime.threads is None
    assert build_host_env('maya', tmp_path, original) == original
    assert build_host_env('blender', tmp_path, original) == original
    assert list(tmp_path.iterdir()) == []


def test_helper_import_needs_only_host_standard_library(monkeypatch):
    original_import = builtins.__import__
    def host_import(name, *args, **kwargs):
        if name.split('.')[0] in ('psutil', 'dotenv', 'celery', 'maya', 'bpy'):
            raise ImportError(f'Host does not provide {name}')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', host_import)
    namespace = runpy.run_path(str(SOURCE_ROOT / 'cgi_pipeline' / 'hosts' / 'runtime.py'))
    assert not namespace['host_runtime'](REPOSITORY_ROOT, {}).enabled


@pytest.mark.parametrize('updates', [
    {'CGI_RUNTIME_ID': ''}, {'CGI_RUNTIME_GENERATION': ''},
    {'CGI_RUNTIME_ROOT': ''}, {'CGI_RUNTIME_ROOT': 'relative/slot'},
    {'CGI_RUNTIME_THREADS': '0'}, {'CGI_RUNTIME_THREADS': '-1'},
    {'CGI_RUNTIME_THREADS': 'many'}, {'CGI_RUNTIME_THREADS': '1025'},
])
def test_invalid_namespace_refuses_before_creating_paths(tmp_path, updates):
    with pytest.raises(ValueError, match='CGI_RUNTIME_'):
        host_runtime(REPOSITORY_ROOT, runtime_env(tmp_path) | updates)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('host', ['maya', 'blender'])
def test_child_environment_is_private_and_preserves_explicit_search_sources(tmp_path, host):
    root = tmp_path / 'slot'
    original = runtime_env(root) | {
        'PYTHONPATH': 'shared-python', 'MAYA_APP_DIR': str(tmp_path / 'maya-profile'),
        'MAYA_MODULE_PATH': 'shared-modules', 'MAYA_PLUG_IN_PATH': 'shared-plugins',
        'MAYA_SCRIPT_PATH': 'shared-mel', 'BLENDER_USER_SCRIPTS': str(tmp_path / 'scripts'),
        'BLENDER_SYSTEM_SCRIPTS': 'shared-system-scripts',
        'BLENDER_USER_RESOURCES': str(tmp_path / 'blender-profile'),
        'BLENDER_SYSTEM_EXTENSIONS': 'shared-extensions',
    }
    snapshot = dict(original)
    child = build_host_env(host, REPOSITORY_ROOT, original)
    assert original == snapshot and child is not original
    assert child['PYTHONPATH'] == 'shared-python'
    for key in ('TEMP', 'TMP'):
        assert Path(child[key]).is_relative_to(root) and Path(child[key]).is_dir()
    if host == 'maya':
        assert Path(child['MAYA_APP_DIR']).is_relative_to(root)
        for key, subdir in [('MAYA_MODULE_PATH', 'modules'),
                            ('MAYA_PLUG_IN_PATH', 'plug-ins'), ('MAYA_SCRIPT_PATH', 'scripts')]:
            assert child[key].split(os.pathsep) == [snapshot[key],
                                                    str(Path(snapshot['MAYA_APP_DIR']) / subdir)]
    else:
        for key in ('BLENDER_USER_RESOURCES', 'BLENDER_USER_CONFIG', 'BLENDER_USER_DATAFILES',
                    'BLENDER_USER_SCRIPTS', 'BLENDER_USER_EXTENSIONS'):
            assert Path(child[key]).is_relative_to(root) and Path(child[key]).is_dir()
        assert child['BLENDER_SYSTEM_SCRIPTS'].split(os.pathsep) == [
            'shared-system-scripts', snapshot['BLENDER_USER_SCRIPTS'],
            str(Path(snapshot['BLENDER_USER_RESOURCES']) / 'scripts')]
        assert child['BLENDER_SYSTEM_EXTENSIONS'].split(os.pathsep) == [
            'shared-extensions', str(Path(snapshot['BLENDER_USER_RESOURCES']) / 'extensions')]


@pytest.mark.parametrize('host', ['maya', 'blender'])
def test_workers_freeze_namespace_and_do_not_share_ipc(tmp_path, monkeypatch, host):
    module = importlib.import_module(f'cgi_pipeline.hosts.{host}.worker')
    worker_type = module.MayaWorker if host == 'maya' else module.BlenderWorker
    monkeypatch.setattr(module.uuid, 'uuid4', lambda: 'same-worker-id')
    workers = []
    for slot in ('a', 'b'):
        for key, value in runtime_env(tmp_path / slot, 'slot-' + slot).items():
            monkeypatch.setenv(key, value)
        workers.append(worker_type())
    first, second = workers
    assert first.worker_id == second.worker_id
    assert first.ipc.root == tmp_path / 'a' / 'ipc'
    assert second.ipc.root == tmp_path / 'b' / 'ipc'
    first.ipc.send({'task_id': 'one', 'api_id': 'example'})
    assert second.ipc.claim() is None
    assert first.ipc.claim()['task_id'] == 'one'
    assert first.runtime.root == tmp_path / 'a'
    assert first._environment['CGI_RUNTIME_ID'] == 'slot-a'


def fake_host(monkeypatch, host):
    calls = []
    if host == 'maya':
        standalone = SimpleNamespace(initialize=lambda **kwargs: calls.append(('initialize', kwargs)))
        cmds = SimpleNamespace(optionVar=lambda **kwargs: None,
                               threadCount=lambda **kwargs: calls.append(('threads', kwargs)))
        monkeypatch.setitem(sys.modules, 'maya', SimpleNamespace(standalone=standalone, cmds=cmds))
        monkeypatch.setitem(sys.modules, 'maya.standalone', standalone)
        monkeypatch.setitem(sys.modules, 'maya.cmds', cmds)
    else:
        monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace())
    return calls


@pytest.mark.parametrize('host', ['maya', 'blender'])
def test_adapter_keeps_package_source_and_uses_worker_slot_ipc(tmp_path, monkeypatch, host):
    module = importlib.import_module(f'cgi_pipeline.hosts.{host}.worker')
    worker_type = module.MayaWorker if host == 'maya' else module.BlenderWorker
    for key, value in runtime_env(tmp_path / 'slot').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('CGI_PROJECT_ROOT', str(tmp_path / 'different-legacy-root'))
    worker = worker_type()
    monkeypatch.setenv('CGI_WORKER_ID', worker.worker_id)
    worker.ipc.send({'task_id': 'pending', 'api_id': 'example'})
    calls = fake_host(monkeypatch, host)
    namespace = runpy.run_path(str(SOURCE_ROOT / 'cgi_pipeline' / 'hosts' / host / 'adapter.py'))
    assert namespace['SOURCE_ROOT'] == SOURCE_ROOT
    assert namespace['IPC'].root == worker.ipc.root
    assert namespace['IPC'].claim()['task_id'] == 'pending'
    namespace['_write_result']('pending', 'NEEDS_ATTENTION', {'value': 3})
    assert worker.ipc.exchange({'task_id': 'pending'}) == {
        'task_id': 'pending', 'status': 'NEEDS_ATTENTION', 'detail': {'value': 3}}
    if host == 'maya':
        assert calls == [('initialize', {'name': 'python'}), ('threads', {'numberOfThreads': 2})]


def test_legacy_adapter_does_not_apply_runtime_threads(tmp_path, monkeypatch):
    monkeypatch.setenv('CGI_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('CGI_RUNTIME_THREADS', '2')
    calls = fake_host(monkeypatch, 'maya')
    namespace = runpy.run_path(str(SOURCE_ROOT / 'cgi_pipeline' / 'hosts' / 'maya' / 'adapter.py'))
    assert namespace['IPC'].root == tmp_path / 'ipc'
    assert calls == [('initialize', {'name': 'python'})]


def fake_process_identity(monkeypatch, pid=1234):
    import psutil
    monkeypatch.setattr(psutil, 'Process', lambda actual: SimpleNamespace(
        create_time=lambda: 123.25, cmdline=lambda: ['host.exe', 'adapter.py']))
    return SimpleNamespace(pid=pid, poll=lambda: None)


def test_host_ownership_record_is_exact_and_atomically_published(tmp_path, monkeypatch):
    runtime = host_runtime(REPOSITORY_ROOT, runtime_env(tmp_path))
    process = fake_process_identity(monkeypatch)
    target = record_host_process(runtime, 'maya', 'worker-a', process)
    assert target == tmp_path / 'hosts' / 'worker-a.json'
    assert json.loads(target.read_text(encoding='utf-8')) == {
        'pid': 1234, 'create_time': 123.25, 'cmdline': ['host.exe', 'adapter.py'],
        'runtime_id': 'slot-a', 'generation': 'generation-a', 'root': str(tmp_path),
        'host': 'maya', 'worker_id': 'worker-a'}
    assert list(target.parent.iterdir()) == [target]
    assert record_host_process(host_runtime(tmp_path, {}), 'maya', 'legacy', process) is None


@pytest.mark.parametrize('host', ['maya', 'blender'])
@pytest.mark.parametrize('isolated', [False, True])
def test_host_start_routes_only_runtime_resources(tmp_path, monkeypatch, host, isolated):
    module = importlib.import_module(f'cgi_pipeline.hosts.{host}.worker')
    worker_type = module.MayaWorker if host == 'maya' else module.BlenderWorker
    monkeypatch.setattr(module, 'PROJECT_ROOT', tmp_path / 'legacy')
    if isolated:
        for key, value in runtime_env(tmp_path / 'slot').items():
            monkeypatch.setenv(key, value)
    process = fake_process_identity(monkeypatch)
    launches = []
    def spawn(cmd, **kwargs):
        if isolated:
            intent = tmp_path / 'slot' / 'host_launches' / f'{worker.worker_id}.json'
            assert json.loads(intent.read_text())['state'] == 'PENDING'
        launches.append((cmd, kwargs))
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(module.os.path, 'exists', lambda path: True)
    worker = worker_type()
    if isolated:
        monkeypatch.setenv('CGI_RUNTIME_ID', 'different-slot-after-construction')
        monkeypatch.setenv('CGI_RUNTIME_ROOT', str(tmp_path / 'different-slot'))
    worker.start()
    command, launch = launches[0]
    root = tmp_path / ('slot' if isolated else 'legacy')
    assert Path(worker._stderr_log.name).parent == root / 'logs'
    assert launch['env']['CGI_PROJECT_ROOT'] == str(tmp_path / 'legacy')
    assert launch['env']['PYTHONPATH'].split(os.pathsep)[0] == str(SOURCE_ROOT)
    if isolated:
        assert launch['env']['CGI_RUNTIME_ID'] == 'slot-a'
        assert Path(launch['env']['CGI_RUNTIME_ROOT']) == root
    if host == 'blender':
        assert ('--threads' in command) is isolated
        if isolated:
            assert command[command.index('--threads') + 1] == '2'
    assert (root / 'hosts' / f'{worker.worker_id}.json').exists() is isolated
    worker._stderr_log.close()


@pytest.mark.parametrize('host', ['maya', 'blender'])
def test_failed_ownership_record_stops_only_newly_spawned_host(tmp_path, monkeypatch, host):
    module = importlib.import_module(f'cgi_pipeline.hosts.{host}.worker')
    worker_type = module.MayaWorker if host == 'maya' else module.BlenderWorker
    for key, value in runtime_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    process = SimpleNamespace(pid=1234, poll=lambda: None)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(module.os.path, 'exists', lambda path: True)
    def fail(*args):
        raise OSError('ownership write refused')
    monkeypatch.setattr(module, 'record_host_process', fail)
    worker, killed = worker_type(), []
    monkeypatch.setattr(worker, '_kill_process_tree', lambda: killed.append(worker.process.pid))
    with pytest.raises(OSError, match='ownership write refused'):
        worker.start()
    assert killed == [1234]
    assert worker._stderr_log.closed
