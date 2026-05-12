# skills/blender_export_abc.py
# ── Blender 导出 ABC（原子技能）──

import os
import time

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt
from core.path_guard import is_protected_path
from core.run_archive import create_run_dir


def _resolve_abc_path(payload, params):
    """推导 ABC 输出路径，优先使用沙盒 `.info`。"""
    abc_path = (params.get('abc_path') or '').strip()
    if abc_path:
        return abc_path

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

    return os.path.join(str(info_dir), f'{source_stem}.abc')


def _disable_all_modifiers(obj):
    """禁用物体的所有修改器"""
    if not hasattr(obj, 'modifiers'):
        return
    for mod in obj.modifiers:
        mod.show_viewport = False
        mod.show_render = False



def execute(payload: dict) -> dict:
    t0 = time.time()
    import bpy

    params = payload.get('parameters', {})
    abc_path = _resolve_abc_path(payload, params)
    cache_group_name = params.get('cache_group', 'cache')
    source_path = payload.get('source_path', '')

    if not abc_path:
        return make_receipt('blender_export_abc', 'ERROR', t0,
                            error='无法确定输出路径: abc_path 为空，且无法从任务沙盒推导 .info 目录')

    if is_protected_path(abc_path):
        return make_receipt('blender_export_abc', 'BLOCKED', t0,
                            error=f'输出路径 "{abc_path}" 位于只读/受保护区域，禁止写入。')

    abc_name = os.path.basename(abc_path)

    # ── MTime 缓存校验（加速二次读取）──
    if os.path.exists(abc_path) and source_path and os.path.exists(source_path):
        if os.path.getmtime(abc_path) >= os.path.getmtime(source_path):
            return make_receipt(
                skill_id='blender_export_abc',
                status='SUCCESS',
                start_time=t0,
                summary_input=abc_name,
                summary_action='命中缓存，跳过导出',
                summary_count=1,
                summary_label='cache',
                outputs={'output_path': abc_path},
            )

    # 确保输出目录存在
    out_dir = os.path.dirname(abc_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    # ── 查找 cache 组 ──
    # 兼容 Maya 风格的大纲路径 (例如 "|Group|cache") -> "cache"
    blender_obj_name = cache_group_name.split('|')[-1]
    cache_grp = bpy.data.objects.get(blender_obj_name)
    if not cache_grp:
        objs = [o.name for o in bpy.data.objects][:20]
        return make_receipt('blender_export_abc', 'ERROR', t0,
                            error=f"场景中未找到 '{cache_group_name}' 组。当前场景对象: {objs}, filepath: {bpy.data.filepath}")

    # 全局强制显示
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_set(False)
        obj.hide_render = False

    bpy.ops.object.select_all(action='DESELECT')
    export_objects = []

    def collect_hierarchy(parent):
        export_objects.append(parent)
        for child in parent.children:
            collect_hierarchy(child)

    collect_hierarchy(cache_grp)

    for obj in export_objects:
        obj.select_set(True)
        if obj.type == 'MESH':
            _disable_all_modifiers(obj)

    # ── 导出 ABC（含 FaceSet）──
    try:
        bpy.ops.wm.alembic_export(
            filepath=abc_path,
            start=1, end=1,
            selected=True, flatten=False,
            global_scale=100.0,
            uvs=True, normals=True,
            face_sets=False,
            export_custom_properties=True,
            as_background_job=False,
            evaluation_mode='VIEWPORT'
        )
    except Exception as e:
        return make_receipt('blender_export_abc', 'ERROR', t0,
                            summary_input=abc_name,
                            error=f'导出崩溃: {e}')

    mesh_count = sum(1 for o in export_objects if o.type == 'MESH')

    return make_receipt(
        skill_id='blender_export_abc',
        status='SUCCESS',
        start_time=t0,
        summary_input=abc_name,
        summary_action=f'Blender ABC 导出',
        summary_count=mesh_count,
        summary_label='mesh',
        outputs={'output_path': abc_path},
    )
