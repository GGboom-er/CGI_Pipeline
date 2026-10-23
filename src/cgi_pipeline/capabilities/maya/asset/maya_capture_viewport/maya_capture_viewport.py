"""
Maya 观察截图 API：整窗或原生视口，加当前观察元数据。
 
整窗模式保留 Windows PrintWindow；viewport 模式使用 Maya 原生 playblast。
两种取证范围明确区分，不在失败时静默替换。实际 PNG 须打开核验。
"""
import time
import traceback
import ctypes
import ctypes.wintypes
import struct
import zlib
from pathlib import Path
from uuid import uuid4

from cgi_pipeline.core.receipt import make_receipt
from cgi_pipeline.paths import REPOSITORY_ROOT


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', ctypes.c_uint32),
        ('biWidth', ctypes.c_int32),
        ('biHeight', ctypes.c_int32),
        ('biPlanes', ctypes.c_uint16),
        ('biBitCount', ctypes.c_uint16),
        ('biCompression', ctypes.c_uint32),
        ('biSizeImage', ctypes.c_uint32),
        ('biXPelsPerMeter', ctypes.c_int32),
        ('biYPelsPerMeter', ctypes.c_int32),
        ('biClrUsed', ctypes.c_uint32),
        ('biClrImportant', ctypes.c_uint32),
    ]


def _execute_window(payload: dict) -> dict:
    t0 = time.time()
    
    try:
        import maya.cmds as cmds
        import maya.OpenMayaUI as omui
        try:
            from shiboken6 import wrapInstance
            from PySide6 import QtWidgets, QtGui, QtCore
        except ImportError:
            from shiboken2 import wrapInstance
            from PySide2 import QtWidgets, QtGui, QtCore
        
        # 创建截图存储目录
        temp_path = Path(payload['_capture_output_path'])
        
        save_path = str(temp_path).replace('\\', '/')
        
        # 获取 Maya 主窗口的 Win32 原生句柄 (HWND)
        main_window_ptr = omui.MQtUtil.mainWindow()
        if not main_window_ptr:
            raise RuntimeError('Maya GUI main window is unavailable')
        main_window = wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
        hwnd = int(main_window.winId())
        
        # 将 Maya 窗口拉到最前面并强制刷新 GPU 渲染
        main_window.raise_()
        main_window.activateWindow()
        cmds.refresh(force=True)
        time.sleep(0.3)  # 等待 DWM 合成完成
        
        # ── Win32 PrintWindow 截图 ──
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        _configure_win32(user32, gdi32)
        
        # 获取窗口尺寸
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise RuntimeError('GetWindowRect failed')
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            raise RuntimeError('Maya window has no image area')
        
        # 创建兼容的内存 DC 和位图
        hdc_window = hdc_mem = hbm = old = None
        try:
            hdc_window = user32.GetDC(hwnd)
            if not hdc_window:
                raise RuntimeError('GetDC failed')
            hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
            hbm = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
            if not hdc_mem or not hbm:
                raise RuntimeError('Could not allocate capture bitmap')
            old = gdi32.SelectObject(hdc_mem, hbm)
            if not old or old == ctypes.c_void_p(-1).value:
                old = None
                raise RuntimeError('SelectObject failed')
            # PW_RENDERFULLCONTENT; never substitute this for panel scope.
            if not user32.PrintWindow(hwnd, hdc_mem, 0x2):
                raise RuntimeError('PrintWindow failed')
            gdi32.SelectObject(hdc_mem, old)
            old = None
            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth, bmi.biHeight = w, -h
            bmi.biPlanes, bmi.biBitCount = 1, 32
            buf = ctypes.create_string_buffer(w * h * 4)
            if gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bmi), 0) != h:
                raise RuntimeError('GetDIBits returned incomplete pixels')
            img = QtGui.QImage(buf, w, h, w * 4, QtGui.QImage.Format_RGB32)
            if not img.save(save_path, 'PNG'):
                raise RuntimeError('Could not save captured PNG')
        finally:
            if old and hdc_mem:
                gdi32.SelectObject(hdc_mem, old)
            if hbm:
                gdi32.DeleteObject(hbm)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            if hdc_window:
                user32.ReleaseDC(hwnd, hdc_window)
        
        if not temp_path.exists():
            raise RuntimeError("截图文件未生成")
        
        return make_receipt(
            api_id='maya_capture_viewport',
            status='SUCCESS',
            start_time=t0,
            summary_action="抓取 Maya 窗口（Win32 PrintWindow）",
            output={'output_path': save_path},
            items=[{'name': '截图路径', 'detail': save_path}],
            report_content=f"截图已保存至: {save_path}\n分辨率: {w}x{h}"
        )
    
    except Exception as e:
        return make_receipt(
            api_id='maya_capture_viewport',
            status='ERROR',
            start_time=t0,
            summary_action="抓取视口失败",
            error=str(e),
            recovery_hint=traceback.format_exc()
        )


