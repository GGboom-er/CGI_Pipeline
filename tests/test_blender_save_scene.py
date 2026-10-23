from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cgi_pipeline.contracts import ApiContext, validate_spec
from cgi_pipeline.execution import execute_api


save_scene = importlib.import_module('cgi_pipeline.capabilities.blender.asset.save_scene.save_scene')


class FakeBpy:
    def __init__(self, scene='', *, background=False):
        self.data = SimpleNamespace(filepath=scene, is_saved=bool(scene), is_dirty=True)
        self.app = SimpleNamespace(background=background)
        self.calls = []
        self.result, self.failure, self.keep_dirty = {'FINISHED'}, None, False
        self.write_file, self.update_path, self.mark_saved = True, True, True
        self.contents = b'BLENDER native scene'
        self.ops = SimpleNamespace(wm=SimpleNamespace(save_as_mainfile=self.save))

    def save(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure:
            raise RuntimeError(self.failure)
        if self.result == {'FINISHED'}:
            if self.write_file:
                Path(kwargs['filepath']).write_bytes(self.contents)
            if self.update_path:
                self.data.filepath = kwargs['filepath']
            self.data.is_saved = self.mark_saved
            self.data.is_dirty = self.keep_dirty
        return self.result


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(save_scene, 'REPOSITORY_ROOT', tmp_path)
    return tmp_path


def context(bpy):
    return ApiContext(executor='blender', extras={'bpy_module': bpy})


def test_canonical_manifest_and_execution_use_standard_save_handler(runtime):
    spec = validate_spec(yaml.safe_load(Path(save_scene.__file__).with_name('capability.yaml').read_text('utf-8')))
    assert (spec['api_id'], spec['version'], spec['capability'], spec['operation']) == (
        'blender.asset.save_scene', '1.0.1', 'scene.file', 'save')
    bpy = FakeBpy()
    target = runtime / 'projects' / 'p' / 'scene.blend'
    receipt = execute_api(spec['api_id'], {'save_path': str(target)},
                          {'execution_mode': 'local', 'host': 'blender', 'bpy_module': bpy},
                          catalog_snapshot=({spec['api_id']: spec}, 'save-test'))
    assert receipt['status'] == 'SUCCESS' and receipt['api_version'] == '1.0.1'
    assert receipt['output']['dirty'] is False
    assert receipt['output']['background'] is False and receipt['output']['clean_state'] == 'CLEAN'
    assert receipt['output']['dirty_basis'] == 'native'
    assert receipt['artifacts'] == [target.as_posix()]
    assert bpy.calls == [{'filepath': target.as_posix(), 'check_existing': False, 'copy': False}]


def test_default_save_versions_current_named_file_without_overwrite(runtime):
    target = runtime / 'runs' / 'r' / 'scene_v001.blend'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'original file')
    bpy = FakeBpy(str(target))
    result = save_scene.execute({}, context(bpy))
    output = result['output']
    assert result['status'] == 'SUCCESS'
    assert output['output_path'] == target.with_name('scene_v002.blend').as_posix()
    assert output['scene'] == output['output_path'] and output['is_saved'] is True
    assert output['dirty'] is False and output['file_size_bytes'] > 0
    assert target.read_bytes() == b'original file'


def test_relative_save_uses_current_scene_directory(runtime):
    directory = runtime / 'projects' / 'p'
    directory.mkdir(parents=True)
    bpy = FakeBpy(str(directory / 'old.blend'))
    result = save_scene.execute({'save_path': 'new.blend'}, context(bpy))
    assert result['status'] == 'SUCCESS'
    assert result['output']['output_path'] == (directory / 'new.blend').as_posix()


@pytest.mark.parametrize('path', ['', 'relative.blend', 'scene.ma', 'scene.blend1'])
def test_unnamed_scene_requires_absolute_native_blend_path(runtime, path):
    bpy = FakeBpy()
    supplied = str(runtime / 'projects' / path) if path in ('scene.ma', 'scene.blend1') else path
    result = save_scene.execute({'save_path': supplied}, context(bpy))
    assert result['status'] == 'ERROR' and bpy.calls == []


@pytest.mark.parametrize('outside_current', [False, True])
def test_current_and_target_must_both_be_managed(runtime, outside_current):
    bpy = FakeBpy(str(runtime / 'outside.blend') if outside_current else '')
    target = runtime / ('projects/new.blend' if outside_current else 'outside.blend')
    result = save_scene.execute({'save_path': str(target)}, context(bpy))
    assert result['status'] == 'BLOCKED' and bpy.calls == []
    assert not target.exists()


def test_protected_target_refuses_before_directory_or_host_write(runtime, monkeypatch):
    bpy = FakeBpy()
    monkeypatch.setattr(save_scene, 'is_protected_path', lambda path: 'protected' in str(path))
    target = runtime / 'projects' / 'protected' / 'new.blend'
    result = save_scene.execute({'save_path': str(target)}, context(bpy))
    assert result['status'] == 'BLOCKED' and bpy.calls == []
    assert not target.parent.exists()


