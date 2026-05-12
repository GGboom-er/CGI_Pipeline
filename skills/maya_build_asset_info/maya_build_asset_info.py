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
from core.asset_info_schema import make_empty_info, make_mesh_entry
from core.path_guard import is_protected_path
from core.run_archive import create_run_dir


def _as_long_mesh_shape(node_or_plug):
    """将节点或 plug 解析为唯一的 long mesh shape；解析不到唯一值时返回 None。"""
    node = str(node_or_plug).split('.', 1)[0]
    matches = cmds.ls(node, long=True, type='mesh') or []
    return matches[0] if len(matches) == 1 else None


def _leaf(node_path):
    return str(node_path).split('|')[-1]


def _expected_shape_name(transform):
    return _leaf(transform) + "Shape"


def _expected_orig_name(transform):
    return _leaf(transform) + "ShapeOrig"


def _same_parent_shape(candidate, transform):
    if not candidate or not cmds.objExists(candidate):
        return False
    candidate_long = (cmds.ls(candidate, long=True) or [candidate])[0]
    parent = cmds.listRelatives(candidate_long, parent=True, fullPath=True) or []
    return bool(parent and parent[0] == transform)


def _child_mesh_shapes(transform):
    return cmds.listRelatives(
        transform, children=True, shapes=True, type="mesh", fullPath=True
    ) or []


def _mesh_transforms_under(root):
    """列出 root 层级下直接带 mesh shape 子物体的 transform。"""
    transforms = [root]
    transforms.extend(
        cmds.listRelatives(root, allDescendents=True, type="transform", fullPath=True) or []
    )
    result = []
    seen = set()
    for transform in transforms:
        transform_long = (cmds.ls(transform, long=True, type="transform") or [transform])[0]
        if transform_long in seen:
            continue
        seen.add(transform_long)
        if _child_mesh_shapes(transform_long):
            result.append(transform_long)
    return sorted(result, key=lambda item: (item.count("|"), item))


def _is_standard_visible_shape(shape_full, transform):
    shape_long = (cmds.ls(shape_full, long=True) or [shape_full])[0]
    if not _same_parent_shape(shape_long, transform):
        return False
    if cmds.getAttr(shape_long + ".intermediateObject"):
        return False
    return _leaf(shape_long) == _expected_shape_name(transform)


def _is_standard_orig(candidate, transform):
    """只接受当前 transform 下命名规范的 ShapeOrig 候选。"""
    if not _same_parent_shape(candidate, transform):
        return False
    candidate_long = (cmds.ls(candidate, long=True) or [candidate])[0]
    if not cmds.getAttr(candidate_long + ".intermediateObject"):
        return False
    return _leaf(candidate_long) == _expected_orig_name(transform)


def _get_shape_orig(shape_full, transform):
    """返回 shape_full 对应且唯一有效的 Orig；找不到唯一值时返回 None。"""
    all_shapes = _child_mesh_shapes(transform)
    visible_shapes = [
        s for s in all_shapes
        if not cmds.getAttr(s + ".intermediateObject")
    ]
    if len(visible_shapes) != 1:
        return None
    shape_long = (cmds.ls(shape_full, long=True) or [shape_full])[0]
    if (cmds.ls(visible_shapes[0], long=True) or [visible_shapes[0]])[0] != shape_long:
        return None
    if not _is_standard_visible_shape(shape_long, transform):
        return None

    official = []
    try:
        orig_plugs = cmds.deformableShape(shape_full, originalGeometry=True) or []
    except Exception:
        orig_plugs = []
    if isinstance(orig_plugs, str):
        orig_plugs = [orig_plugs]
    for plug in orig_plugs:
        candidate = _as_long_mesh_shape(plug)
        if _is_standard_orig(candidate, transform):
            official.append(candidate)
    official = sorted(set(official))
    if len(official) == 1:
        return official[0]
    if len(official) > 1:
        return None

    intermediates = [
        s for s in all_shapes
        if _is_standard_orig(s, transform)
    ]
    candidates = [
        s for s in intermediates
        if cmds.listConnections(
            s + ".outMesh",
            source=False,
            destination=True,
            skipConversionNodes=True,
        )
    ]
    return candidates[0] if len(candidates) == 1 else None


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


def _resolve_cache_group(cache_group):
    """按项目配置传入的候选路径查找 Maya 场景中的几何根。"""
    candidates = []
    raw = (cache_group or '').strip()
    if raw:
        candidates.append(raw)
        leaf = raw.strip('|').split('|')[-1]
        if leaf and leaf != raw:
            candidates.append(leaf)
        if leaf:
            candidates.append(f"|Group|Geometry|{leaf}")
            candidates.append(f"|Group|{leaf}")

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        matches = cmds.ls(candidate, long=True) or []
        if matches:
            return matches[0]
        if cmds.objExists(candidate):
            long_name = cmds.ls(candidate, long=True) or [candidate]
            return long_name[0]

    return None


def collect_scene_info(cache_group):
    """
    公共 API：从当前 Maya 活场景采集 cache 组下的 asset_info。

    cache_group 必须由项目配置或 workflow 传入。
    返回完整的 asset_info dict（含 meshes、source_file）。
    """
    actual_cache = _resolve_cache_group(cache_group)
    if not actual_cache:
        raise RuntimeError(f'场景中未找到 cache 组: {cache_group}')

    asset_info = make_empty_info()

    for transform_path in _mesh_transforms_under(actual_cache):
        visible_shapes = [
            shape for shape in _child_mesh_shapes(transform_path)
            if not cmds.getAttr(shape + ".intermediateObject")
        ]
        if not visible_shapes:
            continue

        for shape_full in visible_shapes:
            shape_dag = (cmds.ls(shape_full, long=True) or [shape_full])[0]

            # 顶点只从同 transform 下标准且唯一的 Orig 采集；找不到时保留 mesh 条目但输出空几何。
            shape_orig = _get_shape_orig(shape_full, transform_path)
            if not shape_orig:
                asset_info["meshes"][shape_dag] = make_mesh_entry(
                    vertices=0,
                    vert_positions=[],
                )
                continue

            vtx_count = cmds.polyEvaluate(shape_orig, vertex=True) or 0

            # 顶点坐标（OpenMaya API 2.0）
            vert_positions = []
            try:
                import maya.api.OpenMaya as om2
                sel = om2.MSelectionList()
                sel.add(shape_orig)
                dag = sel.getDagPath(0)
                fn_mesh = om2.MFnMesh(dag)
                pts = fn_mesh.getPoints(om2.MSpace.kWorld)
                for pt in pts:
                    vert_positions.extend([
                        round(pt.x, 4), round(pt.y, 4), round(pt.z, 4),
                    ])
            except Exception as e:
                raise RuntimeError(
                    f'采集 Orig 顶点坐标失败: {shape_orig} -> {type(e).__name__}: {e}'
                )

            asset_info["meshes"][shape_dag] = make_mesh_entry(
                vertices=vtx_count,
                vert_positions=vert_positions,
            )

    asset_info["source_file"] = cmds.file(query=True, sceneName=True) or ""
    return asset_info


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
