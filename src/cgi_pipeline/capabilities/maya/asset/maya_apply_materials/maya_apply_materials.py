# ── 材质信息消费：读取 _materials.json 并在 Maya 中按面赋予材质球 ──
#
# 与 blender_extract_materials 配对。UDIM 贴图已在 Blender 侧按象限拆分为独立条目，
# 本API直接按条目创建 lambert 材质球，无需 Maya 侧 UV 分析。

import os
import json
import time
import logging
import re

import maya.cmds as cmds

from cgi_pipeline.core.receipt import make_receipt, make_item
from cgi_pipeline.core.material_semantics import should_use_as_color_texture

logger = logging.getLogger(__name__)


PLACE2D_ATTRS = [
    "coverage", "translateFrame", "rotateFrame", "mirrorU", "mirrorV",
    "stagger", "wrapU", "wrapV", "repeatUV", "rotateUV",
    "noiseUV", "vertexUvOne", "vertexUvTwo", "vertexUvThree", "vertexCameraOne",
]

_MATERIAL_UPSTREAM_TYPES = {
    "file", "place2dTexture", "reverse", "gammaCorrect", "multiplyDivide",
    "plusMinusAverage", "remapValue", "remapColor", "blendColors",
    "bump2d", "unitConversion",
}


def _safe_node_name(value, fallback="material"):
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", str(value or ""))
    cleaned = cleaned.strip("_") or fallback
    if cleaned[0].isdigit():
        cleaned = "M_" + cleaned
    return cleaned


def _flatten_material_contract(raw):
    """Validate schema v3 Color/Alpha entries before Maya scene edits."""
    if raw.get("schema_version") != 3:
        raise ValueError(
            f"不支持的 _materials.json schema: {raw.get('schema_version')!r}; 需要 3"
        )

    materials = raw.get("materials")
    if not isinstance(materials, dict):
        raise ValueError("schema v3 必须包含 materials 对象")
    for material_name, material_data in materials.items():
        if not isinstance(material_data, dict):
            raise ValueError(f"材质 {material_name!r} 不是对象")
        color = material_data.get("color") or {}
        alpha = material_data.get("alpha") or {"type": "value", "value": 1.0}
        if color.get("type") not in {"solid", "texture"}:
            raise ValueError(f"材质 {material_name!r} 缺少有效 Color")
        if alpha.get("type") not in {"value", "texture"}:
            raise ValueError(f"材质 {material_name!r} 缺少有效 Alpha")
        for label, info in (("Color", color), ("Alpha", alpha)):
            if info.get("type") != "texture":
                continue
            path = info.get("path", "")
            if not path or not os.path.isfile(path):
                raise ValueError(f"{label} 贴图不存在: {path}")
        faces_by_mesh = material_data.get("faces_by_mesh")
        if not isinstance(faces_by_mesh, dict) or not faces_by_mesh:
            raise ValueError(f"材质 {material_name!r} 的 faces_by_mesh 为空或不是对象")
    if not materials:
        raise ValueError("_materials.json 没有任何可创建材质")
    return materials


def _ensure_render_partition_unlocked():
    """解锁场景里被锁定的 partition 成员连接（建 SG 前调用）。

    某些异常导出的 rig 把 :renderPartition 的成员连接设成锁定(connectAttr -l on)，
    新建 SG 时 Maya 自动把它插入 renderPartition.sets[-1] 会因 'Destination is locked'
    崩溃并触发整链回滚。建 SG 前先解锁这些连接，让自动插入成功；正常资产无锁定连接，
    此函数为无副作用空操作。"""
    for part in (cmds.ls(type="partition") or []):
        plugs = cmds.listConnections(part, plugs=True, connections=True) or []
        for plug in plugs:
            try:
                if cmds.getAttr(plug, lock=True):
                    cmds.setAttr(plug, lock=False)
            except Exception as e:
                logger.debug("解锁 partition 连接失败 %s: %s", plug, e)


