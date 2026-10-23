"""Shared window tools: identity, bounded output, worker lifetime and real PNG QC."""
import ctypes
import importlib
import json
from pathlib import Path
import subprocess
import sys
import types

from PIL import Image
import pytest
import yaml

from cgi_pipeline.contracts import ApiContext, validate_spec, validate_receipt
from cgi_pipeline.capabilities.pipeline.window import capture_worker, native
from cgi_pipeline.capabilities.pipeline.window.capture import handler as capture
from cgi_pipeline.capabilities.pipeline.window.discover import handler as discover

RUN_WORKER_IMPLEMENTATION = capture.run_worker


TARGET = {'hwnd': 2**40 + 1, 'pid': 123, 'process_name': 'maya.exe',
          'process_started_at': 12345.25, 'title': 'Maya', 'class': 'Qt5QWindowIcon',
          'visible': True, 'minimized': False,
          'rect': {'left': 0, 'top': 0, 'right': 12, 'bottom': 8}}
PARAMS = {'hwnd': TARGET['hwnd'], 'expected_pid': TARGET['pid'],
          'expected_process_started_at': TARGET['process_started_at']}


@pytest.fixture(autouse=True)
def worker_windows(monkeypatch):
    monkeypatch.setattr(capture_worker, 'NativeWindows', FakeWindows)


class FakeWindows:
    def __init__(self, rows=None):
        self.rows = rows or [TARGET.copy()]
        self.description_count = 0
        self.foregrounds = [999, 999]
        self.session = {'process_id': 555, 'session_id': 2, 'connect_state': 0,
                        'connect_state_name': 'WTSActive', 'active': True}

    def session_status(self):
        if isinstance(self.session, Exception):
            raise self.session
        return self.session

    def describe(self, hwnd):
        self.description_count += 1
        row = self.rows[min(self.description_count - 1, len(self.rows) - 1)]
        if isinstance(row, Exception):
            raise row
        return dict(row, hwnd=hwnd)

    def foreground(self):
        return self.foregrounds.pop(0) if len(self.foregrounds) > 1 else self.foregrounds[0]

    def enumerate(self, visit):
        for index in range(len(self.rows)):
            if not visit(TARGET['hwnd'] + index):
                break


@pytest.fixture
def rig(tmp_path, monkeypatch):
    windows = FakeWindows()
    monkeypatch.setattr(capture, 'NativeWindows', lambda: windows)
    monkeypatch.setattr(capture, 'is_protected_path', lambda path: False)
    monkeypatch.setattr(discover, 'NativeWindows', lambda: windows)

    def worker(request, timeout):
        assert {key: request[key] for key in PARAMS} == PARAMS
        Image.new('RGBA', (12, 8), (12, 34, 56, 255)).save(request['output_path'])
        return {'status': 'SUCCESS', 'backend': 'windows-graphics-capture', 'width': 12, 'height': 8,
                'timespan_qpc_100ns': 100, 'acquired_at': '2026-10-20T00:00:00+00:00',
                'acquired_monotonic': 10.5, 'frame_pixel_format': 'BGRA8'}

    monkeypatch.setattr(capture, 'run_worker', worker)
    return windows, ApiContext(executor='pipeline', run_dir=str(tmp_path))


def test_capture_has_real_decodable_unique_task_png_and_no_focus_operation(rig):
    windows, context = rig
    first = capture.execute(PARAMS, context)
    second = capture.execute(PARAMS, context)
    assert first['status'] == second['status'] == 'SUCCESS'
    assert first['output']['output_path'] != second['output']['output_path']
    path = Path(first['output']['output_path'])
    assert path.parent == Path(context.run_dir) / 'outputs' / 'captures'
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == (12, 34, 56, 255)
    assert first['output']['target']['hwnd'] == 2**40 + 1
    assert first['output']['foreground_unchanged'] is True
    assert windows.description_count == 4
    validate_receipt(first, api_id='pipeline.window.capture', api_version=capture.VERSION)


def test_concurrent_focus_change_is_reported_without_restore(rig):
    windows, context = rig
    windows.foregrounds = [999, 888]
    result = capture.execute(PARAMS, context)
    assert result['status'] == 'SUCCESS'
    assert result['output']['foreground_unchanged'] is False
    assert result['output']['foreground_hwnd_after'] == 888


