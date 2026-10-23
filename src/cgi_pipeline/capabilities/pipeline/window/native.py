"""Read-only Win32 window identity, with pointer-sized HWND prototypes."""
from __future__ import annotations

import ctypes
import math
import os
import sys
from ctypes import wintypes

DEFAULT_PROCESSES = ['maya.exe', 'blender.exe', 'houdini.exe', 'houdinifx.exe', 'unrealeditor.exe']
SESSION_STATES = ['WTSActive', 'WTSConnected', 'WTSConnectQuery', 'WTSShadow', 'WTSDisconnected',
                  'WTSIdle', 'WTSListen', 'WTSReset', 'WTSDown', 'WTSInit']


def positive_int(value, label, maximum):
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError(f'{label} must be an integer between 1 and {maximum}')
    return value


def finite_number(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be finite and between {minimum} and {maximum}')
    return value


class NativeWindows:
    def __init__(self):
        if sys.platform != 'win32':
            raise RuntimeError('Windows window observation requires Windows')
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.wts = ctypes.WinDLL('wtsapi32', use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = {
            'EnumWindows': ([self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            'IsWindow': ([wintypes.HWND], wintypes.BOOL),
            'IsWindowVisible': ([wintypes.HWND], wintypes.BOOL),
            'IsIconic': ([wintypes.HWND], wintypes.BOOL),
            'GetWindowThreadProcessId': ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            'GetWindowTextLengthW': ([wintypes.HWND], ctypes.c_int),
            'GetWindowTextW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetClassNameW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetWindowRect': ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            'GetForegroundWindow': ([], wintypes.HWND),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.ProcessIdToSessionId.restype = wintypes.BOOL
        self.wts.WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
                                                        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
        self.wts.WTSQuerySessionInformationW.restype = wintypes.BOOL
        self.wts.WTSFreeMemory.argtypes, self.wts.WTSFreeMemory.restype = [ctypes.c_void_p], None

    def session_status(self):
        pid, session_id = os.getpid(), wintypes.DWORD()
        if not self.kernel.ProcessIdToSessionId(pid, ctypes.byref(session_id)):
            raise ctypes.WinError(ctypes.get_last_error())
        buffer, byte_count = ctypes.c_void_p(), wintypes.DWORD()
        try:
            # WTS_CURRENT_SERVER_HANDLE = NULL; WTSConnectState = 8.
            if not self.wts.WTSQuerySessionInformationW(None, session_id.value, 8,
                                                        ctypes.byref(buffer), ctypes.byref(byte_count)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not buffer or byte_count.value < ctypes.sizeof(ctypes.c_int):
                raise RuntimeError('WTSConnectState returned no complete state value')
            state = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int)).contents.value
            return {'process_id': pid, 'session_id': session_id.value, 'connect_state': state,
                    'connect_state_name': SESSION_STATES[state] if 0 <= state < len(SESSION_STATES) else 'UNKNOWN',
                    'active': state == 0}
        finally:
            if buffer:
                self.wts.WTSFreeMemory(buffer)

    def foreground(self):
        return int(self.api.GetForegroundWindow() or 0)

    def describe(self, hwnd):
        import psutil

        if not self.api.IsWindow(hwnd):
            raise RuntimeError('Target window no longer exists')
        pid, rect = wintypes.DWORD(), wintypes.RECT()
        if not self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not self.api.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            process = psutil.Process(pid.value)
            process_name, process_started_at = process.name(), process.create_time()
        except psutil.Error as exc:
            raise RuntimeError(f'Window process identity is unavailable: {exc}') from exc
        title = ctypes.create_unicode_buffer(self.api.GetWindowTextLengthW(hwnd) + 1)
        class_name = ctypes.create_unicode_buffer(512)
        self.api.GetWindowTextW(hwnd, title, len(title))
        self.api.GetClassNameW(hwnd, class_name, len(class_name))
        return {'hwnd': int(hwnd), 'pid': pid.value, 'process_name': process_name,
                'process_started_at': process_started_at, 'title': title.value,
                'class': class_name.value, 'visible': bool(self.api.IsWindowVisible(hwnd)),
                'minimized': bool(self.api.IsIconic(hwnd)),
                'rect': {'left': rect.left, 'top': rect.top, 'right': rect.right, 'bottom': rect.bottom}}

    def enumerate(self, visit):
        stopped, failures = False, []

        def callback(hwnd, _):
            nonlocal stopped
            try:
                stopped = not visit(int(hwnd))
                return not stopped
            except Exception as exc:
                failures.append(exc)
                return False

        callback_ref = self.callback_type(callback)
        if not self.api.EnumWindows(callback_ref, 0) and not stopped and not failures:
            raise ctypes.WinError(ctypes.get_last_error())
        if failures:
            raise failures[0]


def require_identity(native, params, *, allow_minimized=False):
    row = native.describe(params['hwnd'])
    if row['pid'] != params['expected_pid'] or row['process_started_at'] != params['expected_process_started_at']:
        raise RuntimeError('Window identity changed; discover the target again')
    if not row['visible'] or (row['minimized'] and not allow_minimized):
        raise RuntimeError('Target must be visible and not minimized; no activation or restore is performed')
    if not row['minimized'] and (row['rect']['right'] <= row['rect']['left'] or row['rect']['bottom'] <= row['rect']['top']):
        raise RuntimeError('Target window has an empty rectangle')
    return row


def observe_session(native):
    try:
        return native.session_status()
    except (OSError, RuntimeError) as exc:
        return {'active': None, 'query_status': 'ERROR', 'error': str(exc)}
