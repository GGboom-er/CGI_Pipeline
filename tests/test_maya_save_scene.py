from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


class FakeCmds:
    def __init__(self, scene=''):
        self.scene, self.calls, self.failure = scene, [], None

    def file(self, **kwargs):
        if kwargs.get('query'):
            return self.scene
        self.calls.append(kwargs)
        if 'rename' in kwargs:
            self.scene = kwargs['rename']
        if kwargs.get('save'):
            if self.failure:
                raise RuntimeError(self.failure)
            Path(self.scene).write_bytes(b'saved Maya scene')


@pytest.fixture
def maya_save(monkeypatch, tmp_path):
    cmds = FakeCmds()
    bootstrap_cmds = FakeCmds()
    monkeypatch.setitem(sys.modules, 'maya', SimpleNamespace(cmds=bootstrap_cmds))
    monkeypatch.setitem(sys.modules, 'maya.cmds', bootstrap_cmds)
    module = importlib.import_module('cgi_pipeline.capabilities.maya.asset.save_scene.save_scene')
    monkeypatch.setattr(module, 'cmds', cmds)
    monkeypatch.setattr(module, 'REPOSITORY_ROOT', tmp_path)
    return module, cmds


def test_maya_save_keeps_legacy_payload_receipt_and_next_version(tmp_path, maya_save):
    module, cmds = maya_save
    original = tmp_path / 'projects' / 'p' / 'scene_v001.ma'
    original.parent.mkdir(parents=True)
    original.write_bytes(b'original Maya file')
    cmds.scene = str(original)
    result = module.execute({'parameters': {}})
    expected = original.with_name('scene_v002.ma')
    assert result['status'] == 'SUCCESS'
    assert result['output'] == {'output_path': str(expected.resolve())}
    assert cmds.calls == [{'rename': str(expected.resolve())}, {'save': True, 'type': 'mayaAscii'}]
    assert original.read_bytes() == b'original Maya file'


def test_maya_relative_save_and_binary_type_are_unchanged(tmp_path, maya_save):
    module, cmds = maya_save
    directory = tmp_path / 'runs' / 'run'
    directory.mkdir(parents=True)
    cmds.scene = str(directory / 'old.ma')
    result = module.execute({'parameters': {'save_path': 'new.mb'}})
    assert result['status'] == 'SUCCESS'
    assert result['output']['output_path'].replace('\\', '/') == (directory / 'new.mb').as_posix()
    assert cmds.calls[-1] == {'save': True, 'type': 'mayaBinary'}


def test_maya_save_rejects_unnamed_or_outside_current_before_save(tmp_path, maya_save):
    module, cmds = maya_save
    assert module.execute({'parameters': {}})['status'] == 'ERROR'
    cmds.scene = str(tmp_path / 'outside.ma')
    result = module.execute({'parameters': {'save_path': str(tmp_path / 'projects' / 'new.ma')}})
    assert result['status'] == 'BLOCKED' and cmds.calls == []


def test_maya_compatibility_helpers_delegate_to_shared_owner(tmp_path, maya_save, monkeypatch):
    module, _ = maya_save
    monkeypatch.setattr(module, '_next_version_path', lambda path: 'shared-next')
    monkeypatch.setattr(module, '_is_managed_path', lambda path, root: path == 'shared' and root == tmp_path)
    assert module.get_next_version_path('original') == 'shared-next'
    assert module._is_under_project_sandbox('shared') is True


def test_maya_lock_failure_does_not_rename_or_overwrite(tmp_path, maya_save, monkeypatch):
    module, cmds = maya_save
    original = tmp_path / 'projects' / 'scene.ma'
    original.parent.mkdir(parents=True)
    original.write_bytes(b'original')
    cmds.scene = str(original)
    def unavailable(*args, **kwargs):
        raise TimeoutError('shared directory lock timeout')
    monkeypatch.setattr(module, 'directory_lock', unavailable)
    result = module.execute({'parameters': {}})
    assert result['status'] == 'ERROR' and 'lock timeout' in result['error']
    assert cmds.calls == [] and original.read_bytes() == b'original'