@pytest.mark.parametrize('cancel', [False, True])
def test_save_failure_never_overwrites_or_retries_existing_file(runtime, cancel):
    target = runtime / 'projects' / 'scene.blend'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'keep original')
    bpy = FakeBpy(str(target))
    if cancel:
        bpy.result = {'CANCELLED'}
    else:
        bpy.failure = 'disk write failed'
    result = save_scene.execute({}, context(bpy))
    assert result['status'] == 'ERROR' and len(bpy.calls) == 1
    assert target.read_bytes() == b'keep original'
    assert not target.with_name('scene_v001.blend').exists()


def test_finished_save_with_dirty_scene_reports_needs_attention(runtime):
    bpy = FakeBpy()
    bpy.keep_dirty = True
    target = runtime / 'projects' / 'dirty.blend'
    result = save_scene.execute({'save_path': str(target)}, context(bpy))
    assert result['status'] == 'NEEDS_ATTENTION'
    assert result['error_code'] == 'BLENDER_SAVE_DIRTY'
    assert result['output']['dirty'] is True and target.is_file()


@pytest.mark.parametrize('native_dirty', [False, True])
def test_headless_save_reports_verified_snapshot_without_claiming_clean(runtime, native_dirty):
    bpy = FakeBpy(background=True)
    bpy.keep_dirty = native_dirty
    target = runtime / 'projects' / 'headless.blend'
    result = save_scene.execute({'save_path': str(target)}, context(bpy))
    assert result['status'] == 'SUCCESS'
    output = result['output']
    assert output['save_completed'] is True and output['artifact_verified'] is True
    assert output['dirty'] is native_dirty and output['native_dirty'] is native_dirty
    assert output['clean_state'] == 'UNKNOWN'
    assert output['background'] is True and output['dirty_basis'] == 'native_notifier_unconsumed'
    assert bpy.data.is_dirty is native_dirty and len(bpy.calls) == 1
    assert target.read_bytes() == bpy.contents


@pytest.mark.parametrize('background', ['missing_app', 'missing_background', None, 0, 1, 'false', 'true', []])
@pytest.mark.parametrize('native_dirty', [False, True])
def test_unconfirmed_background_mode_never_claims_clean(runtime, background, native_dirty):
    bpy = FakeBpy(background=background)
    if background == 'missing_app':
        del bpy.app
    elif background == 'missing_background':
        del bpy.app.background
    bpy.keep_dirty = native_dirty
    result = save_scene.execute({'save_path': str(runtime / 'projects' / 'unknown_mode.blend')}, context(bpy))
    assert result['status'] == 'SUCCESS'
    output = result['output']
    assert output['save_completed'] is True and output['artifact_verified'] is True
    assert output['clean_state'] == 'UNKNOWN' and output['background'] is None
    assert output['dirty_basis'] == 'background_mode_unknown'
    assert output['dirty'] is native_dirty and output['native_dirty'] is native_dirty


@pytest.mark.parametrize('failure', ['cancel', 'no_file', 'wrong_path', 'not_saved', 'empty'])
def test_headless_save_still_requires_native_completion_and_actual_artifact(runtime, failure):
    bpy = FakeBpy(background=True)
    bpy.keep_dirty = True
    if failure == 'cancel':
        bpy.result = {'CANCELLED'}
    elif failure == 'no_file':
        bpy.write_file = False
    elif failure == 'wrong_path':
        bpy.update_path = False
    elif failure == 'not_saved':
        bpy.mark_saved = False
    else:
        bpy.contents = b''
    result = save_scene.execute({'save_path': str(runtime / 'projects' / 'invalid.blend')}, context(bpy))
    assert result['status'] == 'ERROR'
    assert not result['output'].get('artifact_verified')
    assert len(bpy.calls) == 1


@pytest.mark.parametrize('missing_file', [False, True])
def test_finished_operator_requires_actual_file_and_matching_current_scene(runtime, missing_file):
    bpy = FakeBpy()
    bpy.write_file, bpy.update_path = not missing_file, missing_file
    result = save_scene.execute({'save_path': str(runtime / 'projects' / 'new.blend')}, context(bpy))
    assert result['status'] == 'ERROR'
    assert result['error_code'] == 'BLENDER_SAVE_VERIFICATION_FAILED'


@pytest.mark.parametrize('empty_file', [False, True])
def test_finished_save_checks_nonempty_file_and_actual_is_saved_flag(runtime, empty_file):
    bpy = FakeBpy()
    if empty_file:
        bpy.contents = b''
    else:
        bpy.mark_saved = False
    result = save_scene.execute({'save_path': str(runtime / 'projects' / 'new.blend')}, context(bpy))
    assert result['status'] == 'ERROR'
    assert result['error_code'] == 'BLENDER_SAVE_VERIFICATION_FAILED'