@pytest.mark.parametrize('state', [1, 2, 3, 4, 5, 6, 7, 8, 9, 999])
def test_non_active_session_fails_without_worker_or_output_side_effects(rig, monkeypatch, state):
    windows, context = rig
    windows.session = {'process_id': 555, 'session_id': 2, 'connect_state': state,
                       'connect_state_name': native.SESSION_STATES[state] if state < 10 else 'UNKNOWN', 'active': False}
    monkeypatch.setattr(capture, 'run_worker', lambda *_: pytest.fail('inactive session started a worker'))
    monkeypatch.setattr(capture, 'output_path', lambda *_: pytest.fail('inactive session created an output path'))
    result = capture.execute(PARAMS, context)
    assert result['status'] == 'ERROR' and result['error_code'] == 'WINDOW_CAPTURE_SESSION_UNAVAILABLE'
    assert result['output']['session'] == windows.session
    assert result['artifacts'] == [] and windows.description_count == 0
    assert not (Path(context.run_dir) / 'outputs').exists()


def test_unknown_session_query_fails_without_worker_or_output(rig, monkeypatch):
    windows, context = rig
    windows.session = OSError('WTS query unavailable')
    monkeypatch.setattr(capture, 'run_worker', lambda *_: pytest.fail('unknown session started a worker'))
    result = capture.execute(PARAMS, context)
    assert result['error_code'] == 'WINDOW_CAPTURE_SESSION_UNAVAILABLE'
    assert result['output']['session']['active'] is None
    assert result['output']['session']['query_status'] == 'ERROR'
    assert not (Path(context.run_dir) / 'outputs').exists()


def test_discovery_remains_available_in_non_active_or_unknown_session(rig):
    windows, context = rig
    for session in [{'session_id': 2, 'connect_state': 4, 'connect_state_name': 'WTSDisconnected', 'active': False},
                    OSError('state query failed')]:
        windows.session, windows.description_count = session, 0
        result = discover.execute({}, context)
        assert result['status'] == 'SUCCESS' and result['output']['window_count'] == 1
        assert result['output']['session']['active'] is not True


@pytest.mark.parametrize('changes', [{'pid': 456}, {'process_started_at': 999.0},
                                    {'visible': False}, {'minimized': True},
                                    {'rect': {'left': 0, 'right': 0, 'top': 0, 'bottom': 5}}])
def test_changed_or_unusable_target_never_starts_capture(rig, monkeypatch, changes):
    windows, context = rig
    windows.rows = [dict(TARGET, **changes)]
    monkeypatch.setattr(capture, 'run_worker', lambda *_: pytest.fail('must not capture'))
    assert capture.execute(PARAMS, context)['status'] == 'ERROR'


@pytest.mark.parametrize('changes', [{'pid': 456}, {'process_started_at': 999.0}, {'minimized': True}])
def test_identity_is_rechecked_after_worker_exit(rig, changes):
    windows, context = rig
    windows.rows = [TARGET, dict(TARGET, **changes)]
    result = capture.execute(PARAMS, context)
    assert result['status'] == 'ERROR'
    assert result['artifacts'] == []
    assert 'output_path' not in result['output']


@pytest.mark.parametrize('key,value', [('hwnd', True), ('hwnd', 0), ('hwnd', 2**64),
                                      ('expected_pid', -1), ('expected_pid', True),
                                      ('expected_process_started_at', float('nan')),
                                      ('expected_process_started_at', float('inf')),
                                      ('expected_process_started_at', False),
                                      ('timeout_sec', 0), ('timeout_sec', 31),
                                      ('timeout_sec', float('nan')), ('timeout_sec', '8')])
def test_invalid_capture_identity_and_deadline_fail_before_native(rig, monkeypatch, key, value):
    _, context = rig
    monkeypatch.setattr(capture, 'NativeWindows', lambda: pytest.fail('invalid request touched Windows'))
    assert capture.execute(dict(PARAMS, **{key: value}), context)['status'] == 'ERROR'


def test_missing_run_directory_and_protected_output_fail(rig, monkeypatch):
    _, context = rig
    assert capture.execute(PARAMS, ApiContext(executor='pipeline'))['status'] == 'ERROR'
    monkeypatch.setattr(capture, 'is_protected_path', lambda _: True)
    assert capture.execute(PARAMS, context)['status'] == 'ERROR'


