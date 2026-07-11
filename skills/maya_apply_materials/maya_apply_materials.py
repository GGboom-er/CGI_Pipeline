# skills/maya_apply_materials/maya_apply_materials.py
# ── 材质信息消费：读取 _materials.json 并在 Maya 中按面赋予材质球 ──
#
# 与 blender_extract_materials 配对。UDIM 贴图已在 Blender 侧按象限拆分为独立条目，
# 本技能直接按条目创建 lambert 材质球，无需 Maya 侧 UV 分析。

import os
import json
import time
import logging

import maya.cmds as cmds

from core.receipt import make_receipt, make_item
from core.material_semantics import should_use_as_color_texture

logger = logging.getLogger(__name__)


PLACE2D_ATTRS = [
    "coverage", "translateFrame", "rotateFrame", "mirrorU", "mirrorV",
    "stagger", "wrapU", "wrapV", "repeatUV", "offset", "rotateUV",
    "noiseUV", "vertexUvOne", "vertexUvTwo", "vertexUvThree", "vertexCameraOne",
]


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


def _create_material_and_sg(mat_name, color_info, alpha_info):
    """创建 lambert + SG + 贴图节点，返回 (shader, sg)。"""
    safe_name = mat_name.replace(" ", "_").replace(".", "_")
    sg_name = safe_name + "_SG"

    if cmds.objExists(sg_name):
        sg = sg_name
        conns = cmds.listConnections(sg + ".surfaceShader")
        shader = conns[0] if conns else None
        if not shader:
            shader = cmds.shadingNode("lambert", asShader=True, name=safe_name)
            cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    else:
        shader = cmds.shadingNode("lambert", asShader=True, name=safe_name)
        # 异常资产可能把 renderPartition 锁死，导致下面 cmds.sets 自动插入 SG 时崩；先解锁再建。
        _ensure_render_partition_unlocked()
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg_name)
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)

    # 颜色
    if color_info.get("type") == "solid":
        rgba = color_info.get("value", [0.8, 0.8, 0.8, 1.0])
        cmds.setAttr(shader + ".color", rgba[0], rgba[1], rgba[2], type="double3")
    elif color_info.get("type") == "texture":
        tex_path = color_info.get("path", "")
        if tex_path and should_use_as_color_texture(tex_path):
            file_node = cmds.shadingNode("file", asTexture=True, name=safe_name + "_file")
            p2d = cmds.shadingNode("place2dTexture", asUtility=True, name=safe_name + "_p2d")
            cmds.connectAttr(p2d + ".outUV", file_node + ".uvCoord")
            cmds.connectAttr(p2d + ".outUvFilterSize", file_node + ".uvFilterSize")
            for attr in PLACE2D_ATTRS:
                try:
                    cmds.connectAttr(f"{p2d}.{attr}", f"{file_node}.{attr}", force=True)
                except Exception as e:
                    logger.debug("跳过 place2dTexture 颜色属性连接 %s: %s", attr, e)
            cmds.setAttr(file_node + ".fileTextureName", tex_path, type="string")
            if color_info.get("is_udim"):
                cmds.setAttr(file_node + ".uvTilingMode", 3)
            cmds.connectAttr(file_node + ".outColor", shader + ".color", force=True)
        else:
            rgba = color_info.get("value", [0.8, 0.8, 0.8, 1.0])
            cmds.setAttr(shader + ".color", rgba[0], rgba[1], rgba[2], type="double3")

    # 透明度
    semantic = alpha_info.get("semantic", "alpha")
    if alpha_info.get("type") == "value":
        a = alpha_info.get("value", 1.0)
        if semantic == "alpha":
            t = 1.0 - a if a < 1.0 else 0.0
        else:
            t = a
        if t > 0.0:
            cmds.setAttr(shader + ".transparency", t, t, t, type="double3")
    elif alpha_info.get("type") == "texture":
        alpha_path = alpha_info.get("path", "")
        if alpha_path:
            alpha_file = cmds.shadingNode("file", asTexture=True, name=safe_name + "_alpha_file")
            alpha_p2d = cmds.shadingNode("place2dTexture", asUtility=True, name=safe_name + "_alpha_p2d")
            cmds.connectAttr(alpha_p2d + ".outUV", alpha_file + ".uvCoord")
            cmds.connectAttr(alpha_p2d + ".outUvFilterSize", alpha_file + ".uvFilterSize")
            for attr in PLACE2D_ATTRS:
                try:
                    cmds.connectAttr(f"{alpha_p2d}.{attr}", f"{alpha_file}.{attr}", force=True)
                except Exception as e:
                    logger.debug("跳过 place2dTexture 透明度属性连接 %s: %s", attr, e)
            cmds.setAttr(alpha_file + ".fileTextureName", alpha_path, type="string")
            if semantic == "alpha":
                rev = cmds.shadingNode("reverse", asUtility=True, name=safe_name + "_alphaRev")
                cmds.connectAttr(alpha_file + ".outAlpha", rev + ".inputX")
                cmds.connectAttr(rev + ".outputX", shader + ".transparencyR", force=True)
                cmds.connectAttr(rev + ".outputX", shader + ".transparencyG", force=True)
                cmds.connectAttr(rev + ".outputX", shader + ".transparencyB", force=True)
            else:
                cmds.connectAttr(alpha_file + ".outAlpha", shader + ".transparencyR", force=True)
                cmds.connectAttr(alpha_file + ".outAlpha", shader + ".transparencyG", force=True)
                cmds.connectAttr(alpha_file + ".outAlpha", shader + ".transparencyB", force=True)

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
    """删已无成员的旧材质 SG 壳(及其孤立 shader/file)，不碰默认 SG。"""
    for sg in sg_names:
        if not cmds.objExists(sg) or sg in ("initialShadingGroup", "initialParticleSE"):
            continue
        if cmds.sets(sg, q=True) or []:
            continue  # 还有成员，跳过
        try:
            shaders = cmds.listConnections(sg + ".surfaceShader", s=True, d=False) or []
            cmds.delete(sg)
            for sh in shaders:
                if cmds.objExists(sh) and not (cmds.listConnections(sh, type="shadingEngine") or []):
                    cmds.delete(sh)
        except Exception as e:
            logger.debug("删空 SG 失败 %s: %s", sg, e)


