"""Native monitor selection and coordinate-preserving screenshot composition."""
import ctypes
from ctypes import wintypes


class MonitorInfo(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                ('work', wintypes.RECT), ('flags', wintypes.DWORD), ('device', wintypes.WCHAR * 32)]


def enumerate_monitors(native):
    api, rows, errors = native.api, [], []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                       ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    api.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), callback_type, wintypes.LPARAM]
    api.EnumDisplayMonitors.restype = wintypes.BOOL
    api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    api.GetMonitorInfoW.restype = wintypes.BOOL
    api.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    api.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p

    def visit(handle, dc, rect, data):
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if not api.GetMonitorInfoW(handle, ctypes.byref(info)):
            errors.append(str(ctypes.WinError(ctypes.get_last_error())))
            return False
        bounds = info.monitor
        rows.append({'index': len(rows) + 1, 'handle': int(handle), 'device': info.device,
                     'primary': bool(info.flags & 1),
                     'rect': dict(left=bounds.left, top=bounds.top, right=bounds.right, bottom=bounds.bottom)})
        return True

    previous = api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if not previous:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ok = api.EnumDisplayMonitors(None, None, callback_type(visit), 0)
    finally:
        api.SetThreadDpiAwarenessContext(previous)
    if not ok or errors or not 0 < len(rows) <= 16:
        raise RuntimeError('Monitor enumeration failed or exceeded 16 displays: ' + '; '.join(errors))
    return rows


def require_monitor(native, expected):
    if expected not in enumerate_monitors(native):
        raise RuntimeError('Display identity or topology changed; discover monitors again')


def select_monitors(monitors, indices):
    if indices is None:
        return monitors
    if (not isinstance(indices, list) or not indices or len(indices) > 16
            or any(type(index) is not int or index < 1 for index in indices)
            or len(set(indices)) != len(indices)):
        raise ValueError('monitor_indices must contain 1 to 16 distinct positive integer indices')
    selected = [row for row in monitors if row['index'] in indices]
    if len(selected) != len(indices):
        raise ValueError('A selected monitor is unavailable; discover monitors again')
    return selected


def compose(captures, path):
    from PIL import Image

    rects = [capture['monitor']['rect'] for capture in captures]
    bounds = {key: fn(rect[key] for rect in rects) for key, fn in
              [('left', min), ('top', min), ('right', max), ('bottom', max)]}
    width, height = bounds['right'] - bounds['left'], bounds['bottom'] - bounds['top']
    if width <= 0 or height <= 0 or width * height > 100_000_000:
        raise ValueError('Combined desktop exceeds the 100 megapixel bound')
    canvas = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    for capture in captures:
        rect = capture['monitor']['rect']
        with Image.open(capture['output_path']) as image:
            expected = (rect['right'] - rect['left'], rect['bottom'] - rect['top'])
            if image.size != expected:
                raise RuntimeError('Monitor frame dimensions do not match physical display bounds')
            canvas.paste(image.convert('RGBA'), (rect['left'] - bounds['left'], rect['top'] - bounds['top']))
    with path.open('xb') as stream:
        canvas.save(stream, format='PNG')
    return bounds, width, height
