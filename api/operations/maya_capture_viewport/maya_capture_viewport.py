"""
Maya 视口截图API — 使用 Windows PrintWindow API 精确捕获完整窗口
 
技术方案：
- Qt grab()：无法获取 OpenGL framebuffer，视口区域为黑色 ✗
- playblast：不继承视口背景色，白色背景与用户所见不一致 ✗  
- Win32 PrintWindow + PW_RENDERFULLCONTENT：直接从 DWM 合成器抓取，
  包含 OpenGL/DirectX 渲染内容，100% 匹配用户所见 ✓
"""
import os
import time
import traceback
import ctypes
import ctypes.wintypes
from pathlib import Path

from core.bootstrap import PROJECT_ROOT
from core.receipt import make_receipt


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


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    
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
        sandbox_dir = Path(PROJECT_ROOT) / 'runs' / 'captures'
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        temp_path = sandbox_dir / f"viewport_capture_{timestamp}.png"
        save_path = str(temp_path).replace('\\', '/')
        
        # 获取 Maya 主窗口的 Win32 原生句柄 (HWND)
        main_window_ptr = omui.MQtUtil.mainWindow()
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
        
        # 获取窗口尺寸
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        
        # 创建兼容的内存 DC 和位图
        hdc_window = user32.GetDC(hwnd)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
        hbm = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
        gdi32.SelectObject(hdc_mem, hbm)
        
        # PW_RENDERFULLCONTENT (0x2)：Windows 8.1+ 支持
        # 直接从 DWM 合成器抓取，包含 OpenGL/DirectX 渲染的 3D 内容
        PW_RENDERFULLCONTENT = 0x00000002
        success = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        
        if not success:
            raise RuntimeError("PrintWindow 失败，可能是权限不足或窗口不可见")
        
        # 从位图中提取像素数据
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # 负值 = 自顶向下扫描
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB
        
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bmi), 0)
        
        # 转为 QImage 并保存
        img = QtGui.QImage(buf, w, h, w * 4, QtGui.QImage.Format_ARGB32)
        img.save(save_path, 'PNG')
        
        # 清理 GDI 资源
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)
        
        if not temp_path.exists():
            raise RuntimeError("截图文件未生成")
        
        return make_receipt(
            api_id='maya_capture_viewport',
            status='SUCCESS',
            start_time=t0,
            summary_action="抓取 Maya 窗口（Win32 PrintWindow）",
            outputs={'output_path': save_path},
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