def test_resolved_directory_escape_is_refused(rig, monkeypatch):
    _, context = rig
    original = Path.resolve
    directory = Path(context.run_dir) / 'outputs' / 'captures'

    def resolve(path, *args, **kwargs):
        return Path(context.run_dir).parent if path == directory else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', resolve)
    assert capture.execute(PARAMS, context)['status'] == 'ERROR'


@pytest.mark.parametrize('mode', ['empty', 'truncated', 'wrong_dimensions'])
def test_corrupt_or_wrong_png_is_not_success(rig, monkeypatch, mode):
    _, context = rig

    def worker(request, timeout):
        path = Path(request['output_path'])
        if mode == 'wrong_dimensions':
            Image.new('RGB', (1, 1)).save(path)
        else:
            path.write_bytes(b'' if mode == 'empty' else b'\x89PNG\r\n\x1a\n')
        return {'status': 'SUCCESS', 'width': 12, 'height': 8}

    monkeypatch.setattr(capture, 'run_worker', worker)
    result = capture.execute(PARAMS, context)
    assert result['status'] == 'ERROR' and result['artifacts'] == []


def test_timeout_with_existing_png_is_still_timeout(rig, monkeypatch):
    _, context = rig

    def worker(request, timeout):
        Image.new('RGB', (12, 8)).save(request['output_path'])
        raise TimeoutError('stuck in native wait; killed and reaped')

    monkeypatch.setattr(capture, 'run_worker', worker)
    result = capture.execute(PARAMS, context)
    assert result['status'] == 'TIMEOUT' and result['artifacts'] == []
    assert result['error_code'] == 'WINDOW_CAPTURE_TIMEOUT'


class FakeChild:
    def __init__(self, outcome=None, returncode=0):
        self.outcome, self.returncode, self.killed, self.calls = outcome, returncode, False, []
        self.pid = 4321
        self.completed = False

    def communicate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if len(self.calls) == 1 and isinstance(self.outcome, Exception):
            raise self.outcome
        self.completed = True
        return (self.outcome if isinstance(self.outcome, str) else '{"status":"SUCCESS"}', 'x' * 3000)

    def kill(self):
        self.killed, self.returncode = True, -1

    def poll(self):
        return self.returncode if self.killed or self.completed else None


def test_timeout_kills_and_reaps_only_owned_hidden_worker(monkeypatch):
    child = FakeChild(subprocess.TimeoutExpired('capture', 1))
    calls = []

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return child

    monkeypatch.setattr(capture.subprocess, 'Popen', popen)
    with pytest.raises(TimeoutError, match='exited and was reaped'):
        capture.run_worker({'hwnd': TARGET['hwnd']}, 1)
    assert child.killed and len(child.calls) == 2
    assert calls[0][0] == [sys.executable, '-m', 'cgi_pipeline.capabilities.pipeline.window.capture_worker']
    assert calls[0][1]['creationflags'] == getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    assert child.calls[0][1] == {'timeout': 1}
    assert child.calls[1][1] == {'timeout': capture.CLEANUP_TIMEOUT_SEC}


def test_timeout_preserves_bounded_stages_and_reaped_process_metadata(monkeypatch):
    child = FakeChild(subprocess.TimeoutExpired('capture', 1))

    def communicate(*args, **kwargs):
        child.calls.append((args, kwargs))
        if len(child.calls) == 1:
            raise subprocess.TimeoutExpired('capture', 1)
        child.completed = True
        return '', 'discarded-prefix' * 400 + '{"stage":"starting","elapsed_sec":0.02}\n'

    child.communicate = communicate
    monkeypatch.setattr(capture.subprocess, 'Popen', lambda *a, **k: child)
    with pytest.raises(capture.WorkerTimeout) as failure:
        capture.run_worker({}, 1)
    metadata = failure.value.worker_metadata
    assert metadata['worker_pid'] == child.pid and metadata['worker_exited'] is True
    assert metadata['worker_lifecycle_elapsed_sec'] >= 0
    assert len(metadata['worker_diagnostics']) <= 3000
    assert '"stage":"starting"' in metadata['worker_diagnostics']


