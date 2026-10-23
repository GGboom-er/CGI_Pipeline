"""Regression coverage for automatic capability code reload in warm DCC hosts.

capability 代码在 DCC 进程内执行，warm pool 复用该进程；进程只在首次 import
时读文件，因此复用前必须按源码指纹判断是否需要重载（lesson L118）。
生产源码不变则指纹不变，永不触发重载。
"""

from __future__ import annotations

import sys
import subprocess
import textwrap

import pytest

from cgi_pipeline.core import code_reload, dcc_factory


def test_foreground_refreshes_changed_dependency_without_resetting_host(tmp_path):
    script = textwrap.dedent('''
        import importlib, os, sys, types
        from pathlib import Path
        import cgi_pipeline
        from cgi_pipeline import catalog
        from cgi_pipeline.core.code_reload import capability_session
        published = ({}, 'v1')
        catalog.snapshot = lambda: published
        root = Path(sys.argv[1])
        package = root / 'capabilities'
        package.mkdir()
        (package / '__init__.py').write_text('')
        leaf = package / 'leaf.py'
        leaf.write_text('VALUE = 1\\n')
        (package / 'consumer.py').write_text('from .leaf import VALUE\\n')
        cgi_pipeline.__path__.insert(0, str(root))
        host = types.ModuleType('maya.cmds')
        sys.modules['maya.cmds'] = host
        with capability_session() as snapshot:
            assert snapshot is published
            first = importlib.import_module('cgi_pipeline.capabilities.consumer')
            assert first.VALUE == 1
        with capability_session():
            assert importlib.import_module('cgi_pipeline.capabilities.consumer') is first
        stamp = leaf.stat()
        leaf.write_text('VALUE = 2\\n')
        os.utime(leaf, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        # Unpublished changes are not a release and must not trigger a reload.
        with capability_session():
            assert importlib.import_module('cgi_pipeline.capabilities.consumer') is first
        published = ({}, 'v2')
        with capability_session():
            second = importlib.import_module('cgi_pipeline.capabilities.consumer')
            assert second is not first and second.VALUE == 2
            leaf.write_text('VALUE = 3  # changed during execution\\n')
            published = ({}, 'v3')
            assert importlib.import_module('cgi_pipeline.capabilities.consumer') is second
        with capability_session():
            assert importlib.import_module('cgi_pipeline.capabilities.consumer').VALUE == 3
        assert sys.modules['maya.cmds'] is host
        leaf.unlink()
        published = ({}, 'v4')
        try:
            with capability_session():
                importlib.import_module('cgi_pipeline.capabilities.consumer')
                raise AssertionError('ran with missing loaded source')
        except ModuleNotFoundError:
            pass
        print('PASS')
    ''')
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS' in result.stdout


def test_fingerprint_is_stable_when_source_unchanged():
    """源码没动就必须给出同一指纹，否则生产会被无谓重载。"""
    assert code_reload.source_fingerprint() == code_reload.source_fingerprint()


def test_fingerprint_changes_when_a_source_file_is_touched(tmp_path, monkeypatch):
    root = tmp_path / 'cgi_pipeline'
    (root / 'capabilities').mkdir(parents=True)
    (root / 'core').mkdir(parents=True)
    target = root / 'capabilities' / 'thing.py'
    target.write_text('x = 1', encoding='utf-8')
    monkeypatch.setattr(code_reload, '_source_root', lambda: str(root))

    before = code_reload.source_fingerprint()
    target.write_text('x = 2  # edited', encoding='utf-8')
    assert code_reload.source_fingerprint() != before


def test_fingerprint_ignores_pycache(tmp_path, monkeypatch):
    root = tmp_path / 'cgi_pipeline'
    cache = root / 'capabilities' / '__pycache__'
    cache.mkdir(parents=True)
    (root / 'capabilities' / 'thing.py').write_text('x = 1', encoding='utf-8')
    monkeypatch.setattr(code_reload, '_source_root', lambda: str(root))

    before = code_reload.source_fingerprint()
    (cache / 'thing.cpython-311.pyc').write_bytes(b'\x00\x01')
    assert code_reload.source_fingerprint() == before


def test_evict_drops_first_party_modules_only(monkeypatch):
    """剔除只针对第一方模块，宿主状态不动。

    真剔除会把已导入的 cgi_pipeline.* 从 sys.modules 拿掉，污染同进程后续测试，
    所以这里在 sys.modules 的副本上验证选择逻辑，不动真的解释器状态。
    """
    import types

    fake_modules = {
        'cgi_pipeline.capabilities.fake': types.ModuleType('fake'),
        'cgi_pipeline.core.thing': types.ModuleType('thing'),
        code_reload.__name__: sys.modules[code_reload.__name__],
        'bpy': types.ModuleType('bpy'),
        'maya.cmds': types.ModuleType('cmds'),
    }
    monkeypatch.setattr(code_reload, 'sys', types.SimpleNamespace(modules=fake_modules))

    evicted = code_reload.evict_capability_modules()

    assert evicted == 2
    assert 'cgi_pipeline.capabilities.fake' not in fake_modules
    assert 'cgi_pipeline.core.thing' not in fake_modules
    # 宿主自身状态绝不能被剔除
    assert 'bpy' in fake_modules
    assert 'maya.cmds' in fake_modules
    # 本模块正在执行，保留
    assert code_reload.__name__ in fake_modules