def _create_file_texture(base_name, suffix, texture_path):
    file_node = cmds.shadingNode(
        "file", asTexture=True, name=base_name + suffix + "_file"
    )
    p2d = cmds.shadingNode(
        "place2dTexture", asUtility=True, name=base_name + suffix + "_p2d"
    )
    cmds.connectAttr(p2d + ".outUV", file_node + ".uvCoord")
    cmds.connectAttr(p2d + ".outUvFilterSize", file_node + ".uvFilterSize")
    for attr in PLACE2D_ATTRS:
        try:
            cmds.connectAttr(f"{p2d}.{attr}", f"{file_node}.{attr}", force=True)
        except Exception as exc:
            logger.debug("跳过 place2dTexture 属性连接 %s: %s", attr, exc)
    cmds.setAttr(file_node + ".fileTextureName", texture_path, type="string")
    cmds.setAttr(file_node + ".uvTilingMode", 0)
    return file_node


def _same_path(path_a, path_b):
    return os.path.normcase(os.path.normpath(path_a or "")) == os.path.normcase(
        os.path.normpath(path_b or "")
    )


def _create_material_and_sg(mat_name, color_info, alpha_info):
    """Create one fresh lambert from existing textures and scalar attributes."""
    base_name = _safe_node_name(mat_name)
    sg_name = base_name + "_SG"

    # 不查找或复用旧 SG；旧网络由 apply_materials 的清理阶段负责移除。
    shader = cmds.shadingNode("lambert", asShader=True, name=base_name)
    _ensure_render_partition_unlocked()
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg_name)
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)

    color_file = None
    if color_info.get("type") == "solid":
        rgba = color_info.get("value", [0.8, 0.8, 0.8, 1.0])
        cmds.setAttr(shader + ".color", rgba[0], rgba[1], rgba[2], type="double3")
    elif color_info.get("type") == "texture":
        tex_path = color_info.get("path", "")
        if tex_path and should_use_as_color_texture(tex_path):
            color_file = _create_file_texture(base_name, "", tex_path)
            cmds.connectAttr(color_file + ".outColor", shader + ".color", force=True)
        else:
            rgba = color_info.get("value", [0.8, 0.8, 0.8, 1.0])
            cmds.setAttr(shader + ".color", rgba[0], rgba[1], rgba[2], type="double3")

    if alpha_info.get("type") == "value":
        alpha = min(1.0, max(0.0, float(alpha_info.get("value", 1.0))))
        transparency = 1.0 - alpha
        if transparency > 0.0:
            cmds.setAttr(
                shader + ".transparency",
                transparency, transparency, transparency,
                type="double3",
            )
    elif alpha_info.get("type") == "texture":
        alpha_path = alpha_info.get("path", "")
        if color_file and _same_path(alpha_path, color_info.get("path", "")):
            alpha_file = color_file
        else:
            alpha_file = _create_file_texture(base_name, "_alpha", alpha_path)
        if alpha_info.get("channel") == "luminance":
            cmds.setAttr(alpha_file + ".alphaIsLuminance", True)
        reverse = cmds.shadingNode("reverse", asUtility=True, name=base_name + "_alphaRev")
        cmds.connectAttr(alpha_file + ".outAlpha", reverse + ".inputX", force=True)
        for channel in "RGB":
            cmds.connectAttr(
                reverse + ".outputX", shader + ".transparency" + channel, force=True
            )

    return shader, sg


def _build_mesh_lookup(meshes):
    """构建 mesh 查找表：短名 → [(transform, shape)]，路径后缀 → (transform, shape)。"""
    by_short = {}
    by_suffix = {}
    for node in meshes:
        if not cmds.objExists(node):
            continue
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True, noIntermediate=True)
        if not shapes:
            continue
        short = node.split("|")[-1]
        by_short.setdefault(short, []).append((node, shapes[0]))
        suffix = node.lstrip("|")
        by_suffix[suffix] = (node, shapes[0])
    return by_short, by_suffix


def _match_mesh(dag_path, by_short, by_suffix):
    """根据 _materials.json 中的 DAG 路径匹配场景 mesh。返回 (transform, shape) 或 None。"""
    clean = dag_path.replace("cache|", "").replace("\\", "|").lstrip("|")

    # 精确后缀匹配
    if clean in by_suffix:
        return by_suffix[clean]

    # 短名匹配
    short = clean.split("|")[-1]
    candidates = by_short.get(short, [])
    if len(candidates) == 1:
        return candidates[0]

    # 路径包含匹配
    for suffix, pair in by_suffix.items():
        if suffix.endswith(clean) or clean.endswith(suffix):
            return pair

    return None


