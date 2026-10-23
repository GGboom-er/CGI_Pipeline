"""Explicit temporary non-activating window restoration, including failure cleanup."""
import ctypes
import time
from ctypes import wintypes

from .native import require_identity


class WindowPlacement(ctypes.Structure):
    _fields_ = [('length', wintypes.UINT), ('flags', wintypes.UINT), ('show_cmd', wintypes.UINT),
                ('minimum', wintypes.POINT), ('maximum', wintypes.POINT), ('normal', wintypes.RECT)]


class Placement:
    def __init__(self, native, hwnd):
        self.api, self.hwnd = native.api, hwnd
        signatures = {
            'GetWindowPlacement': ([wintypes.HWND, ctypes.POINTER(WindowPlacement)], wintypes.BOOL),
            'SetWindowPlacement': ([wintypes.HWND, ctypes.POINTER(WindowPlacement)], wintypes.BOOL),
            'ShowWindowAsync': ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            'GetWindow': ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            'SetWindowPos': ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result

    def read(self):
        value = WindowPlacement(length=ctypes.sizeof(WindowPlacement))
        if not self.api.GetWindowPlacement(self.hwnd, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'show_cmd': value.show_cmd, 'flags': value.flags,
                'min': [value.minimum.x, value.minimum.y], 'max': [value.maximum.x, value.maximum.y],
                'normal': [value.normal.left, value.normal.top, value.normal.right, value.normal.bottom]}

    def previous(self):
        return int(self.api.GetWindow(self.hwnd, 3) or 0)  # GW_HWNDPREV

    def keep_order(self, previous):
        # Preserve the original predecessor where it still exists; never activate.
        anchor = previous if previous and self.api.IsWindow(previous) else 1  # HWND_BOTTOM
        if not self.api.SetWindowPos(self.hwnd, anchor, 0, 0, 0, 0, 0x0013):
            raise ctypes.WinError(ctypes.get_last_error())  # NOSIZE | NOMOVE | NOACTIVATE

    def show(self, previous):
        if not self.api.ShowWindowAsync(self.hwnd, 4):  # SW_SHOWNOACTIVATE
            raise ctypes.WinError(ctypes.get_last_error())
        self.keep_order(previous)

    def restore(self, snapshot, previous):
        value = WindowPlacement(length=ctypes.sizeof(WindowPlacement), flags=snapshot['flags'], show_cmd=7,
                                minimum=wintypes.POINT(*snapshot['min']), maximum=wintypes.POINT(*snapshot['max']),
                                normal=wintypes.RECT(*snapshot['normal']))
        if not self.api.SetWindowPlacement(self.hwnd, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.keep_order(previous)


def wait_state(native, params, minimized):
    deadline = time.monotonic() + 2
    while True:
        row = require_identity(native, params, allow_minimized=True)
        if row['minimized'] is minimized:
            return row
        if time.monotonic() >= deadline:
            raise TimeoutError('Window did not reach the requested show state within 2 seconds')
        time.sleep(0.02)


class TemporaryRestore:
    def __init__(self, native, params, policy):
        self.native, self.params, self.policy = native, params, policy
        self.changed = False
        self.details = {'requested_policy': policy, 'performed': False, 'restored': None}

    def __enter__(self):
        self.identity = require_identity(self.native, self.params, allow_minimized=self.policy == 'restore')
        if not self.identity['minimized']:
            return self
        self.placement = Placement(self.native, self.params['hwnd'])
        self.snapshot, self.previous = self.placement.read(), self.placement.previous()
        self.details.update(performed=True, before=self.snapshot, foreground_before=self.native.foreground())
        self.changed = True  # Show may partially succeed before raising.
        try:
            self.placement.show(self.previous)
            self.details['capture_target'] = wait_state(self.native, self.params, False)
            self.details['capture_placement'] = self.placement.read()
        except Exception as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise
        return self

    def __exit__(self, kind, error, tb):
        if not self.changed:
            return False
        try:
            current = require_identity(self.native, self.params, allow_minimized=True)
            # A user activating or moving the target owns the new state. Do not undo it.
            if (self.native.foreground() == current['hwnd']
                    or ('capture_placement' in self.details
                        and self.placement.read()['normal'] != self.details['capture_placement']['normal'])):
                raise RuntimeError('Target was activated or moved during capture; restoration skipped to preserve user action')
            self.placement.restore(self.snapshot, self.previous)
            wait_state(self.native, self.params, True)
            after = self.placement.read()
            self.details.update(after=after, restored=after == self.snapshot,
                                foreground_after=self.native.foreground())
            if not self.details['restored']:
                raise RuntimeError('Window placement after re-minimizing differs from its original state')
        except Exception as restore_error:
            self.details.update(restored=False, error=str(restore_error))
            failure = RuntimeError(f'{error or "Capture completed"}; window state restoration failed: {restore_error}')
            failure.worker_metadata = getattr(error, 'worker_metadata', {})
            raise failure from restore_error
        return False
