# skills/save_scene.py
# ── 任务沙盒场景保存（带版本防覆盖 + 路径保护） ──

import maya.cmds as cmds
import os
import re
import sys
import time
from pathlib import Path

# 确保项目根在 sys.path 中（mayapy 独立进程需要）
from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.path_guard import is_protected_path, ProtectedPathError
from core.receipt import make_receipt


try:
    from filelock import FileLock
except ImportError:
    from contextlib import contextmanager

    @contextmanager
    def _noop_lock(*_a, **_kw):
        yield

    class FileLock:
        def __init__(self, *a, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass


def get_next_version_path(filepath: str) -> str:
    """
    给定一个路径，如果该路径已存在且包含 _vXXX 后缀，则自动递增版本号。
    例如：asset_v001.ma -> asset_v002.ma
    """
    path_obj = Path(filepath)
    if not path_obj.exists():
        return filepath
        
    directory = path_obj.parent
    stem = path_obj.stem
    ext = path_obj.suffix

    # 正则提取末尾的 _vXXX 或 vXXX
    match = re.search(r'(_?v)(\d+)$', stem, re.IGNORECASE)
    if match:
        prefix = match.group(1)
        version_num = int(match.group(2))
        base_name = stem[:match.start()]
        
        while path_obj.exists():
            version_num += 1
            # 补齐对应的 0 位数，例如 001 -> 002
            padding = len(match.group(2))
            new_version_str = f"{version_num:0{padding}d}"
            new_stem = f"{base_name}{prefix}{new_version_str}"
            path_obj = directory / f"{new_stem}{ext}"
    else:
        # 如果没有版本号，强加 _v001
        base_name = stem
        version_num = 1
        while path_obj.exists():
            new_stem = f"{base_name}_v{version_num:03d}"
            path_obj = directory / f"{new_stem}{ext}"
            version_num += 1

    return str(path_obj.resolve())


def _generate_report(*args, **kwargs):
    # 此函数已弃用，报告统一由核心管线的 core/tasks.py 处理
    return ""


def _is_under_project_sandbox(path: str) -> bool:
    """后台保存只允许落在项目沙盒目录内。"""
    if not path:
        return False
    try:
        p = Path(path).resolve()
        root = Path(_PROJECT_ROOT).resolve()
        projects_root = root / 'projects'
        runs_root = root / 'runs'
        return p.is_relative_to(projects_root) or p.is_relative_to(runs_root)
    except Exception:
        norm = path.replace('\\', '/').lower()
        root = str(_PROJECT_ROOT).replace('\\', '/').rstrip('/').lower()
        return norm.startswith(f'{root}/projects/') or norm.startswith(f'{root}/runs/')


def execute(payload: dict) -> dict:
    """
    保存当前沙盒场景。

    后台 pipeline 中，源文件已由调度层复制到任务沙盒并从沙盒打开。
    本技能只负责把当前 DCC 内存状态落盘到沙盒，不做人工决策，也不写生产路径。
    """
    params = payload.get('parameters', {})
    save_path = params.get('save_path', '').strip()
    t0 = time.time()

    current_scene = cmds.file(query=True, sceneName=True) or ''
    scene_name = os.path.basename(current_scene or save_path or 'untitled')

    if not current_scene:
        return make_receipt(
            'save_scene', 'ERROR', t0,
            summary_input='untitled',
            error='当前场景未命名，后台 save_scene 无法确定沙盒保存目标。'
        )

    if not save_path:
        save_path = current_scene

    if not os.path.isabs(save_path):
        base_dir = os.path.dirname(current_scene)
        save_path = os.path.join(base_dir, save_path)

    save_path = save_path.replace('\\', '/')
    current_scene_norm = current_scene.replace('\\', '/')

    if is_protected_path(save_path) or is_protected_path(current_scene):
        blocked_target = save_path if is_protected_path(save_path) else current_scene
        return make_receipt(
            'save_scene', 'BLOCKED', t0,
            summary_input=scene_name,
            summary_action='保存被拦截 — 受保护路径',
            error=f'路径 "{blocked_target}" 位于受保护区域。后台任务只允许写入任务沙盒。',
            outputs={'blocked_target': blocked_target},
        )

    if not _is_under_project_sandbox(current_scene_norm):
        return make_receipt(
            'save_scene', 'BLOCKED', t0,
            summary_input=scene_name,
            summary_action='保存被拦截 — 当前场景不在任务沙盒',
            error=f'当前场景 "{current_scene}" 不在项目任务沙盒内。后台 pipeline 禁止保存沙盒外场景。',
            outputs={'blocked_target': current_scene},
        )

    if not _is_under_project_sandbox(save_path):
        return make_receipt(
            'save_scene', 'BLOCKED', t0,
            summary_input=scene_name,
            summary_action='保存被拦截 — 目标不在任务沙盒',
            error=f'目标路径 "{save_path}" 不在项目任务沙盒内。后台 pipeline 只允许写入任务沙盒。',
            outputs={'blocked_target': save_path},
        )

    try:
        # 1. 确保目标文件夹存在
        out_dir = os.path.dirname(save_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
            
        # 2. 版本防覆盖检测（加锁保证原子性）
        final_save_path = save_path
        version_lock = FileLock(os.path.join(out_dir or '.', '.version_lock'), timeout=10)
        with version_lock:
            # 强制升版本：只要路径存在，一律递增，禁止覆盖！
            final_save_path = get_next_version_path(save_path)

            # 3. 最终保存路径再检查一次
            if is_protected_path(final_save_path):
                reason = f'版本递增后路径 "{final_save_path}" 仍在受保护区域。'
                return make_receipt(
                    'save_scene', 'BLOCKED', t0,
                    summary_input=scene_name,
                    summary_action='保存被拦截 — 版本递增后仍在受保护区域',
                    error=reason,
                    outputs={'blocked_target': final_save_path},
                )

            # 4. 决定保存类型 .ma 或 .mb
            file_type = "mayaAscii" if final_save_path.lower().endswith('.ma') else "mayaBinary"

            # 5. 执行重命名和保存
            cmds.file(rename=final_save_path)
            cmds.file(save=True, type=file_type)

        receipt = make_receipt(
            'save_scene', 'SUCCESS', t0,
            summary_input=scene_name,
            summary_action='场景已保存到任务沙盒',
            outputs={'output_path': final_save_path},
        )
        return receipt

    except Exception as e:
        error_detail = str(e)
        return make_receipt(
            'save_scene', 'ERROR', t0,
            summary_input=scene_name,
            error=error_detail,
        )

