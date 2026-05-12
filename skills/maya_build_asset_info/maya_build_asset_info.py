# skills/maya_build_asset_info.py
# ── Maya 资产信息采集 ──
# 输出结构与 blender_build_asset_info 完全一致，遵循 core/asset_info_schema.py
# JSON 只存轻量几何指纹（顶点数+顶点坐标）。拓扑/UV/法线从 ABC 获取，
# 材质与贴图由独立材料技能负责。

import os
import json
import time
import maya.cmds as cmds

from core.receipt import make_receipt
from core.path_guard import is_protected_path
from core.run_archive import create_run_dir
from dccs.maya.asset_info_collector import collect_scene_info


def _resolve_info_path(payload, params):
    """推导 `_info.json` 输出路径，优先使用沙盒 `.info`。"""
    info_path = (params.get('info_path') or '').strip()
    if info_path:
        return info_path

    source_path = payload.get('source_path', '') or cmds.file(query=True, sceneName=True) or ''
    source_stem = os.path.splitext(os.path.basename(source_path or 'asset'))[0] or 'asset'

    info_dir = (
        params.get('info_dir')
        or payload.get('info_dir')
        or (payload.get('extra_params') or {}).get('info_dir')
    )
    if not info_dir:
        run_dir = payload.get('run_dir') or (payload.get('extra_params') or {}).get('run_dir')
        if not run_dir and payload.get('task_id'):
            run_dir = create_run_dir(
                payload.get('task_id'),
                payload.get('project', 'default'),
                payload.get('asset_name', 'untitled'),
                payload.get('submitted_at'),
            )
        if run_dir:
            info_dir = os.path.join(str(run_dir), '.info')

    if not info_dir:
        return ''

    return os.path.join(str(info_dir), f'{source_stem}_info.json')


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    info_path = _resolve_info_path(payload, params)
    cache_group = (params.get('cache_group') or '').strip()

    if not info_path:
        return make_receipt('maya_build_asset_info', 'ERROR', t0,
                            error='无法确定输出路径: info_path 为空，且无法从任务沙盒推导 .info 目录')

    if not cache_group:
        return make_receipt('maya_build_asset_info', 'ERROR', t0,
                            summary_input=os.path.basename(info_path),
                            error='缺少必填参数 cache_group。请由 workflow 从项目配置传入。')

    if is_protected_path(info_path):
        return make_receipt('maya_build_asset_info', 'BLOCKED', t0,
                            error=f'输出路径 "{info_path}" 位于只读/受保护区域，禁止写入。')

    info_name = os.path.basename(info_path)

    # ── 采集（复用 collect_scene_info）──
    try:
        asset_info = collect_scene_info(cache_group)
    except RuntimeError as e:
        return make_receipt('maya_build_asset_info', 'ERROR', t0, error=str(e))

    # ── 写入 JSON ──
    out_dir = os.path.dirname(info_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    try:
        with open(info_path, 'w', encoding='utf-8') as fp:
            json.dump(asset_info, fp, ensure_ascii=False, indent=2)
    except Exception as e:
        return make_receipt('maya_build_asset_info', 'ERROR', t0,
                            summary_input=info_name,
                            error=f'写入 JSON 失败: {e}')

    mesh_count = len(asset_info["meshes"])

    return make_receipt(
        skill_id='maya_build_asset_info',
        status='SUCCESS',
        start_time=t0,
        summary_input=info_name,
        summary_action=f'Maya mesh 信息采集 — {mesh_count} mesh',
        summary_count=mesh_count,
        summary_label='mesh',
        outputs={'output_path': info_path},
    )