def _assign_faces(sg, transform, face_indices):
    """将面列表赋予 SG，压缩连续面为范围格式。"""
    if not face_indices:
        cmds.sets(transform, forceElement=sg)
        return

    sorted_faces = sorted(face_indices)
    comps = []
    i = 0
    while i < len(sorted_faces):
        start = sorted_faces[i]
        end = start
        while i + 1 < len(sorted_faces) and sorted_faces[i + 1] == end + 1:
            i += 1
            end = sorted_faces[i]
        if start == end:
            comps.append(f"{transform}.f[{start}]")
        else:
            comps.append(f"{transform}.f[{start}:{end}]")
        i += 1
    cmds.sets(comps, forceElement=sg)


def _validate_assignments(materials_info, mesh_nodes):
    """Validate every contract face before old shading assignments are touched."""
    by_short, by_suffix = _build_mesh_lookup(mesh_nodes)
    covered = {}
    errors = []

    for mat_name, mat_data in materials_info.items():
        faces_by_mesh = mat_data.get("faces_by_mesh")
        if not isinstance(faces_by_mesh, dict) or not faces_by_mesh:
            errors.append(f"contract→{mat_name}: faces_by_mesh 为空或不是对象")
            continue
        for dag_path, face_indices in faces_by_mesh.items():
            match = _match_mesh(dag_path, by_short, by_suffix)
            if not match:
                errors.append(f"{dag_path}→{mat_name}: Maya 中找不到对应 mesh")
                continue
            transform, shape = match
            if not isinstance(face_indices, list) or not face_indices:
                errors.append(f"{transform}→{mat_name}: 面列表为空或不是数组")
                continue
            face_count = int(cmds.polyEvaluate(shape, face=True) or 0)
            seen = covered.setdefault(transform, {})
            for face in face_indices:
                if not isinstance(face, int) or face < 0 or face >= face_count:
                    errors.append(f"{transform}→{mat_name}: 非法面索引 {face!r}")
                elif face in seen:
                    errors.append(
                        f"{transform}→{mat_name}: face {face} 与 {seen[face]} 重复分配"
                    )
                else:
                    seen[face] = mat_name

    for transform, shape in by_suffix.values():
        face_count = int(cmds.polyEvaluate(shape, face=True) or 0)
        missing = sorted(set(range(face_count)) - set(covered.get(transform, {})))
        if missing:
            errors.append(f"{transform}→contract: 缺少面分配 {missing[:20]}")
    return errors


def _clear_shading_assignments(mesh_nodes):
    """赋新材质前，清除 mesh 旧的材质指派 + shading 侧 groupId。

    复用件（绑定体）身上常残留 rig 原来的材质球连接（整体 SG + per-face），
    apply 只加不减会新旧并存。这里在赋新材质前先清干净：
      1. forceElement 到 initialShadingGroup：收回整体+per-face 材质指派，
         Maya 自动清掉 shape 的 instObjGroups 材质分组项(即 group "part"/component list)。
      2. 删 shading 侧孤立 groupId：只删连 shadingEngine 的（材质 id），
         skinCluster/blendShape/tweak 的 groupId/groupParts 是变形器命根子，一个不碰。
    返回清掉的旧材质 SG 名集合，供末尾清空壳。
    """
    touched_sgs = set()
    for node in mesh_nodes:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True, noIntermediate=True) or []
        for shape in shapes:
            # 先记录旧材质 SG + shading 侧 groupId（连 shadingEngine 的）
            old_sgs = cmds.listConnections(shape, type="shadingEngine") or []
            touched_sgs.update(sg for sg in old_sgs if sg not in ("initialShadingGroup", "initialParticleSE"))
            shading_gids = []
            for gid in cmds.ls(cmds.listHistory(shape) or [], type="groupId"):
                sets = cmds.listConnections(gid, type="objectSet") or []
                if sets and all(cmds.nodeType(s) == "shadingEngine" for s in sets):
                    shading_gids.append(gid)
            # 收回 initialShadingGroup（清整体+per-face 材质指派 / instObjGroups）
            try:
                cmds.sets(shape, e=True, forceElement="initialShadingGroup")
            except Exception as e:
                logger.debug("重置 SG 失败 %s: %s", shape, e)
            # 删孤立 shading groupId（变形器 groupId 一律保留）
            for gid in shading_gids:
                if cmds.objExists(gid):
                    try:
                        cmds.delete(gid)
                    except Exception as e:
                        logger.debug("删 shading groupId 失败 %s: %s", gid, e)
    return touched_sgs