def _configure_win32(user32, gdi32):
    """Use pointer-sized Windows handles in 64-bit Maya."""
    wt = ctypes.wintypes
    signatures = [
        (user32.GetWindowRect, [wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
        (user32.GetDC, [wt.HWND], wt.HDC),
        (user32.ReleaseDC, [wt.HWND, wt.HDC], ctypes.c_int),
        (user32.PrintWindow, [wt.HWND, wt.HDC, wt.UINT], wt.BOOL),
        (gdi32.CreateCompatibleDC, [wt.HDC], wt.HDC),
        (gdi32.CreateCompatibleBitmap, [wt.HDC, ctypes.c_int, ctypes.c_int], wt.HANDLE),
        (gdi32.SelectObject, [wt.HDC, wt.HANDLE], wt.HANDLE),
        (gdi32.DeleteObject, [wt.HANDLE], wt.BOOL),
        (gdi32.DeleteDC, [wt.HDC], wt.BOOL),
        (gdi32.GetDIBits, [wt.HDC, wt.HANDLE, wt.UINT, wt.UINT, ctypes.c_void_p,
                          ctypes.POINTER(BITMAPINFOHEADER), wt.UINT], ctypes.c_int),
    ]
    for function, args, result in signatures:
        function.argtypes, function.restype = args, result


def _request(params):
    scope, panel = params.get('scope', 'window'), params.get('panel', '')
    if scope not in ('window', 'viewport'):
        raise ValueError('scope must be window or viewport')
    if not isinstance(panel, str):
        raise ValueError('panel must be a string')
    if scope == 'window' and panel:
        raise ValueError('panel is only valid with scope=viewport')
    return scope, panel


def _image_size(params, scope):
    size = params.get('image_size', [])
    if not isinstance(size, (list, tuple)) or (size and (
            len(size) != 2 or any(type(v) is not int or v < 32 or v > 4096 or v % 4 for v in size))):
        raise ValueError('image_size must be [] or [width,height], multiples of 4 from 32 to 4096')
    if size and scope != 'viewport':
        raise ValueError('image_size requires scope=viewport')
    if size and params.get('grounding', False):
        raise ValueError('Custom image_size is inspection-only; pixel grounding requires native viewport dimensions')
    return tuple(size)


def _visible_panels(cmds):
    visible = set(cmds.getPanel(visiblePanels=True) or [])
    return [p for p in (cmds.getPanel(type='modelPanel') or []) if p in visible]


def _resolve_panel(cmds, requested):
    visible = _visible_panels(cmds)
    if requested:
        if requested not in visible:
            raise ValueError('panel must name a visible modelPanel')
        return requested
    focused = cmds.getPanel(withFocus=True)
    if focused in visible:
        return focused
    if len(visible) == 1:
        return visible[0]
    raise ValueError('Specify panel when there is no unique visible/focused modelPanel')


def _view_metadata(cmds, panel):
    camera = cmds.modelPanel(panel, q=True, camera=True)
    paths = cmds.ls(camera, long=True) or []
    if len(paths) != 1:
        raise ValueError('Panel camera does not resolve to one native DAG path')
    return {
        'panel': panel, 'camera': paths[0],
        'camera_world_matrix': cmds.xform(paths[0], q=True, worldSpace=True, matrix=True),
        'renderer': cmds.modelEditor(panel, q=True, rendererName=True),
        'display_appearance': cmds.modelEditor(panel, q=True, displayAppearance=True),
        'wireframe_on_shaded': cmds.modelEditor(panel, q=True, wireframeOnShaded=True),
        'textures': cmds.modelEditor(panel, q=True, displayTextures=True),
        'lighting': cmds.modelEditor(panel, q=True, displayLights=True),
        'exposure': cmds.modelEditor(panel, q=True, exposure=True),
        'gamma': cmds.modelEditor(panel, q=True, gamma=True),
    }


def _observation(cmds, panels):
    return {
        'scene': cmds.file(q=True, sceneName=True),
        'frame': cmds.currentTime(q=True),
        'maya_api_version': cmds.about(apiVersion=True),
        'views': [_view_metadata(cmds, p) for p in panels],
    }


def _output_path(payload, scope):
    root = Path(REPOSITORY_ROOT).resolve()
    run_dir = payload.get('run_dir')
    if run_dir:
        run = Path(run_dir).resolve()
        if not any(run.is_relative_to(root / folder) for folder in ('projects', 'runs')):
            raise ValueError('run_dir must be inside managed projects or runs')
        directory = run / 'outputs' / 'captures'
    else:
        directory = root / 'runs' / 'captures'
    directory = directory.resolve()
    if not any(directory.is_relative_to(root / folder) for folder in ('projects', 'runs')):
        raise ValueError('Capture output must remain inside managed projects or runs')
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f'{scope}_capture_{uuid4().hex}.png'


def _png_dimensions(path):
    with path.open('rb') as stream:
        if stream.read(8) != b'\x89PNG\r\n\x1a\n':
            raise RuntimeError('Capture did not produce a PNG image')
        dimensions, has_data = None, False
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise RuntimeError('Capture produced a truncated PNG')
            length, kind = struct.unpack('>I4s', header)
            if dimensions is None and (kind != b'IHDR' or length != 13):
                raise RuntimeError('Capture PNG has no valid IHDR')
            checksum, remaining, data = zlib.crc32(kind), length, b''
            while remaining:
                block = stream.read(min(remaining, 65536))
                if not block:
                    raise RuntimeError('Capture produced a truncated PNG chunk')
                if kind == b'IHDR':
                    data += block
                checksum = zlib.crc32(block, checksum)
                remaining -= len(block)
            crc = stream.read(4)
            if len(crc) != 4 or struct.unpack('>I', crc)[0] != checksum:
                raise RuntimeError('Capture PNG checksum failed')
            if kind == b'IHDR':
                if dimensions is not None:
                    raise RuntimeError('Capture PNG has repeated IHDR')
                dimensions = struct.unpack('>II', data[:8])
                if not all(dimensions):
                    raise RuntimeError('Capture produced an empty image')
            elif kind == b'IDAT':
                has_data = has_data or length > 0
            elif kind == b'IEND':
                if length or not has_data or stream.read(1):
                    raise RuntimeError('Capture PNG has no valid image ending')
                decoded = _decoded_dimensions(path)
                if decoded != dimensions:
                    raise RuntimeError('Decoded PNG size differs from its header')
                return decoded


def _decoded_dimensions(path):
    """Reuse Maya's bundled decoder instead of treating a valid header as pixels."""
    try:
        from PySide6 import QtGui
    except ImportError:
        from PySide2 import QtGui
    reader = QtGui.QImageReader(path.as_posix())
    image = reader.read()
    if image.isNull():
        raise RuntimeError(f'Capture PNG cannot be decoded: {reader.errorString()}')
    return image.width(), image.height()


def _grounding_preflight(params):
    if type(params.get('grounding', False)) is not bool:
        raise ValueError('grounding must be boolean')
    if params.get('grounding', False):
        from cgi_pipeline.capabilities.maya.scene_observation import require_main_thread
        require_main_thread()


def _grounding_request(cmds, params, scope, requested):
    if not params.get('grounding', False):
        if params.get('targets'):
            raise ValueError('targets require grounding=true')
        return None
    if scope != 'viewport' or not requested:
        raise ValueError('grounding requires scope=viewport and an explicit panel')
    from cgi_pipeline.capabilities.maya import scene_observation
    return scene_observation.snapshot(
        cmds, requested, params.get('targets', []),
        max_meshes=params.get('max_meshes', 1000),
        max_vertices=params.get('max_vertices', 250000))


def _publish_grounding(cmds, params, scope, panel, state, path, width, height):
    if state is None:
        return None
    after = _grounding_request(cmds, params, scope, panel)
    if after != state:
        raise RuntimeError('Supported geometry or view changed during capture; no grounding published')
    from cgi_pipeline.capabilities.maya import scene_observation
    reference = scene_observation.publish(state, path, width, height)
    return {'reference': reference, 'targets': state['targets'],
            'session': state['session'], 'view': state['view'],
            'mesh_count': len(state['meshes']), 'limits': state['limits'],
            'appearance_unsupported_mesh_count': state['counts']['unsupported_mesh_count'],
            'scope': state['scope']}


def execute(payload: dict) -> dict:
    started = time.time()
    output = {}
    try:
        params = payload.get('parameters', {})
        _grounding_preflight(params)
        scope, requested = _request(params)
        image_size = _image_size(params, scope)
        import maya.cmds as cmds
        if cmds.about(batch=True):
            raise RuntimeError('Capture requires a Maya GUI session; batch/mayapy is unsupported')
        panel = _resolve_panel(cmds, requested) if scope == 'viewport' else ''
        panels = [panel] if panel else _visible_panels(cmds)
        if params.get('grounding', False) and scope == 'viewport' and requested:
            # Native viewFit can leave M3dView matrices pending until evaluation.
            # Establish the actual evaluated view before recording its first state.
            cmds.refresh(force=True)
        before = _observation(cmds, panels)
        grounded = _grounding_request(cmds, params, scope, requested)
        path = _output_path(payload, scope)
        if panel:
            cmds.playblast(editorPanelName=panel, frame=before['frame'], format='image',
                           compression='png', completeFilename=path.as_posix(),
                           viewer=False, percent=100, offScreen=True, throwOnError=True,
                           **({'widthHeight':image_size} if image_size else {}))
        else:
            receipt = _execute_window({**payload, '_capture_output_path': path.as_posix()})
            if receipt['status'] != 'SUCCESS':
                return receipt
        width, height = _png_dimensions(path)
        output = {'output_path': path.as_posix(), 'observation': {
            **before, 'scope': scope, 'backend': 'maya.playblast' if panel else 'win32.PrintWindow',
            'width': width, 'height': height,
        }}
        if image_size and (width, height) != image_size:
            raise RuntimeError(f'Native capture size {(width,height)} differs from requested {image_size}; use a supported size')
        after = _observation(cmds, panels)
        if after != before:
            output['observation_after'] = after
            raise RuntimeError('Scene or view changed during capture; reacquire observation')
        if grounded is not None:
            output['grounding'] = _publish_grounding(
                cmds, params, scope, panel, grounded, path, width, height)
        return make_receipt('maya_capture_viewport', 'SUCCESS', started,
                            summary_action=f'Capture Maya {scope}', output=output,
                            items=[{'name': '截图路径', 'detail': path.as_posix()}])
    except Exception as exc:
        return make_receipt('maya_capture_viewport', 'ERROR', started,
                            summary_action='Capture Maya failed', output=output,
                            error=str(exc), recovery_hint=traceback.format_exc())
