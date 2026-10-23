"""The original capture owner publishes grounding only for a stable real capture."""

import copy
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cgi_pipeline.capabilities import maya as maya_capabilities
from cgi_pipeline.capabilities.maya.scene_observation import require_main_thread
from cgi_pipeline.capabilities.maya.asset.maya_capture_viewport import (
    maya_capture_viewport as capture,
)
from test_maya_capture_observation import FakeCmds


@pytest.fixture
def grounded_capture(tmp_path, monkeypatch):
    fake = FakeCmds()
    fake.refresh_calls = []
    fake.refresh = lambda **kwargs: fake.refresh_calls.append(kwargs)
    maya = types.ModuleType('maya')
    maya.__path__ = []
    maya.cmds = fake
    monkeypatch.setitem(sys.modules, 'maya', maya)
    monkeypatch.setitem(sys.modules, 'maya.cmds', fake)
    monkeypatch.setattr(capture, 'REPOSITORY_ROOT', tmp_path)
    monkeypatch.setattr(capture, '_decoded_dimensions', lambda path: (3, 2))
    calls = []
    state = {'session': {'scene_epoch': 0}, 'view': {'panel': 'modelPanel4'},
             'targets': [{'path': '|hair', 'uuid': 'hair-id'}],
             'meshes': [{'path': '|hair|shape', 'geometry_sha256': 'original'}],
             'counts': {'unsupported_mesh_count': 2}, 'scope': 'bounded geometry scope',
             'limits': {'max_meshes': 1000, 'max_vertices': 250000}}

    def snapshot(cmds, panel, targets, **limits):
        assert fake.refresh_calls == [{'force': True}]
        calls.append(('snapshot', len(fake.playblast_calls)))
        assert panel == 'modelPanel4' and targets == ['|hair']
        return copy.deepcopy(state)

    def publish(observed, path, width, height):
        calls.append(('publish', len(fake.playblast_calls)))
        assert Path(path).is_file() and (width, height) == (3, 2)
        return {'path': str(path) + '.json', 'sha256': 'a' * 64}

    helper = types.SimpleNamespace(snapshot=snapshot, publish=publish,
                                   require_main_thread=require_main_thread)
    monkeypatch.setitem(sys.modules, 'cgi_pipeline.capabilities.maya.scene_observation', helper)
    monkeypatch.setattr(maya_capabilities, 'scene_observation', helper, raising=False)
    return fake, state, calls


def request(**overrides):
    return {'parameters': {'scope': 'viewport', 'panel': 'modelPanel4',
                           'grounding': True, 'targets': ['|hair'], **overrides}}


def test_grounding_snapshots_surround_capture_and_reference_publishes_last(grounded_capture):
    fake, state, calls = grounded_capture
    result = capture.execute(request())
    assert result['status'] == 'SUCCESS', result
    assert calls == [('snapshot', 0), ('snapshot', 1), ('publish', 1)]
    grounding = result['output']['grounding']
    assert grounding['targets'] == state['targets']
    assert grounding['reference']['sha256'] == 'a' * 64
    assert grounding['appearance_unsupported_mesh_count'] == 2
    assert grounding['scope'] == state['scope']
    assert fake.playblast_calls[0]['editorPanelName'] == 'modelPanel4'


def test_same_topology_geometry_change_during_capture_never_publishes(grounded_capture):
    fake, state, calls = grounded_capture
    fake.after_capture = lambda _cmds: state['meshes'][0].update(geometry_sha256='deformed')
    result = capture.execute(request())
    assert result['status'] == 'ERROR'
    assert 'geometry or view changed' in result['error']
    assert calls == [('snapshot', 0), ('snapshot', 1)]
    assert 'grounding' not in result['output']
    assert Path(result['output']['output_path']).exists()


@pytest.mark.parametrize('changes', [
    {'scope': 'window', 'panel': ''}, {'panel': ''}, {'grounding': 'true'},
    {'grounding': False},
])
def test_grounding_invalid_scope_or_implicit_panel_fails_before_capture(grounded_capture, changes):
    fake, state, calls = grounded_capture
    result = capture.execute(request(**changes))
    assert result['status'] == 'ERROR'
    assert fake.playblast_calls == [] and calls == []


def test_capture_failure_has_no_valid_grounding_reference(grounded_capture):
    fake, state, calls = grounded_capture
    fake.capture_failure = RuntimeError('injected GPU capture failure')
    result = capture.execute(request())
    assert result['status'] == 'ERROR'
    assert calls == [('snapshot', 0)]
    assert 'grounding' not in result['output']


def test_grounded_capture_off_main_thread_touches_no_maya(grounded_capture):
    fake, state, calls = grounded_capture
    queries = []

    def forbidden(**kwargs):
        queries.append(kwargs)
        raise AssertionError('Maya must not be queried off the main thread')

    fake.about = forbidden
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(capture.execute, request()).result()
    assert result['status'] == 'ERROR' and 'main thread' in result['error']
    assert queries == [] and calls == [] and fake.playblast_calls == []
