# dccs/maya/asset_info_collector.py
# Maya 场景资产信息采集公共模块。
#
# 仅供 Maya 进程内调用；不要在 pipeline/core 纯 Python 进程中导入。

import maya.cmds as cmds

from core.asset_info_schema import make_empty_info, make_mesh_entry


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


def mesh_transforms_under(root):
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


def get_shape_orig(shape_full, transform):
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


def resolve_cache_group(cache_group):
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


def _mesh_points_flat(mesh_shape, precision):
    import maya.api.OpenMaya as om2

    sel = om2.MSelectionList()
    sel.add(mesh_shape)
    dag = sel.getDagPath(0)
    fn_mesh = om2.MFnMesh(dag)
    pts = fn_mesh.getPoints(om2.MSpace.kWorld)
    vert_positions = []
    for pt in pts:
        vert_positions.extend([
            round(pt.x, precision),
            round(pt.y, precision),
            round(pt.z, precision),
        ])
    return fn_mesh, pts, vert_positions


def collect_scene_info(cache_group, include_topology=False, precision=4):
    """
    从当前 Maya 活场景采集 cache 组下的 asset_info。

    meshes 的 key 永远使用标准可见 shape 的绝对 DAG 路径。
    顶点数据来自同 transform 下唯一标准 ShapeOrig；找不到 Orig 时保留条目但写空几何。
    include_topology=True 时额外写 face_counts / face_indices，供 rig sync 运行时使用。
    """
    actual_cache = resolve_cache_group(cache_group)
    if not actual_cache:
        raise RuntimeError(f'场景中未找到 cache 组: {cache_group}')

    asset_info = make_empty_info()

    for transform_path in mesh_transforms_under(actual_cache):
        visible_shapes = [
            shape for shape in _child_mesh_shapes(transform_path)
            if not cmds.getAttr(shape + ".intermediateObject")
        ]
        if not visible_shapes:
            continue

        for shape_full in visible_shapes:
            shape_dag = (cmds.ls(shape_full, long=True) or [shape_full])[0]
            shape_orig = get_shape_orig(shape_full, transform_path)
            if not shape_orig:
                entry = make_mesh_entry(vertices=0, vert_positions=[])
                if include_topology:
                    entry["face_counts"] = []
                    entry["face_indices"] = []
                asset_info["meshes"][shape_dag] = entry
                continue

            try:
                fn_mesh, pts, vert_positions = _mesh_points_flat(shape_orig, precision)
            except Exception as e:
                raise RuntimeError(
                    f'采集 Orig 顶点坐标失败: {shape_orig} -> {type(e).__name__}: {e}'
                )

            entry = make_mesh_entry(
                vertices=len(pts),
                vert_positions=vert_positions,
            )
            if include_topology:
                face_counts, face_indices = fn_mesh.getVertices()
                entry["face_counts"] = list(face_counts)
                entry["face_indices"] = list(face_indices)
            asset_info["meshes"][shape_dag] = entry

    asset_info["source_file"] = cmds.file(query=True, sceneName=True) or ""
    return asset_info