class _FakeWorker:
    def __init__(self):
        self.worker_id = 'fake'
        self.calls = []
        self.payloads = []
        self.shutdown_calls = 0
        self.fail = False

    def run_api(self, payload):
        self.calls.append(payload.get('api_id'))
        self.payloads.append(payload)
        if self.fail:
            raise RuntimeError('ipc timeout')
        return {'status': 'SUCCESS'}

    def shutdown(self):
        self.shutdown_calls += 1


def test_sync_skips_reload_when_fingerprint_matches(monkeypatch):
    monkeypatch.setattr(code_reload, 'source_fingerprint', lambda: 'same')
    worker = _FakeWorker()
    entry = {'worker': worker, 'task_count': 1, 'code_fingerprint': 'same'}

    dcc_factory._sync_worker_code(entry, 'blender')

    assert worker.calls == []


def test_sync_sends_reload_when_source_changed(monkeypatch):
    monkeypatch.setattr(code_reload, 'source_fingerprint', lambda: 'new')
    worker = _FakeWorker()
    entry = {'worker': worker, 'task_count': 1, 'code_fingerprint': 'old'}

    dcc_factory._sync_worker_code(entry, 'blender')

    assert worker.calls == [code_reload.RELOAD_COMMAND]
    assert entry['code_fingerprint'] == 'new'


def test_reload_task_id_is_filename_safe_and_bounded(monkeypatch):
    """task_id 会被当文件名用；含 ':' 等非法字符会让 os.replace 报 WinError 87。

    指纹本身形如 '175:1788268066.257:934623'，直接拿来做 task_id 就会踩这个坑。
    同时必须带 timeout：IPC_TIMEOUT 默认 0 表示永不超时，宿主异常时整链卡死。
    """
    monkeypatch.setattr(code_reload, 'source_fingerprint', lambda: '175:1788268066.257:934623')
    worker = _FakeWorker()
    entry = {'worker': worker, 'task_count': 1, 'code_fingerprint': 'old'}

    dcc_factory._sync_worker_code(entry, 'blender')

    payload = worker.payloads[-1]
    task_id = payload['task_id']
    assert not set(task_id) & set(':<>"/\\|?*'), f"unsafe task_id: {task_id}"
    assert payload.get('timeout'), "reload must bound its wait"


def test_sync_recycles_process_when_reload_fails(monkeypatch):
    """重载失败不能静默降级成跑旧代码——必须关掉进程。"""
    monkeypatch.setattr(code_reload, 'source_fingerprint', lambda: 'new')
    worker = _FakeWorker()
    worker.fail = True
    entry = {'worker': worker, 'task_count': 1, 'code_fingerprint': 'old'}
    monkeypatch.setitem(dcc_factory._warm_pool, 'blender', entry)

    with pytest.raises(RuntimeError):
        dcc_factory._sync_worker_code(entry, 'blender')

    assert worker.shutdown_calls == 1
    assert 'blender' not in dcc_factory._warm_pool


def test_shutdown_keeps_process_below_recycle_threshold(monkeypatch):
    worker = _FakeWorker()
    monkeypatch.setitem(
        dcc_factory._warm_pool, 'blender', {'worker': worker, 'task_count': 1}
    )
    proxy = dcc_factory.WarmWorkerProxy.__new__(dcc_factory.WarmWorkerProxy)
    proxy.dcc_type, proxy.real_worker = 'blender', worker

    proxy.shutdown()

    assert worker.shutdown_calls == 0
    assert 'blender' in dcc_factory._warm_pool


def test_shutdown_still_recycles_at_threshold(monkeypatch):
    worker = _FakeWorker()
    monkeypatch.setitem(
        dcc_factory._warm_pool,
        'maya',
        {'worker': worker, 'task_count': dcc_factory._WARM_POOL_RECYCLE_AFTER + 1},
    )
    proxy = dcc_factory.WarmWorkerProxy.__new__(dcc_factory.WarmWorkerProxy)
    proxy.dcc_type, proxy.real_worker = 'maya', worker

    proxy.shutdown()

    assert worker.shutdown_calls == 1
    assert 'maya' not in dcc_factory._warm_pool
