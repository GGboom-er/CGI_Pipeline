# ── Blender 资产信息采集（统一提取API）──
#
# 统一标尺：厘米（cm）、Y 轴向上、世界空间坐标。
# 与 maya_build_asset_info.py (Maya) 对称：输出格式完全一致。

import os
import json
import time

from cgi_pipeline.core.receipt import make_receipt
from cgi_pipeline.core.asset_info_schema import make_empty_info, make_mesh_entry
from cgi_pipeline.core.path_guard import is_protected_path
from cgi_pipeline.core.run_archive import create_run_dir


def _build_dag_path(obj, root_name):
    """构建从采集根开始的 DAG 路径（含根节点），与 Maya 侧格式统一。"""
    path = []
    current = obj
    while current:
        path.append(current.name)
        current = current.parent
    path.reverse()
    try:
        ci = path.index(root_name)
        return "|".join(path[ci:])
    except ValueError:
        return "|".join(path)


def _resolve_info_path(payload, params):
    """推导 `_info.json` 输出路径，优先使用沙盒 `.info`。"""
    info_path = (params.get('info_path') or '').strip()
    if info_path:
        return info_path

    source_path = payload.get('source_path', '') or ''
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
    import bpy

    params = payload.get('parameters', {})
    info_path = _resolve_info_path(payload, params)
    cache_group_name = (params.get('cache_group') or '').strip()
    source_path = payload.get('source_path', '')

    if not info_path:
        return make_receipt('blender_build_asset_info', 'ERROR', t0,
                            error='无法确定输出路径: info_path 为空，且无法从任务沙盒推导 .info 目录')

    if not cache_group_name:
        return make_receipt('blender_build_asset_info', 'ERROR', t0,
                            summary_input=os.path.basename(info_path),
                            error='缺少必填参数 cache_group。请由 workflow 从项目配置传入。')

    if is_protected_path(info_path):
        return make_receipt('blender_build_asset_info', 'BLOCKED', t0,
                            error=f'输出路径 "{info_path}" 位于只读/受保护区域，禁止写入。')

    info_name = os.path.basename(info_path)

    # 确保输出目录存在
    out_dir = os.path.dirname(info_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    # ── 查找 cache 组 ──
    blender_obj_name = cache_group_name.split('|')[-1]
    cache_grp = bpy.data.objects.get(blender_obj_name)
    if not cache_grp:
        return make_receipt('blender_build_asset_info', 'ERROR', t0,
                            error=f"场景中未找到 '{blender_obj_name}' 组")

    # ── 收集 cache 下所有对象 ──
    export_objects = []

    def collect_hierarchy(parent):
        export_objects.append(parent)
        for child in parent.children:
            collect_hierarchy(child)

    collect_hierarchy(cache_grp)

    # ── 构建资产信息 ──
    asset_info = make_empty_info()

    # 确保 Blender 使用米（默认），我们手动转厘米
    for obj in export_objects:
        if obj.type != 'MESH':
            continue

        mesh_data = obj.data
        dag_path = _build_dag_path(obj, blender_obj_name) + "|" + mesh_data.name

        # ── 顶点坐标 — 世界空间 ──
        # Blender: Z-up, 米 → Maya: Y-up, 厘米
        # X→X, Z→Y, -Y→Z, ×100
        verts = []
        world_matrix = obj.matrix_world
        for v in mesh_data.vertices:
            world_pos = world_matrix @ v.co
            verts.extend([
                round(world_pos.x * 100.0, 4),
                round(world_pos.z * 100.0, 4),
                round(-world_pos.y * 100.0, 4),
            ])

        asset_info["meshes"][dag_path] = make_mesh_entry(
            vertices=len(mesh_data.vertices),
            vert_positions=verts,
        )

    # 记录来源文件
    blend_path = bpy.data.filepath or source_path
    asset_info["source_file"] = os.path.normpath(blend_path).replace("\\", "/")

    # ── 写入 JSON ──
    try:
        with open(info_path, 'w', encoding='utf-8') as fp:
            json.dump(asset_info, fp, ensure_ascii=False, indent=2)
    except Exception as e:
        return make_receipt('blender_build_asset_info', 'ERROR', t0,
                            summary_input=info_name,
                            error=f'写入 JSON 失败: {e}')

    mesh_count = len(asset_info["meshes"])

    return make_receipt(
        api_id='blender_build_asset_info',
        status='SUCCESS',
        start_time=t0,
        summary_input=info_name,
        summary_action=f'Blender mesh 信息采集 — {mesh_count} mesh',
        summary_count=mesh_count,
        summary_label='mesh',
        output={'output_path': info_path},
    )
