"""Disposable WGC worker. windows-capture 2.0.1 (MIT) owns the native backend.

https://github.com/NiiightmareXD/windows-capture -- no capture dependency is
imported by the shared CGI/Maya package until this worker is invoked.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import threading

from .native import NativeWindows, require_identity
from .displays import require_monitor

_STARTED = time.perf_counter()


def mark(stage):
    print(json.dumps({'stage': stage, 'elapsed_sec': round(time.perf_counter() - _STARTED, 6)}),
          file=sys.stderr, flush=True)


def capture(request):
    mark('imports_started')
    import cv2
    import numpy as np
    from windows_capture import WindowsCapture
    mark('imports_done')

    path = Path(request['output_path'])
    result, pending, failures = {}, {}, []
    frame_count = 0
    frame_ready = threading.Event()
    settle_ms = request.get('settle_ms', 0)
    native = NativeWindows()
    check = lambda: require_monitor(native, request['monitor']) if 'monitor' in request else require_identity(native, request)
    check()
    mark('identity_verified')
    target = {'monitor_index': request['monitor']['index']} if 'monitor' in request else {'window_hwnd': request['hwnd']}
    capture_session = WindowsCapture(**target, cursor_capture=False, draw_border=None)
    mark('constructed')

    @capture_session.event
    def on_frame_arrived(frame, internal):
        nonlocal frame_count
        try:
            if failures:
                return
            first = not pending
            if first:
                mark('first_frame')
            check()
            if first:
                mark('frame_identity_verified')
            acquired_at = datetime.now(timezone.utc).isoformat()
            acquired_monotonic = time.monotonic()
            pixels = np.array(frame.frame_buffer, copy=True)
            if first:
                mark('frame_copied')
            if pixels.shape != (frame.height, frame.width, 4) or not pixels.size:
                raise RuntimeError('Capture returned an empty or unsupported BGRA frame')
            pending.update(pixels=pixels, width=frame.width, height=frame.height,
                           timespan_qpc_100ns=int(frame.timespan), acquired_at=acquired_at,
                           acquired_monotonic=acquired_monotonic)
            frame_count += 1
            frame_ready.set()
        except Exception as exc:
            failures.append(str(exc))
            frame_ready.set()
        finally:
            if not settle_ms or failures:
                internal.stop()
                mark('stop_requested')

    @capture_session.event
    def on_closed():
        mark('closed')
        frame_ready.set()

    mark('starting')
    control = capture_session.start_free_threaded()
    mark('started')
    # Native wait/stop both borrow the same control mutably. Never call them
    # concurrently. During settling, collect callbacks first, then stop and join.
    if settle_ms:
        frame_ready.wait()
        threading.Event().wait(settle_ms / 1000)
        control.stop()
    else:
        control.wait()  # Parent bounds native start/stop/wait and frame readiness.
    mark('waited')
    if failures or not pending:
        raise RuntimeError('; '.join(failures) or 'Target closed without delivering a frame')
    pixels = pending.pop('pixels')
    ok, encoded = cv2.imencode('.png', pixels)
    if not ok:
        raise RuntimeError('PNG encoder rejected the frame')
    with path.open('xb') as stream:
        stream.write(encoded.tobytes())
    decoded = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if decoded is None or decoded.shape != pixels.shape:
        raise RuntimeError('Written PNG failed pixel decode')
    mark('written')
    result.update(status='SUCCESS', backend='windows-graphics-capture', frame_pixel_format='BGRA8',
                  **pending, settle_ms=settle_ms, frame_count=frame_count)
    return result


def main():
    mark('worker_main')
    try:
        request = json.load(sys.stdin)
        if 'targets' in request:
            captures = [dict(capture(target), monitor=target['monitor'], output_path=target['output_path'])
                        for target in request['targets']]
            result = {'status': 'SUCCESS', 'backend': 'windows-graphics-capture', 'captures': captures}
        else:
            result = capture(request)
    except Exception as exc:
        mark('failed')
        result = {'status': 'ERROR', 'error': str(exc)}
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