def test_communication_failure_also_reaps_owned_worker(monkeypatch):
    child = FakeChild(OSError('pipe broke'))
    monkeypatch.setattr(capture.subprocess, 'Popen', lambda *a, **k: child)
    with pytest.raises(capture.WorkerFailure, match='pipe broke'):
        capture.run_worker({}, 1)
    assert child.killed and len(child.calls) == 2


def test_cleanup_wait_is_bounded_and_never_claims_unreaped_worker_exited(rig, monkeypatch):
    _, context = rig
    child = FakeChild()

    def communicate(*args, **kwargs):
        child.calls.append((args, kwargs))
        raise subprocess.TimeoutExpired('capture', kwargs['timeout'], stderr=b'{"stage":"starting"}\n')

    child.communicate = communicate
    child.kill = lambda: None  # Simulate a termination request that does not complete.
    monkeypatch.setattr(capture.subprocess, 'Popen', lambda *a, **k: child)
    monkeypatch.setattr(capture, 'run_worker', RUN_WORKER_IMPLEMENTATION)
    result = capture.execute(PARAMS, context)
    assert result['status'] == 'ERROR' and result['error_code'] == 'WINDOW_CAPTURE_CLEANUP_FAILED'
    assert result['output']['worker_exited'] is False
    assert result['output']['worker_pid'] == child.pid
    assert result['output']['worker_cleanup_error']
    assert 'was reaped' not in result['error']
    assert [entry[1]['timeout'] for entry in child.calls] == [8, capture.CLEANUP_TIMEOUT_SEC]
    assert result['artifacts'] == []


def test_real_timeout_reaps_a_hidden_process_without_starting_capture(monkeypatch):
    import psutil

    original_popen, children = subprocess.Popen, []

    def sleeper(_command, **kwargs):
        process = original_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(capture.subprocess, 'Popen', sleeper)
    with pytest.raises(TimeoutError):
        capture.run_worker({}, 1)
    assert len(children) == 1 and children[0].returncode is not None
    assert not psutil.pid_exists(children[0].pid)


@pytest.mark.parametrize('stdout,returncode', [('not-json', 0), ('{"status":"ERROR","error":"closed"}', 0),
                                              ('[]', 0), ('{}', 0), ('', 4)])
def test_worker_failure_or_invalid_result_has_no_success(monkeypatch, stdout, returncode):
    child = FakeChild(stdout, returncode)
    monkeypatch.setattr(capture.subprocess, 'Popen', lambda *a, **k: child)
    with pytest.raises((ValueError, RuntimeError)) as error:
        capture.run_worker({}, 1)
    assert len(str(error.value)) < 2200


def test_discovery_is_bounded_exact_fit_and_case_insensitive(rig):
    windows, context = rig
    windows.rows = [dict(TARGET, process_name='MAYA.EXE'), TARGET]
    result = discover.execute({'max_windows': 1}, context)
    assert result['status'] == 'SUCCESS' and result['output']['truncated'] is True
    windows.description_count, windows.rows = 0, [TARGET]
    result = discover.execute({'max_windows': 1}, context)
    assert result['output']['truncated'] is False
    assert result['output']['window_count'] == 1


def test_discovery_filters_other_processes_and_reports_vanished_windows(rig):
    windows, context = rig
    windows.rows = [RuntimeError('gone'), dict(TARGET, process_name='editor.exe'), TARGET]
    result = discover.execute({}, context)
    assert result['output']['window_count'] == 1
    assert result['output']['unavailable_window_count'] == 1


@pytest.mark.parametrize('params', [{'max_windows': 0}, {'max_windows': 201}, {'max_windows': True},
                                   {'process_names': []}, {'process_names': ['C:/maya.exe']},
                                   {'process_names': 'maya.exe'}, {'process_names': ['']}])
def test_invalid_discovery_bounds_or_process_filters(rig, params):
    assert discover.execute(params, rig[1])['status'] == 'ERROR'


