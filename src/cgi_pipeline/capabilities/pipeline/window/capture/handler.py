"""WGC single-frame capture isolated in a bounded, reapable child process."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from cgi_pipeline.contracts import make_api_receipt
from cgi_pipeline.core.path_guard import is_protected_path
from ..native import NativeWindows, finite_number, positive_int, require_identity, observe_session
from ..capture_state import TemporaryRestore
from .. import displays

VERSION = '1.1.1'
CLEANUP_TIMEOUT_SEC = 2


class WorkerTimeout(TimeoutError):
    def __init__(self, message, metadata):
        super().__init__(message)
        self.worker_metadata = metadata


class WorkerFailure(RuntimeError):
    def __init__(self, message, metadata):
        super().__init__(message)
        self.worker_metadata = metadata


class SessionUnavailable(RuntimeError):
    def __init__(self, session):
        super().__init__('Capture requires an observed WTSActive CGI process session; '
                         'the current session is not Active or its connection state could not be queried. '
                         'This does not establish whether the desktop is locked.')
        self.session = session


def output_path(run_dir, prefix='window'):
    if not run_dir or not Path(run_dir).is_absolute():
        raise ValueError('An absolute context.run_dir is required')
    root = Path(run_dir).resolve()
    if not root.is_dir():
        raise ValueError('context.run_dir must already be a task directory')
    directory = (root / 'outputs' / 'captures').resolve()
    if not directory.is_relative_to(root) or is_protected_path(str(directory)):
        raise PermissionError('Capture output must remain in the task and outside protected paths')
    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / f'{prefix}_{uuid4().hex}.png').resolve()
    if not path.is_relative_to(root) or is_protected_path(str(path)):
        raise PermissionError('Capture output escaped the task directory')
    return path


def validate_png(path, width, height):
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        if image.format != 'PNG' or image.size != (width, height) or width <= 0 or height <= 0:
            raise RuntimeError('Capture output is not a decodable PNG of the reported dimensions')
        return image.mode


def worker_metadata(process, started, stderr, cleanup_error=''):
    if isinstance(stderr, bytes):
        stderr = stderr.decode('utf-8', errors='replace')
    return {'worker_pid': process.pid, 'worker_exited': process.poll() is not None,
            'worker_lifecycle_elapsed_sec': round(time.perf_counter() - started, 6),
            'worker_diagnostics': (stderr or '')[-3000:], 'worker_cleanup_error': cleanup_error}


def terminate_worker(process, started):
    error, stderr = '', ''
    try:
        if process.poll() is None:
            process.kill()
    except OSError as exc:
        error = f'Termination request failed: {exc}. '
    try:
        _, stderr = process.communicate(timeout=CLEANUP_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or ''
        error += f'Worker collection did not complete within {CLEANUP_TIMEOUT_SEC} seconds after termination request.'
    except OSError as exc:
        error += f'Worker collection failed: {exc}.'
    return worker_metadata(process, started, stderr, error)


def run_worker(request, timeout):
    started = time.perf_counter()
    process = subprocess.Popen(
        [sys.executable, '-m', 'cgi_pipeline.capabilities.pipeline.window.capture_worker'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    try:
        stdout, stderr = process.communicate(json.dumps(request), timeout=timeout)
    except subprocess.TimeoutExpired:
        metadata = terminate_worker(process, started)
        outcome = 'exited and was reaped' if metadata['worker_exited'] and not metadata['worker_cleanup_error'] else 'cleanup incomplete'
        raise WorkerTimeout(f'Capture worker {process.pid} exceeded {timeout} seconds; {outcome}. '
                            f"{metadata['worker_cleanup_error']} Diagnostics: {metadata['worker_diagnostics']}", metadata)
    except BaseException as exc:
        metadata = terminate_worker(process, started)
        if not isinstance(exc, Exception):
            raise
        raise WorkerFailure(f'Capture worker communication failed: {exc}', metadata) from exc
    metadata = worker_metadata(process, started, stderr)
    if process.returncode != 0:
        raise WorkerFailure(f'Capture worker failed ({process.returncode}): {stderr[-2000:]}', metadata)
    try:
        result = json.loads(stdout)
    except ValueError as exc:
        raise WorkerFailure('Capture worker returned invalid JSON', metadata) from exc
    if not isinstance(result, dict) or result.get('status') != 'SUCCESS':
        raise WorkerFailure(str(result.get('error', 'Capture worker returned no successful frame')) if isinstance(result, dict) else 'Invalid capture worker response', metadata)
    result.update(metadata)
    return result


def capture_desktop(params, context, native, timeout):
    if type(params.get('combine', True)) is not bool:
        raise ValueError('combine must be a boolean')
    monitors = displays.enumerate_monitors(native)
    if 'expected_monitors' in params and params['expected_monitors'] != monitors:
        raise RuntimeError('Display topology differs from discovery; discover monitors again')
    selected = displays.select_monitors(monitors, params.get('monitor_indices'))
    targets = [{'monitor': row, 'output_path': str(output_path(context.run_dir, 'monitor'))} for row in selected]
    result = run_worker({'targets': targets}, timeout)
    if displays.enumerate_monitors(native) != monitors:
        raise RuntimeError('Display topology changed during capture')
    frames = result.get('captures', [])
    if len(frames) != len(targets):
        raise RuntimeError('Capture worker did not return every selected monitor')
    artifacts = []
    for frame, target in zip(frames, targets):
        if frame['monitor'] != target['monitor'] or frame['output_path'] != target['output_path']:
            raise RuntimeError('Capture worker returned an unexpected monitor or path')
        frame['image_mode'] = validate_png(Path(frame['output_path']), frame['width'], frame['height'])
        rect = target['monitor']['rect']
        if (frame['width'], frame['height']) != (rect['right'] - rect['left'], rect['bottom'] - rect['top']):
            raise RuntimeError('Monitor pixel dimensions differ from physical display bounds')
        artifacts.append(frame['output_path'])
    result.update(scope='desktop', monitors=monitors, simultaneous=False, captures=frames)
    if params.get('combine', True):
        path = output_path(context.run_dir, 'desktop')
        bounds, width, height = displays.compose(frames, path)
        result.update(output_path=str(path), desktop_rect=bounds, width=width, height=height,
                      image_mode=validate_png(path, width, height))
        artifacts.append(str(path))
    result.pop('status', None)
    return result, artifacts


def execute(params, context):
    started = time.perf_counter()
    path, state = None, None
    try:
        scope = params.get('scope', 'window')
        policy = params.get('minimized_policy', 'reject')
        if scope not in ('window', 'desktop') or policy not in ('reject', 'restore'):
            raise ValueError('scope must be window/desktop; minimized_policy must be reject/restore')
        if 'settle_ms' in params and (type(params['settle_ms']) is not int or not 0 <= params['settle_ms'] <= 3000):
            raise ValueError('settle_ms must be an integer between 0 and 3000')
        if scope == 'window':
            positive_int(params.get('hwnd'), 'hwnd', 2**64 - 1)
            positive_int(params.get('expected_pid'), 'expected_pid', 2**32 - 1)
            finite_number(params.get('expected_process_started_at'), 'expected_process_started_at', 0.000001, 1e12)
        timeout = finite_number(params.get('timeout_sec', 8), 'timeout_sec', 1, 30)
        native = NativeWindows()
        session = observe_session(native)
        if session.get('active') is not True:
            raise SessionUnavailable(session)
        foreground_before = native.foreground()
        if scope == 'desktop':
            result, artifacts = capture_desktop(params, context, native, timeout)
        else:
            state = TemporaryRestore(native, params, policy)
            with state:
                identity = state.identity
                path = output_path(context.run_dir)
                result = run_worker({**{key: params[key] for key in ('hwnd', 'expected_pid', 'expected_process_started_at')},
                                     'settle_ms': params.get('settle_ms', 750 if state.details['performed'] else 0),
                                     'output_path': str(path)}, timeout)
                require_identity(native, params)
                width, height = result['width'], result['height']
                if type(width) is not int or type(height) is not int:
                    raise RuntimeError('Capture worker returned invalid dimensions')
                image_mode = validate_png(path, width, height)
            result.update(output_path=str(path), target=identity, image_mode=image_mode,
                          window_state=state.details, scope='window')
            result.pop('status')
            artifacts = [str(path)]
        foreground_after = native.foreground()
        result.update({'session': session,
                       'foreground_hwnd_before': foreground_before, 'foreground_hwnd_after': foreground_after,
                       'foreground_unchanged': foreground_before == foreground_after})
        return make_api_receipt('pipeline.window.capture', VERSION, 'SUCCESS', started,
                                input_data=params, output=result, artifacts=artifacts)
    except Exception as exc:
        # A partial file is retained for diagnosis, never returned as a successful artifact.
        metadata = getattr(exc, 'worker_metadata', {})
        if state is not None:
            metadata['window_state'] = state.details
        cleanup_failed = bool(metadata.get('worker_cleanup_error')) or metadata.get('worker_exited') is False
        restore_failed = state is not None and state.details.get('restored') is False
        timed_out = isinstance(exc, TimeoutError) and not cleanup_failed and not restore_failed
        session_unavailable = isinstance(exc, SessionUnavailable)
        return make_api_receipt('pipeline.window.capture', VERSION, 'TIMEOUT' if timed_out else 'ERROR', started,
                                input_data=params, output={**({'attempted_output_path': str(path)} if path else {}),
                                                          **metadata, **({'session': exc.session} if session_unavailable else {})},
                                error_code='WINDOW_CAPTURE_SESSION_UNAVAILABLE' if session_unavailable else ('WINDOW_CAPTURE_CLEANUP_FAILED' if cleanup_failed else ('WINDOW_CAPTURE_RESTORE_FAILED' if restore_failed else ('WINDOW_CAPTURE_TIMEOUT' if timed_out else 'WINDOW_CAPTURE_FAILED'))),
                                error=str(exc), recovery_hint='Inspect capture and window_state recovery evidence; rediscover changed targets. Do not replay an unknown restore outcome.')
