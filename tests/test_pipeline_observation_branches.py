"""Behavior checks for existing observation entry's desktop and minimized branches."""
from pathlib import Path
import types
import sys

from PIL import Image
import pytest

from cgi_pipeline.contracts import ApiContext
from cgi_pipeline.capabilities.pipeline.window.capture import handler
from cgi_pipeline.capabilities.pipeline.window.discover import handler as discovery
from test_pipeline_window_capture import FakeWindows, PARAMS, TARGET


MONITORS = [
    {'index': 1, 'handle': 41, 'device': 'display-a', 'primary': True,
     'rect': {'left': 0, 'top': 0, 'right': 12, 'bottom': 8}},
    {'index': 2, 'handle': 42, 'device': 'display-b', 'primary': False,
     'rect': {'left': -4, 'top': -2, 'right': 0, 'bottom': 8}},
]


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    from cgi_pipeline.capabilities.pipeline.window import displays
    windows = FakeWindows()
    monkeypatch.setattr(handler, 'NativeWindows', lambda: windows)
    monkeypatch.setattr(discovery, 'NativeWindows', lambda: windows)
    monkeypatch.setattr(displays, 'enumerate_monitors', lambda _: MONITORS)
    monkeypatch.setattr(handler, 'is_protected_path', lambda _: False)

    def worker(request, timeout):
        captures = []
        for target in request['targets']:
            rect = target['monitor']['rect']
            width, height = rect['right'] - rect['left'], rect['bottom'] - rect['top']
            color = (255, 0, 0, 255) if target['monitor']['index'] == 1 else (0, 255, 0, 255)
            Image.new('RGBA', (width, height), color).save(target['output_path'])
            captures.append({'width': width, 'height': height, 'output_path': target['output_path'],
                             'monitor': target['monitor'], 'acquired_at': '2026-10-22T00:00:00+00:00',
                             'timespan_qpc_100ns': 100})
        return {'status': 'SUCCESS', 'captures': captures, 'backend': 'windows-graphics-capture'}

    monkeypatch.setattr(handler, 'run_worker', worker)
    return windows, ApiContext(executor='pipeline', run_dir=str(tmp_path))


def test_discovery_exposes_same_native_display_selection(desktop):
    result = discovery.execute({'include_monitors': True}, desktop[1])
    assert result['status'] == 'SUCCESS'
    assert result['output']['monitors'] == MONITORS


def test_desktop_preserves_negative_origin_portrait_and_transparent_gaps(desktop):
    result = handler.execute({'scope': 'desktop'}, desktop[1])
    assert result['status'] == 'SUCCESS', result
    output = result['output']
    assert output['desktop_rect'] == {'left': -4, 'top': -2, 'right': 12, 'bottom': 8}
    assert len(output['captures']) == 2 and len(result['artifacts']) == 3
    assert output['simultaneous'] is False
    with Image.open(output['output_path']) as image:
        assert image.size == (16, 10)
        assert image.getpixel((0, 0)) == (0, 255, 0, 255)
        assert image.getpixel((4, 2)) == (255, 0, 0, 255)
        assert image.getpixel((4, 0))[3] == 0


def test_selected_monitor_can_skip_composition(desktop):
    result = handler.execute({'scope': 'desktop', 'monitor_indices': [2], 'combine': False}, desktop[1])
    assert result['status'] == 'SUCCESS', result
    assert len(result['artifacts']) == 1
    assert result['output']['captures'][0]['monitor']['index'] == 2


@pytest.mark.parametrize('params', [
    {'scope': 'unknown'}, {'scope': 'desktop', 'monitor_indices': [9]},
    {'scope': 'desktop', 'monitor_indices': [1, 1]}, {'scope': 'desktop', 'combine': 'yes'},
    {'scope': 'desktop', 'monitor_indices': [True]}, {'scope': 'desktop', 'monitor_indices': []},
])
def test_bad_display_selection_has_no_success_or_artifacts(desktop, params):
    result = handler.execute(params, desktop[1])
    assert result['status'] == 'ERROR' and not result['artifacts']


def test_topology_change_after_worker_cannot_publish_success(desktop, monkeypatch):
    from cgi_pipeline.capabilities.pipeline.window import displays
    calls = []

    def enumerate_now(_):
        calls.append(1)
        return MONITORS if len(calls) == 1 else [MONITORS[0]]

    monkeypatch.setattr(displays, 'enumerate_monitors', enumerate_now)
    result = handler.execute({'scope': 'desktop'}, desktop[1])
    assert result['status'] == 'ERROR' and result['artifacts'] == []