def test_worker_copies_bgra_encodes_pixels_and_stops_in_callback(tmp_path, monkeypatch):
    import numpy as np

    events, options = {}, {}
    source = np.full((8, 12, 4), [56, 34, 12, 255], dtype=np.uint8)
    stopped = []

    class FakeCapture:
        def __init__(self, **kwargs): options.update(kwargs)
        def event(self, function): events[function.__name__] = function; return function
        def start_free_threaded(self):
            frame = types.SimpleNamespace(frame_buffer=source, width=12, height=8, timespan=123)
            events['on_frame_arrived'](frame, types.SimpleNamespace(stop=lambda: stopped.append(True)))
            source[:] = 0
            return types.SimpleNamespace(wait=lambda: None)

    monkeypatch.setitem(sys.modules, 'windows_capture', types.SimpleNamespace(WindowsCapture=FakeCapture))
    path = tmp_path / 'frame.png'
    result = capture_worker.capture(dict(PARAMS, output_path=str(path)))
    assert stopped == [True]
    assert options == {'window_hwnd': TARGET['hwnd'], 'cursor_capture': False, 'draw_border': None}
    assert result['timespan_qpc_100ns'] == 123
    assert capture.validate_png(path, 12, 8) == 'RGBA'
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == (12, 34, 56, 255)


def test_worker_encoding_failure_stops_and_cannot_succeed(tmp_path, monkeypatch):
    import numpy as np
    import cv2

    stopped = []

    class FakeCapture:
        def __init__(self, **kwargs): self.events = {}
        def event(self, function): self.events[function.__name__] = function; return function
        def start_free_threaded(self):
            self.events['on_frame_arrived'](types.SimpleNamespace(frame_buffer=np.zeros((1, 1, 4), dtype=np.uint8),
                width=1, height=1, timespan=1), types.SimpleNamespace(stop=lambda: stopped.append(True)))
            return types.SimpleNamespace(wait=lambda: None)

    monkeypatch.setitem(sys.modules, 'windows_capture', types.SimpleNamespace(WindowsCapture=FakeCapture))
    monkeypatch.setattr(cv2, 'imencode', lambda *_: (False, None))
    with pytest.raises(RuntimeError, match='encoder'):
        capture_worker.capture(dict(PARAMS, output_path=str(tmp_path / 'frame.png')))
    assert stopped == [True]


def test_worker_closed_before_frame_fails_cleanly(tmp_path, monkeypatch):
    class FakeCapture:
        def __init__(self, **kwargs): self.events = {}
        def event(self, function): self.events[function.__name__] = function; return function
        def start_free_threaded(self):
            self.events['on_closed']()
            return types.SimpleNamespace(wait=lambda: None)

    monkeypatch.setitem(sys.modules, 'windows_capture', types.SimpleNamespace(WindowsCapture=FakeCapture))
    with pytest.raises(RuntimeError, match='without delivering a frame'):
        capture_worker.capture(dict(PARAMS, output_path=str(tmp_path / 'frame.png')))
    assert not (tmp_path / 'frame.png').exists()


def test_worker_rejects_changed_identity_before_wgc_construction(tmp_path, monkeypatch):
    windows = FakeWindows([dict(TARGET, pid=987)])
    monkeypatch.setattr(capture_worker, 'NativeWindows', lambda: windows)
    monkeypatch.setitem(sys.modules, 'windows_capture', types.SimpleNamespace(
        WindowsCapture=lambda **_: pytest.fail('must reject identity before constructing capture')))
    with pytest.raises(RuntimeError, match='identity changed'):
        capture_worker.capture(dict(PARAMS, output_path=str(tmp_path / 'frame.png')))


def test_worker_rechecks_identity_at_first_frame_before_writing(tmp_path, monkeypatch):
    import numpy as np

    windows, stopped = FakeWindows([TARGET, dict(TARGET, process_started_at=999.0)]), []
    monkeypatch.setattr(capture_worker, 'NativeWindows', lambda: windows)

    class FakeCapture:
        def __init__(self, **kwargs): self.events = {}
        def event(self, function): self.events[function.__name__] = function; return function
        def start_free_threaded(self):
            self.events['on_frame_arrived'](types.SimpleNamespace(frame_buffer=np.zeros((1, 1, 4), dtype=np.uint8),
                width=1, height=1, timespan=1), types.SimpleNamespace(stop=lambda: stopped.append(True)))
            return types.SimpleNamespace(wait=lambda: None)

    monkeypatch.setitem(sys.modules, 'windows_capture', types.SimpleNamespace(WindowsCapture=FakeCapture))
    path = tmp_path / 'frame.png'
    with pytest.raises(RuntimeError, match='identity changed'):
        capture_worker.capture(dict(PARAMS, output_path=str(path)))
    assert stopped == [True] and not path.exists()


