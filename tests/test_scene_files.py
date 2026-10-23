from __future__ import annotations

import builtins
import errno
import multiprocessing
from pathlib import Path
import runpy

import pytest

from cgi_pipeline.core.scene_files import directory_lock, is_managed_path, next_version_path
from cgi_pipeline.core import scene_files
from cgi_pipeline.paths import SOURCE_ROOT


def _allocate(directory, ready, start, results):
    try:
        ready.put(True)
        if not start.wait(10):
            raise TimeoutError('parent did not release start barrier')
        with directory_lock(directory, timeout=10):
            target = Path(next_version_path(str(Path(directory) / 'scene.blend')))
            target.write_bytes(b'owned new scene')
        results.put(('ok', target.name))
    except Exception as exc:
        results.put(('error', repr(exc)))


def _hold(directory, ready, release):
    with directory_lock(directory, timeout=10):
        ready.set()
        release.wait(10)


def test_helper_import_is_host_safe_without_external_packages(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split('.')[0] in ('maya', 'bpy', 'filelock', 'psutil', 'dotenv', 'celery'):
            raise ImportError(name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    namespace = runpy.run_path(str(SOURCE_ROOT / 'cgi_pipeline' / 'core' / 'scene_files.py'))
    assert callable(namespace['directory_lock'])


@pytest.mark.parametrize('name,occupied,expected', [
    ('scene.blend', [], 'scene.blend'),
    ('scene.blend', ['scene.blend'], 'scene_v001.blend'),
    ('scene.blend', ['scene.blend', 'scene_v001.blend'], 'scene_v002.blend'),
    ('scene_v009.ma', ['scene_v009.ma', 'scene_v010.ma'], 'scene_v011.ma'),
    ('sceneV09.mb', ['sceneV09.mb'], 'sceneV10.mb'),
])
def test_next_version_preserves_existing_maya_naming(tmp_path, name, occupied, expected):
    for item in occupied:
        (tmp_path / item).write_bytes(b'original')
    assert Path(next_version_path(str(tmp_path / name))).name == expected
    assert all((tmp_path / item).read_bytes() == b'original' for item in occupied)


def test_managed_path_uses_resolved_projects_and_runs_boundary(tmp_path):
    assert is_managed_path(tmp_path / 'projects' / 'p' / 'scene.blend', tmp_path)
    assert is_managed_path(tmp_path / 'runs' / 'run' / 'scene.ma', tmp_path)
    assert not is_managed_path(tmp_path / 'projects-other' / 'scene.blend', tmp_path)
    assert not is_managed_path(tmp_path / 'projects' / '..' / 'outside.blend', tmp_path)
    assert not is_managed_path('', tmp_path)


def test_directory_lock_serializes_real_process_version_allocations(tmp_path):
    original = tmp_path / 'scene.blend'
    original.write_bytes(b'keep original')
    ctx = multiprocessing.get_context('spawn')
    ready, start, results = ctx.Queue(), ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_allocate, args=(str(tmp_path), ready, start, results))
                 for _ in range(4)]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            assert ready.get(timeout=15) is True
        start.set()
        outputs = [results.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert sorted(outputs) == [('ok', f'scene_v{index:03d}.blend') for index in range(1, 5)]
        assert original.read_bytes() == b'keep original'
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(10)


def test_directory_lock_timeout_refuses_then_recovers_after_release(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_hold, args=(str(tmp_path), ready, release))
    process.start()
    try:
        assert ready.wait(15)
        with pytest.raises(TimeoutError, match='version_lock'):
            with directory_lock(tmp_path, timeout=0.1):
                pytest.fail('contended lock must not enter')
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(10)
    assert process.exitcode == 0
    with directory_lock(tmp_path, timeout=0.1):
        assert (tmp_path / '.version_lock').is_file()


def test_directory_lock_propagates_body_failure_and_unlocks(tmp_path):
    with pytest.raises(RuntimeError, match='save failed'):
        with directory_lock(tmp_path):
            raise RuntimeError('save failed')
    with directory_lock(tmp_path, timeout=0.1):
        pass


def test_unavailable_os_lock_refuses_without_running_save(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError(errno.ENOSYS, 'OS lock is unavailable')
    monkeypatch.setattr(scene_files, '_set_lock', unavailable)
    with pytest.raises(OSError, match='unavailable'):
        with directory_lock(tmp_path):
            pytest.fail('there must be no unlocked fallback')
