"""Exercise automatic open scripts through the real exec handlers without DCCs."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from cgi_pipeline.core import dcc_factory, scene_session, workspace
from cgi_pipeline.capabilities.blender.asset.blender_exec_code import blender_exec_code
from cgi_pipeline.capabilities.maya.asset.exec_code import exec_code
from test_workspace import root, begin


def scripted_worker(monkeypatch, host, source='', *, fault=None):
    state = {'path': source, 'value': 'unsaved edit', 'opened': 0, 'new': 0}

    def native_open(path):
        state['opened'] += 1
        if fault == 'corrupt':
            raise RuntimeError('invalid native scene bytes')
        if fault == 'cancelled':
            return {'CANCELLED'}
        state.update(path='wrong-scene' if fault == 'wrong' else path, value='disk scene')
        return {'FINISHED'}

    def reset():
        state.update(path='', value='new scene', new=state['new'] + 1)
        return {'FINISHED'}

    if host == 'blender':
        bpy = ModuleType('bpy')
        class SceneData:
            @property
            def filepath(self):
                return state['path']
        bpy.data = SceneData()
        bpy.ops = SimpleNamespace(wm=SimpleNamespace(
            open_mainfile=lambda *, filepath, load_ui: native_open(filepath),
            read_factory_settings=lambda *, use_empty: reset()))
        monkeypatch.setitem(sys.modules, 'bpy', bpy)
        handler = blender_exec_code.execute
    else:
        maya, cmds = ModuleType('maya'), ModuleType('maya.cmds')
        def scene_file(path=None, **kwargs):
            if kwargs.get('q'):
                return state['path']
            if kwargs.get('new'):
                reset()
            elif kwargs.get('open'):
                native_open(path)
            return state['path']
        cmds.file = scene_file
        maya.cmds = cmds
        monkeypatch.setitem(sys.modules, 'maya', maya)
        monkeypatch.setitem(sys.modules, 'maya.cmds', cmds)
        handler = exec_code.execute

    class Worker:
        worker_id = 'open-test'
        process = None
        _scene_workspace_id = 'old-workspace'

        def run_api(self, payload):
            receipt = handler({'parameters': payload['api_params']})
            return {'status': receipt['status'], 'detail': json.dumps(receipt)}

    return Worker(), state


@pytest.mark.parametrize('host,ext', [('blender', '.blend'), ('maya', '.ma')])
def test_explicit_open_reloads_identical_path(monkeypatch, tmp_path, host, ext):
    source = tmp_path / ('native' + ext)
    source.write_bytes(b'fixture native bytes')
    worker, state = scripted_worker(monkeypatch, host, str(source))
    for task_id in ('first', 'second'):
        state['value'] = 'unsaved edit'
        assert dcc_factory.open_source_file(worker, task_id, str(source), host) == (True, '')
        assert state['value'] == 'disk scene'
    assert state['opened'] == 2
    assert worker._scene_workspace_id is None


@pytest.mark.parametrize('host,ext', [('blender', '.blend'), ('maya', '.ma')])
@pytest.mark.parametrize('fault', ['corrupt', 'wrong'])
def test_native_open_failure_cannot_become_success(monkeypatch, tmp_path, host, ext, fault):
    source = tmp_path / ('native' + ext)
    source.write_bytes(b'invalid or stub bytes')
    worker, state = scripted_worker(monkeypatch, host, fault=fault)
    ok, error = dcc_factory.open_source_file(worker, 'open', str(source), host)
    assert ok is False
    assert error
    assert state['opened'] == 1
    assert worker._scene_workspace_id is None


def test_blender_cancelled_operator_is_not_opened(monkeypatch, tmp_path):
    source = tmp_path / 'cancelled.blend'
    source.write_bytes(b'fixture')
    worker, _ = scripted_worker(monkeypatch, 'blender', fault='cancelled')
    ok, error = dcc_factory.open_source_file(worker, 'open', str(source), 'blender')
    assert ok is False and error


@pytest.mark.parametrize('host,ext', [('blender', '.blend'), ('maya', '.ma')])
@pytest.mark.parametrize('empty', [False, True])
def test_source_missing_after_staging_or_empty_fails(monkeypatch, tmp_path, host, ext, empty):
    import time
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    source = tmp_path / ('native' + ext)
    if empty:
        source.write_bytes(b'')
    worker, state = scripted_worker(monkeypatch, host)
    ok, error = dcc_factory.open_source_file(worker, 'open', str(source), host)
    assert ok is False and error
    assert state['opened'] == 0


@pytest.mark.parametrize('host', ['blender', 'maya'])
@pytest.mark.parametrize('source', ['', 'model.abc', 'model.fbx', 'model.usd'])
def test_empty_and_non_native_source_initialize_fresh_scene(monkeypatch, host, source):
    worker, state = scripted_worker(monkeypatch, host, 'old-native-scene')
    assert dcc_factory.open_source_file(worker, 'new', source, host) == (True, '')
    assert state['new'] == 1 and state['opened'] == 0
    assert state['path'] == ''


@pytest.mark.parametrize('response', [
    None, [], {'status': 'SUCCESS'}, {'status': 'ERROR', 'detail': 'adapter failed'},
    {'status': 'SUCCESS', 'detail': 'not json'},
    {'status': 'SUCCESS', 'detail': None},
    {'status': 'SUCCESS', 'detail': {'status': 'ERROR', 'error': 'open failed'}},
    {'status': 'SUCCESS', 'detail': {'status': 'SUCCESS', 'output': {}}},
    {'status': 'SUCCESS', 'detail': {'status': 'SUCCESS', 'output': {'result': []}}},
    {'status': 'SUCCESS', 'detail': {'status': 'SUCCESS', 'output': {'result': {
        'status': 'SUCCESS', 'operation': 'other', 'dcc': 'blender', 'scene': '<new_scene>'}}}},
    {'status': 'SUCCESS', 'detail': {'status': 'SUCCESS', 'output': {'result': {
        'status': 'SUCCESS', 'operation': 'open_source_file', 'dcc': 'maya', 'scene': '<new_scene>'}}}},
])
def test_open_owner_rejects_incomplete_or_unrelated_success(response):
    worker = SimpleNamespace(run_api=lambda payload: response)
    ok, error = dcc_factory.open_source_file(worker, 'open', '', 'blender')
    assert ok is False and error


@pytest.mark.parametrize('encoded', [False, True])
def test_open_owner_accepts_only_confirmed_result(encoded):
    receipt = {'status': 'SUCCESS', 'output': {'result': {
        'status': 'SUCCESS', 'operation': 'open_source_file', 'dcc': 'blender', 'scene': '<new_scene>'}}}
    worker = SimpleNamespace(run_api=lambda payload: {
        'status': 'SUCCESS', 'detail': json.dumps(receipt) if encoded else receipt})
    assert dcc_factory.open_source_file(worker, 'open', '', 'blender') == (True, '')


def test_corrupt_native_scene_never_records_ready_binding(root, monkeypatch):
    source = root / 'corrupt.blend'
    source.write_bytes(b'invalid blend')
    payload = begin('failed-open', scene_action='open', source_path=str(source))
    worker, _ = scripted_worker(monkeypatch, 'blender', fault='corrupt')
    with pytest.raises(workspace.WorkspaceError, match='invalid native scene bytes') as raised:
        scene_session.prepare_scene(payload, 'blender', lambda *a, **kw: worker,
                                    dcc_factory.open_source_file, lambda *a: None)
    assert raised.value.code == 'WORKSPACE_SCENE_OPEN_FAILED'
    binding = workspace.get_scene_binding(payload['run_dir'], 'blender')
    assert binding['state'] == 'INITIALIZING'
    assert worker._scene_workspace_id is None
    assert 'scene_session' not in payload


@pytest.mark.parametrize('handler', [exec_code.execute, blender_exec_code.execute])
def test_generic_exec_nested_status_semantics_are_unchanged(handler):
    result = handler({'parameters': {'code': "result = {'status': 'ERROR'}"}})
    assert result['status'] == 'SUCCESS'
    assert result['output']['result']['status'] == 'ERROR'