def _delete_empty_shading_groups(sg_names):
    """删目标 cache 已脱离且不被其他对象使用的旧材质网络。"""
    removed_sgs = []
    removed_nodes = []
    shared_sgs = []
    for sg in sg_names:
        if not cmds.objExists(sg) or sg in ("initialShadingGroup", "initialParticleSE"):
            continue
        if cmds.sets(sg, q=True) or []:
            shared_sgs.append(sg)
            continue  # 还有成员，跳过
        try:
            shaders = cmds.listConnections(sg + ".surfaceShader", s=True, d=False) or []
            upstream = []
            seen = set()
            pending = list(shaders)
            while pending:
                node = pending.pop(0)
                if node in seen or not cmds.objExists(node):
                    continue
                seen.add(node)
                for source in cmds.listConnections(node, s=True, d=False) or []:
                    if (cmds.nodeType(source) in _MATERIAL_UPSTREAM_TYPES
                            and source not in seen):
                        upstream.append(source)
                        pending.append(source)
            cmds.delete(sg)
            removed_sgs.append(sg)
            for sh in shaders:
                if cmds.objExists(sh) and not (cmds.listConnections(sh, type="shadingEngine") or []):
                    cmds.delete(sh)
                    removed_nodes.append(sh)
                    for node in upstream:
                        if (cmds.objExists(node)
                                and cmds.nodeType(node) in _MATERIAL_UPSTREAM_TYPES
                                and not cmds.listConnections(node, s=False, d=True)):
                            cmds.delete(node)
                            removed_nodes.append(node)
        except Exception as e:
            logger.debug("删空 SG 失败 %s: %s", sg, e)
    return {
        "removed_sgs": removed_sgs,
        "removed_nodes": removed_nodes,
        "shared_sgs": shared_sgs,
    }