def test_manifests_use_existing_facets_and_explicit_target_contract():
    base = Path(capture.__file__).parents[1]
    for operation in ['discover', 'capture']:
        raw = yaml.safe_load((base / operation / 'capability.yaml').read_text(encoding='utf-8'))
        validated = validate_spec(raw)
        assert validated['executor'] == 'pipeline'
        assert validated['execution_modes'] == ['background']
        if operation == 'capture':
            assert validated['version'] == capture.VERSION
        else:
            assert validated['version'] == discover.VERSION
    assert raw['inputs']['scope']['default'] == 'window'
    for name in ('hwnd', 'expected_pid', 'expected_process_started_at'):
        assert 'Required for scope=window' in raw['inputs'][name]['description']
    assert raw['inputs']['minimized_policy']['default'] == 'reject'


@pytest.mark.skipif(sys.platform != 'win32', reason='Win32 prototypes are only constructed on Windows')
def test_native_prototypes_keep_64_bit_hwnd_without_focus_apis(monkeypatch):
    class Function:
        def __call__(self, *args): return 0

    allowed = ['EnumWindows', 'IsWindow', 'IsWindowVisible', 'IsIconic', 'GetWindowThreadProcessId',
               'GetWindowTextLengthW', 'GetWindowTextW', 'GetClassNameW', 'GetWindowRect', 'GetForegroundWindow',
               'ProcessIdToSessionId', 'WTSQuerySessionInformationW', 'WTSFreeMemory']
    api = types.SimpleNamespace(**{name: Function() for name in allowed})
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: api)
    native.NativeWindows()
    assert api.GetForegroundWindow.restype is ctypes.wintypes.HWND
    assert api.GetWindowThreadProcessId.argtypes[0] is ctypes.wintypes.HWND
    assert ctypes.sizeof(ctypes.wintypes.HWND) == ctypes.sizeof(ctypes.c_void_p)
    assert api.WTSQuerySessionInformationW.argtypes[0] is ctypes.wintypes.HANDLE
    assert api.WTSQuerySessionInformationW.argtypes[3] == ctypes.POINTER(ctypes.c_void_p)
    assert api.WTSFreeMemory.argtypes == [ctypes.c_void_p] and api.WTSFreeMemory.restype is None


@pytest.mark.skipif(sys.platform != 'win32', reason='Win32 errors are only constructed on Windows')
@pytest.mark.parametrize('success,byte_count', [(True, 4), (False, 4), (True, 0)])
def test_native_session_query_uses_current_pid_and_always_frees_allocated_state(success, byte_count):
    state_buffer, freed, queried = ctypes.c_int(4), [], []
    windows = native.NativeWindows.__new__(native.NativeWindows)

    def process_to_session(pid, result):
        queried.append(('pid', pid))
        ctypes.cast(result, ctypes.POINTER(ctypes.wintypes.DWORD))[0] = 2
        return True

    def query(server, session_id, info_class, result, size):
        queried.append(('session', server, session_id, info_class))
        ctypes.cast(result, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(state_buffer)
        ctypes.cast(size, ctypes.POINTER(ctypes.wintypes.DWORD))[0] = byte_count
        return success

    windows.kernel = types.SimpleNamespace(ProcessIdToSessionId=process_to_session)
    windows.wts = types.SimpleNamespace(WTSQuerySessionInformationW=query,
                                        WTSFreeMemory=lambda buffer: freed.append(buffer.value))
    if success and byte_count:
        result = windows.session_status()
        assert result['session_id'] == 2 and result['connect_state_name'] == 'WTSDisconnected'
        assert result['active'] is False
    else:
        with pytest.raises((OSError, RuntimeError)):
            windows.session_status()
    assert queried == [('pid', native.os.getpid()), ('session', None, 2, 8)]
    assert freed == [ctypes.addressof(state_buffer)]


def test_shared_package_import_does_not_import_native_capture_runtime(monkeypatch):
    monkeypatch.delitem(sys.modules, 'windows_capture', raising=False)
    import cgi_pipeline.capabilities.pipeline.window as package
    importlib.reload(package)
    assert 'windows_capture' not in sys.modules