def apply_materials(materials_info, mesh_nodes=None):
    """核心函数：根据材质信息为场景 mesh 创建材质球并按面赋予。

    Args:
        materials_info: {"mat_name": {"color": {...}, "alpha": {...}, "faces_by_mesh": {...}}}
        mesh_nodes: 场景中的 mesh transform 列表。为 None 则自动收集全场景。

    Returns:
        tuple[list[str], list[str]]: 赋予记录和失败记录。
    """
    if not materials_info:
        return [], []

    if mesh_nodes is None:
        mesh_nodes = cmds.ls(type="mesh", noIntermediate=True, long=True) or []
        mesh_nodes = [cmds.listRelatives(m, parent=True, fullPath=True)[0] for m in mesh_nodes]

    # 赋新材质前先清旧材质指派 + shading 侧 groupId（变形器节点不碰）
    old_sgs = _clear_shading_assignments(mesh_nodes)

    by_short, by_suffix = _build_mesh_lookup(mesh_nodes)
    assigned = []
    failed = []

    for mat_name, mat_data in materials_info.items():
        color_info = mat_data.get("color", {})
        alpha_info = mat_data.get("alpha", {})
        faces_by_mesh = mat_data.get("faces_by_mesh", {})

        _, sg = _create_material_and_sg(mat_name, color_info, alpha_info)

        for dag_path, face_indices in faces_by_mesh.items():
            match = _match_mesh(dag_path, by_short, by_suffix)
            if not match:
                continue
            transform, shape = match
            try:
                _assign_faces(sg, transform, face_indices)
                mesh_short = transform.split("|")[-1]
                assigned.append(f"{mesh_short}→{mat_name}")
            except Exception as e:
                failed.append(f"{transform.split('|')[-1]}→{mat_name}: {e}")

    # 收尾：删已空的旧材质 SG 壳（新材质已赋，旧 SG 无成员即可清）
    _delete_empty_shading_groups(old_sgs)

    return assigned, failed


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get("parameters", {})
    materials_path = params.get("materials_path", "")
    target_group = params.get("target_group", "")
    if target_group:
        from dccs.maya.asset_info_collector import resolve_cache_group
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
    materials_info = raw.get("materials", raw)

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
        return make_receipt("maya_apply_materials", "SUCCESS", t0,
                           summary_input=os.path.basename(materials_path),
                           summary_action="无 mesh 可赋材",
                           summary_count=0, summary_label="材质球",
                           output={
                               "materials_path": materials_path,
                               "target_group": target_group,
                               "material_count": len(materials_info),
                               "material_names": sorted(materials_info.keys()),
                               "assigned_mesh_count": 0,
                               "assignment_count": 0,
                               "failed_count": 0,
                           })

    # 执行赋予
    cmds.undoInfo(openChunk=True, chunkName="maya_apply_materials")
    try:
        assigned, failed = apply_materials(materials_info, mesh_nodes)
    finally:
        cmds.undoInfo(closeChunk=True)

    items = [make_item(name=a.split("→")[0], detail=a) for a in assigned]
    items.extend(make_item(name=f.split("→")[0], detail=f"失败: {f}") for f in failed)
    status = "SUCCESS" if not failed else "PARTIAL"
    assigned_meshes = sorted({a.split("→", 1)[0] for a in assigned})
    material_names = sorted({a.split("→", 1)[1] for a in assigned if "→" in a})

    return make_receipt(
        skill_id="maya_apply_materials",
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
        },
        error="; ".join(failed) if failed else "",
    )