def test_restore_guard_reminimizes_after_capture_exception(monkeypatch):
    from cgi_pipeline.capabilities.pipeline.window import capture_state
    windows = FakeWindows([dict(TARGET, minimized=True)])
    calls = []

    class Placement:
        def __init__(self, native, hwnd): pass
        def read(self): return {'show_cmd': 2, 'flags': 0, 'normal': [1, 2, 13, 10], 'min': [0, 0], 'max': [0, 0]}
        def previous(self): return 77
        def show(self, previous):
            calls.append('show')
            windows.rows = [TARGET]
        def restore(self, snapshot, previous):
            calls.append('restore')
            windows.rows = [dict(TARGET, minimized=True)]

    monkeypatch.setattr(capture_state, 'Placement', Placement)
    guard = capture_state.TemporaryRestore(windows, PARAMS, 'restore')
    with pytest.raises(RuntimeError, match='capture failed'):
        with guard:
            raise RuntimeError('capture failed')
    assert calls == ['show', 'restore']
    assert guard.details['restored'] is True


def test_default_minimized_policy_never_restores(monkeypatch, tmp_path):
    windows = FakeWindows([dict(TARGET, minimized=True)])
    monkeypatch.setattr(handler, 'NativeWindows', lambda: windows)
    monkeypatch.setattr(handler, 'run_worker', lambda *_: pytest.fail('minimized default must not capture'))
    result = handler.execute(PARAMS, ApiContext(executor='pipeline', run_dir=str(tmp_path)))
    assert result['status'] == 'ERROR' and not result['artifacts']


def test_settling_retains_latest_frame_without_concurrent_wait_stop(monkeypatch, tmp_path):
    import numpy as np
    from cgi_pipeline.capabilities.pipeline.window import capture_worker

    windows, events, calls = FakeWindows(), {}, []
    monkeypatch.setattr(capture_worker, 'NativeWindows', lambda: windows)

    class Capture:
        def __init__(self, **kwargs): pass
        def event(self, function): events[function.__name__] = function; return function
        def start_free_threaded(self):
            for value in (12, 34):
                events['on_frame_arrived'](types.SimpleNamespace(
                    frame_buffer=np.full((8, 12, 4), [value, 0, 0, 255], dtype=np.uint8),
                    width=12, height=8, timespan=value), types.SimpleNamespace(stop=lambda: calls.append('internal')))
            return types.SimpleNamespace(stop=lambda: calls.append('stop'), wait=lambda: pytest.fail('settle must not borrow wait'))

    monkeypatch.setitem(sys.modules, 'windows_capture', types.SimpleNamespace(WindowsCapture=Capture))
    path = tmp_path / 'latest.png'
    result = capture_worker.capture(dict(PARAMS, output_path=str(path), settle_ms=1))
    assert calls == ['stop'] and result['frame_count'] == 2 and result['timespan_qpc_100ns'] == 34
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == (0, 0, 34, 255)


def test_user_activation_prevents_restoring_over_user_action(monkeypatch):
    from cgi_pipeline.capabilities.pipeline.window import capture_state

    windows = FakeWindows([dict(TARGET, minimized=True)])
    calls = []

    class Placement:
        def __init__(self, native, hwnd): pass
        def read(self): return {'normal': [1, 2, 13, 10]}
        def previous(self): return 77
        def show(self, previous): windows.rows = [TARGET]
        def restore(self, snapshot, previous): calls.append('restore')

    monkeypatch.setattr(capture_state, 'Placement', Placement)
    guard = capture_state.TemporaryRestore(windows, PARAMS, 'restore')
    with pytest.raises(RuntimeError, match='preserve user action'):
        with guard:
            windows.foregrounds = [TARGET['hwnd']]
    assert calls == [] and guard.details['restored'] is False


def test_restore_failure_keeps_original_worker_cleanup_evidence(monkeypatch):
    from cgi_pipeline.capabilities.pipeline.window import capture_state

    windows = FakeWindows([dict(TARGET, minimized=True)])

    class Placement:
        def __init__(self, native, hwnd): pass
        def read(self): return {'normal': [1, 2, 13, 10]}
        def previous(self): return 77
        def show(self, previous): windows.rows = [TARGET]
        def restore(self, snapshot, previous): raise OSError('placement refused')

    monkeypatch.setattr(capture_state, 'Placement', Placement)
    guard = capture_state.TemporaryRestore(windows, PARAMS, 'restore')
    with pytest.raises(RuntimeError) as caught:
        with guard:
            raise handler.WorkerTimeout('worker did not exit', {'worker_pid': 4321, 'worker_exited': False})
    assert caught.value.worker_metadata == {'worker_pid': 4321, 'worker_exited': False}
    assert guard.details['restored'] is False
