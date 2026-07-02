# dccs/maya/asset_info_collector.py
# Maya 场景资产信息采集公共模块。
#
# 仅供 Maya 进程内调用；不要在 pipeline/core 纯 Python 进程中导入。

import maya.cmds as cmds

from core.asset_info_schema import make_empty_info, make_mesh_entry


def normalize_cache_group_param(cache_group):
    """把配置候选根统一成分号分隔字符串。"""
    if isinstance(cache_group, (list, tuple)):
        return ";".join(str(item).strip() for item in cache_group if str(item).strip())
    return str(cache_group or "").strip()


def _as_long_mesh_shape(node_or_plug):
    """将节点或 plug 解析为唯一的 long mesh shape；解析不到唯一值时返回 None。"""
    text = str(node_or_plug or "").strip()
    if not text:
        return None
    node = text.split('.', 1)[0]
    if not node:
        return None
    matches = cmds.ls(node, long=True, type='mesh') or []
    return matches[0] if len(matches) == 1 else None


def _child_mesh_shapes(transform):
    return cmds.listRelatives(
        transform, children=True, shapes=True, type="mesh", fullPath=True
    ) or []


def mesh_transforms_under(root):
    """列出 root 全层级中自身带 mesh shape 子物体的 transform。"""
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


def _unique_mesh_from_plugs(plugs):
    if not plugs:
        return None
    if isinstance(plugs, str):
        plugs = [plugs]
    candidates = []
    for plug in plugs:
        candidate = _as_long_mesh_shape(plug)
        if candidate:
            candidates.append(candidate)
    candidates = sorted(set(candidates))
    return candidates[0] if len(candidates) == 1 else None


def _is_intermediate_mesh(candidate):
    if not candidate or not cmds.objExists(candidate):
        return False
    candidate_long = (cmds.ls(candidate, long=True, type="mesh") or [None])[0]
    if not candidate_long:
        return False
    if not cmds.getAttr(candidate_long + ".intermediateObject"):
        return False
    return True


def _has_orig_output(candidate):
    if not candidate or not cmds.objExists(candidate):
        return False
    out_mesh = cmds.listConnections(
        candidate + ".outMesh",
        source=False,
        destination=True,
        skipConversionNodes=True,
    ) or []
    world_mesh = cmds.listConnections(
        candidate + ".worldMesh",
        source=False,
        destination=True,
        skipConversionNodes=True,
    ) or []
    return bool(out_mesh or world_mesh)


def _orig_from_deformable_shape(shape_full):
    try:
        orig_plugs = cmds.deformableShape(shape_full, originalGeometry=True) or []
    except Exception:
        return None
    return _unique_mesh_from_plugs(orig_plugs)


def _orig_from_tweak_input(shape_full):
    try:
        tweaks = cmds.listConnections(f"{shape_full}.tweakLocation") or []
    except Exception:
        return None
    if not tweaks:
        return None

    orig_plugs = []
    for tweak in tweaks:
        try:
            plugs = cmds.listConnections(
                f"{tweak}.input[0].inputGeometry",
                destination=False,
                source=True,
                plugs=True,
            ) or []
        except Exception:
            continue
        orig_plugs.extend(plugs)
    return _unique_mesh_from_plugs(orig_plugs)


def _orig_from_connected_intermediate(transform):
    candidates = []
    for shape in _child_mesh_shapes(transform):
        shape_long = (cmds.ls(shape, long=True, type="mesh") or [shape])[0]
        if _is_intermediate_mesh(shape_long) and _has_orig_output(shape_long):
            candidates.append(shape_long)
    candidates = sorted(set(candidates))
    return candidates[0] if len(candidates) == 1 else None


def get_shape_orig(shape_full, transform):
    """返回 shape_full 对应且唯一有效的 Orig；找不到唯一值时返回 None。

    Orig 身份只来自 Maya 图关系，不根据 ShapeOrig 名称或数字后缀判断。
    """
    all_shapes = _child_mesh_shapes(transform)
    not_intermediate_shapes = [
        s for s in all_shapes
        if not cmds.getAttr(s + ".intermediateObject")
    ]
    if len(not_intermediate_shapes) != 1:
        return None
    shape_long = (cmds.ls(shape_full, long=True) or [shape_full])[0]
    if (cmds.ls(not_intermediate_shapes[0], long=True) or [not_intermediate_shapes[0]])[0] != shape_long:
        return None

    for finder in (
        lambda: _orig_from_deformable_shape(shape_long),
        lambda: _orig_from_tweak_input(shape_long),
        lambda: _orig_from_connected_intermediate(transform),
    ):
        candidate = finder()
        if candidate:
            return candidate
    return None


def _cache_group_candidates(cache_group):
    raw_text = normalize_cache_group_param(cache_group)
    raw_items = [item.strip() for item in raw_text.split(";") if item.strip()]
    for raw in raw_items:
        yield raw
        leaf = raw.strip('|').split('|')[-1]
        if leaf and leaf != raw:
            yield leaf
        if leaf:
            yield f"|Group|Geometry|{leaf}"
            yield f"|Group|{leaf}"


def resolve_cache_group(cache_group):
    """按项目配置传入的候选路径查找 Maya 场景中的几何根。"""
    seen = set()
    for candidate in _cache_group_candidates(cache_group):
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

    meshes 的 key 永远使用标准非 intermediate mesh shape 的绝对 DAG 路径。
    顶点数据优先取同 transform 下唯一标准 ShapeOrig(绑定 mesh 的静止态)；无 Orig(未绑定/新注入 mesh)时回退读标准 shape 本身。
    include_topology=True 时额外写 face_counts / face_indices，供 rig sync 运行时使用。
    """
    actual_cache = resolve_cache_group(cache_group)
    if not actual_cache:
        raise RuntimeError(f'场景中未找到 cache 组: {cache_group}')

    asset_info = make_empty_info()

    for transform_path in mesh_transforms_under(actual_cache):
        standard_shapes = [
            shape for shape in _child_mesh_shapes(transform_path)
            if not cmds.getAttr(shape + ".intermediateObject")
        ]
        if not standard_shapes:
            continue

        for shape_full in standard_shapes:
            shape_dag = (cmds.ls(shape_full, long=True) or [shape_full])[0]
            shape_orig = get_shape_orig(shape_full, transform_path)
            # 绑定 mesh 取 Orig(静止态)；无 Orig(未绑定/新注入 mesh)时标准 shape 本身即几何,回退直接读
            geo_shape = shape_orig or shape_dag

            try:
                fn_mesh, pts, vert_positions = _mesh_points_flat(geo_shape, precision)
            except Exception as e:
                raise RuntimeError(
                    f'采集顶点坐标失败: {geo_shape} -> {type(e).__name__}: {e}'
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
