# ── 任务沙盒场景保存（带版本防覆盖 + 路径保护） ──

import maya.cmds as cmds
import os
import time

from cgi_pipeline.core.path_guard import is_protected_path
from cgi_pipeline.core.receipt import make_receipt
from cgi_pipeline.core.scene_files import (directory_lock, is_managed_path as _is_managed_path,
                                           next_version_path as _next_version_path)
from cgi_pipeline.paths import REPOSITORY_ROOT


def get_next_version_path(filepath: str) -> str:
    """Compatibility entry for the shared scene-file version naming owner."""
    return _next_version_path(filepath)


def _generate_report(*args, **kwargs):
    # 此函数已弃用，报告统一由 cgi_pipeline.core.tasks 处理。
    return ""


def _is_under_project_sandbox(path: str) -> bool:
    """后台保存只允许落在项目沙盒目录内。"""
    return _is_managed_path(path, REPOSITORY_ROOT)


def execute(payload: dict) -> dict:
    """
    保存当前沙盒场景。

    后台 pipeline 中，源文件已由调度层复制到任务沙盒并从沙盒打开。
    本API只负责把当前 DCC 内存状态落盘到沙盒，不做人工决策，也不写生产路径。
    """
    params = payload.get('parameters', {})
    save_path = params.get('save_path', '').strip()
    t0 = time.time()

    current_scene = cmds.file(query=True, sceneName=True) or ''
    scene_name = os.path.basename(current_scene or save_path or 'untitled')

    if not current_scene and not save_path:
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
    current_scene_norm = current_scene.replace('\\', '/') if current_scene else ''

    if is_protected_path(save_path) or (current_scene and is_protected_path(current_scene)):
        blocked_target = save_path if is_protected_path(save_path) else current_scene
        return make_receipt(
            'save_scene', 'BLOCKED', t0,
            summary_input=scene_name,
            summary_action='保存被拦截 — 受保护路径',
            error=f'路径 "{blocked_target}" 位于受保护区域。后台任务只允许写入任务沙盒。',
            output={'result': {'blocked_target': blocked_target}},
        )

    if current_scene and not _is_under_project_sandbox(current_scene_norm):
        return make_receipt(
            'save_scene', 'BLOCKED', t0,
            summary_input=scene_name,
            summary_action='保存被拦截 — 当前场景不在任务沙盒',
            error=f'当前场景 "{current_scene}" 不在项目任务沙盒内。后台 pipeline 禁止保存沙盒外场景。',
            output={'result': {'blocked_target': current_scene}},
        )

    if not _is_under_project_sandbox(save_path):
        return make_receipt(
            'save_scene', 'BLOCKED', t0,
            summary_input=scene_name,
            summary_action='保存被拦截 — 目标不在任务沙盒',
            error=f'目标路径 "{save_path}" 不在项目任务沙盒内。后台 pipeline 只允许写入任务沙盒。',
            output={'result': {'blocked_target': save_path}},
        )

    try:
        # 1. 确保目标文件夹存在
        out_dir = os.path.dirname(save_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
            
        # 2. 版本防覆盖检测（加锁保证原子性）
        final_save_path = save_path
        with directory_lock(out_dir or '.', timeout=10):
            # 强制升版本：只要路径存在，一律递增，禁止覆盖！
            final_save_path = get_next_version_path(save_path)

            # 3. 最终保存路径再检查一次
            if is_protected_path(final_save_path) or not _is_under_project_sandbox(final_save_path):
                reason = f'版本递增后路径 "{final_save_path}" 在受保护区域或任务沙盒之外。'
                return make_receipt(
                    'save_scene', 'BLOCKED', t0,
                    summary_input=scene_name,
                    summary_action='保存被拦截 — 版本递增后仍在受保护区域',
                    error=reason,
                    output={'result': {'blocked_target': final_save_path}},
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
            output={'output_path': final_save_path},
        )
        return receipt

    except Exception as e:
        error_detail = str(e)
        return make_receipt(
            'save_scene', 'ERROR', t0,
            summary_input=scene_name,
            error=error_detail,
        )