def apply_materials(materials_info, mesh_nodes=None):
    """核心函数：根据材质信息为场景 mesh 创建材质球并按面赋予。

    Args:
        materials_info: {"material": {"color": {...}, "alpha": {...}, "faces_by_mesh": {...}}}
        mesh_nodes: 场景中的 mesh transform 列表。为 None 则自动收集全场景。

    Returns:
        tuple[list[str], list[str], dict]: 赋予记录、失败记录和旧材质清理结果。
    """
    if not materials_info:
        return [], [], {"removed_sgs": [], "removed_nodes": [], "shared_sgs": []}

    if mesh_nodes is None:
        mesh_nodes = cmds.ls(type="mesh", noIntermediate=True, long=True) or []
        mesh_nodes = [cmds.listRelatives(m, parent=True, fullPath=True)[0] for m in mesh_nodes]

    failed = _validate_assignments(materials_info, mesh_nodes)
    if failed:
        return [], failed, {"removed_sgs": [], "removed_nodes": [], "shared_sgs": []}

    # 先清旧材质指派并删除不再被其他对象使用的旧网络，保证新节点名不复用旧节点。
    old_sgs = _clear_shading_assignments(mesh_nodes)
    cleanup = _delete_empty_shading_groups(old_sgs)

    by_short, by_suffix = _build_mesh_lookup(mesh_nodes)
    assigned = []
    failed = []

    for mat_name, mat_data in materials_info.items():
        color_info = mat_data.get("color", {})
        alpha_info = mat_data.get("alpha", {"type": "value", "value": 1.0})
        faces_by_mesh = mat_data.get("faces_by_mesh", {})

        _, sg = _create_material_and_sg(mat_name, color_info, alpha_info)

        for dag_path, face_indices in faces_by_mesh.items():
            match = _match_mesh(dag_path, by_short, by_suffix)
            if not match:
                failed.append(f"{dag_path}→{mat_name}: Maya 中找不到对应 mesh")
                continue
            transform, shape = match
            try:
                _assign_faces(sg, transform, face_indices)
                mesh_short = transform.split("|")[-1]
                assigned.append(f"{mesh_short}→{mat_name}")
            except Exception as e:
                failed.append(f"{transform.split('|')[-1]}→{mat_name}: {e}")

    return assigned, failed, cleanup


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get("parameters", {})
    materials_path = params.get("materials_path", "")
    target_group = params.get("target_group", "")
    if target_group:
        from cgi_pipeline.hosts.maya.asset_info_collector import resolve_cache_group
        target_group = resolve_cache_group(target_group) or target_group

    if not materials_path:
        return make_receipt("maya_apply_materials", "ERROR", t0,
                           error="缺少必填参数: materials_path")

    if not os.path.isfile(materials_path):
        return make_receipt("maya_apply_materials", "ERROR", t0,
                           error=f"文件不存在: {materials_path}")

    # 读取材质数据
    with open(materials_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    try:
        materials_info = _flatten_material_contract(raw)
    except ValueError as exc:
        return make_receipt(
            "maya_apply_materials", "ERROR", t0,
            summary_input=os.path.basename(materials_path),
            error=str(exc),
        )

    # 收集目标 mesh
    if target_group and cmds.objExists(target_group):
        shapes = cmds.listRelatives(target_group, allDescendents=True,
                                    type="mesh", fullPath=True) or []
        shapes = [s for s in shapes if not cmds.getAttr(s + ".intermediateObject")]
        mesh_nodes = []
        for s in shapes:
            parent = cmds.listRelatives(s, parent=True, fullPath=True)
            if parent:
                mesh_nodes.append(parent[0])
    else:
        shapes = cmds.ls(type="mesh", noIntermediate=True, long=True) or []
        mesh_nodes = []
        for s in shapes:
            parent = cmds.listRelatives(s, parent=True, fullPath=True)
            if parent:
                mesh_nodes.append(parent[0])

    if not mesh_nodes:
        return make_receipt("maya_apply_materials", "ERROR", t0,
                           summary_input=os.path.basename(materials_path),
                           error=f"目标组下没有可赋材 mesh: {target_group or '<scene>'}")

    # 执行赋予
    cmds.undoInfo(openChunk=True, chunkName="maya_apply_materials")
    try:
        assigned, failed, cleanup = apply_materials(materials_info, mesh_nodes)
    finally:
        cmds.undoInfo(closeChunk=True)

    items = [make_item(name=a.split("→")[0], detail=a) for a in assigned]
    items.extend(make_item(name=f.split("→")[0], detail=f"失败: {f}") for f in failed)
    items.extend(make_item(name="旧材质清理", detail=f"移除 SG: {sg}")
                 for sg in cleanup["removed_sgs"])
    items.extend(make_item(name="旧材质保留", detail=f"仍被其他对象使用: {sg}")
                 for sg in cleanup["shared_sgs"])
    status = "SUCCESS" if not failed else "ERROR"
    assigned_meshes = sorted({a.split("→", 1)[0] for a in assigned})
    material_names = sorted({a.split("→", 1)[1] for a in assigned if "→" in a})
    approximation_items = []
    for material_name, material_data in materials_info.items():
        for channel in ("color", "alpha"):
            info = material_data.get(channel) or {}
            mode = info.get("approximation")
            if mode and mode != "direct":
                approximation_items.append({
                    "material": material_name,
                    "channel": channel,
                    "mode": mode,
                    "source_image_count": info.get("source_image_count", 0),
                })

    return make_receipt(
        api_id="maya_apply_materials",
        status=status,
        start_time=t0,
        summary_input=os.path.basename(materials_path),
        summary_action=f"材质赋予 — {len(set(m.split('→')[1] for m in assigned))} 个材质球",
        summary_count=len(assigned),
        summary_label="面赋予",
        items=items,
        output={
            "materials_path": materials_path,
            "target_group": target_group,
            "material_count": len(material_names),
            "material_names": material_names,
            "assigned_mesh_count": len(assigned_meshes),
            "assigned_meshes": assigned_meshes,
            "assignment_count": len(assigned),
            "failed_count": len(failed),
            "failed_assignments": failed,
            "removed_old_sg_count": len(cleanup["removed_sgs"]),
            "removed_old_node_count": len(cleanup["removed_nodes"]),
            "shared_old_sg_count": len(cleanup["shared_sgs"]),
            "removed_old_sgs": cleanup["removed_sgs"],
            "shared_old_sgs": cleanup["shared_sgs"],
            "approximation_count": len(approximation_items),
            "approximation_items": approximation_items,
        },
        error="; ".join(failed) if failed else "",
    )
