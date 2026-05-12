# skills/export_abc.py
# ── Maya 导出 ABC ──
#
# 将当前 Maya 场景导出为 Alembic (.abc)。
# 输出含 UV、可见性，Ogawa 格式。不保留 skinCluster。

import os
import sys
import time
import traceback
import maya.cmds as cmds

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt, make_item
from core.path_guard import is_protected_path
from core.run_archive import create_run_dir


def _resolve_abc_path(payload, params):
    """推导 ABC 输出路径，优先使用沙盒 `.info`。"""
    abc_path = (params.get('abc_path') or '').strip()
    if abc_path:
        return abc_path

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

    return os.path.join(str(info_dir), f'{source_stem}.abc')


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    source_path = payload.get('source_path', '')
    frame_range = params.get('frame_range', [1, 1])
    root_nodes = params.get('root_nodes', [])
    if isinstance(root_nodes, str):
        root_nodes = [r.strip() for r in root_nodes.split(',') if r.strip()]

    frame_start = int(frame_range[0]) if len(frame_range) > 0 else 1
    frame_end = int(frame_range[1]) if len(frame_range) > 1 else frame_start

    scene = cmds.file(query=True, sceneName=True) or source_path
    if not scene:
        scene = source_path or 'untitled.ma'

    abc_path = _resolve_abc_path(payload, params)
    if not abc_path:
        return make_receipt('maya_export_abc', 'ERROR', t0,
                            error='无法确定输出路径: abc_path 为空，且无法从任务沙盒推导 .info 目录')

    if is_protected_path(abc_path):
        return make_receipt('maya_export_abc', 'BLOCKED', t0,
                            error=f'输出路径 "{abc_path}" 位于只读/受保护区域，禁止写入。')

    os.makedirs(os.path.dirname(abc_path), exist_ok=True)

    try:
        cmds.loadPlugin('AbcExport', quiet=True)
    except Exception as e:
        return make_receipt('maya_export_abc', 'ERROR', t0,
                            summary_input=os.path.basename(scene),
                            error=f'加载 AbcExport 插件失败: {e}\n{traceback.format_exc()}')

    # 构建根节点参数
    if not root_nodes:
        root_nodes = cmds.ls(assemblies=True, long=True) or []
        root_nodes = [n for n in root_nodes if n not in ('|persp', '|top', '|front', '|side')]

    root_args = ' '.join(f'-root {r}' for r in root_nodes)

    job_str = (
        f'-frameRange {frame_start} {frame_end} '
        f'-uvWrite -writeFaceSets -writeVisibility '
        f'-dataFormat ogawa '
        f'{root_args} '
        f'-file {abc_path}'
    )

    try:
        cmds.AbcExport(j=job_str)
    except Exception as e:
        return make_receipt('maya_export_abc', 'ERROR', t0,
                            summary_input=os.path.basename(scene),
                            error=f'AbcExport 失败: {e}')

    if not os.path.isfile(abc_path):
        return make_receipt('maya_export_abc', 'ERROR', t0,
                            summary_input=os.path.basename(scene),
                            error=f'导出完成但文件不存在: {abc_path}')

    size_mb = round(os.path.getsize(abc_path) / 1024 / 1024, 2)

    items = [
        make_item('帧范围', f'{frame_start}-{frame_end}'),
        make_item('根节点', ', '.join(n.split('|')[-1] for n in root_nodes)),
        make_item('文件大小', f'{size_mb} MB'),
    ]

    return make_receipt(
        skill_id='maya_export_abc',
        status='SUCCESS',
        start_time=t0,
        summary_input=os.path.basename(scene),
        summary_action=f'导出 ABC ({frame_start}-{frame_end})',
        summary_count=len(root_nodes),
        summary_label='根节点',
        items=items,
        outputs={'output_path': abc_path},
    )
