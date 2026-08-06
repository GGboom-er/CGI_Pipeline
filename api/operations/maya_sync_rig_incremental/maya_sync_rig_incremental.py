# api/operations/maya_sync_rig_incremental/maya_sync_rig_incremental.py
# ── Rig-Asset 增量同步（纯几何）──
#
# 核心流程：
#   Phase 1: cache 组批量加 RIG_ 前缀
#   Phase 2: 采集旧 RIG 几何（对比用）
#   Phase 3: 按 compare_result 分发 —— IDENTICAL/ORIG_INJECT 搬运复用（保留原绑定）
#   Phase 4: PAIRED/UNPAIRED 按 ABC 重建几何（不传权重/BS，未绑定交绑定师手绑）
#   Phase 5: 显示层分配与清理
#
# 只做几何：权重/BlendShape 传递与重建迁移代码已移除；复用件靠 MObject 连接保留原绑定。

import os
import json
import time
import sys
import logging
import re

import numpy as np
import maya.cmds as cmds
from maya.api import OpenMaya as om2

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT
from core.receipt import make_receipt, make_item
from api.operations.maya_sync_rig_incremental.sync_contract import (
    API_ID,
    SyncContractError,
    build_sync_output_details,
    format_action_summary,
    load_compare_result_file,
    load_compare_result_value,
    parse_sync_inputs,
    summarize_sync_actions,
    validate_input_files,
    validate_sync_inputs,
)

logger = logging.getLogger(__name__)

def _plog(msg):
    """行缓冲进度日志（定位卡点用）。"""
    sys.stderr.write(f"[sync_progress {time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()

# ═══════════════════════════════════════════
# 配置常量
# ═══════════════════════════════════════════
RIG_PREFIX = "RIG_"
THRESH_IDENTICAL = 0.0001   # cm，完全一致阈值
MIN_DOT = 0.0               # 法线夹角判定阈值 (0.0 = 90度)
MAX_K_SEARCH = 50           # 法线失败时向下检索备选面的数量

# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════

def _load_compare_result(compare_result_value):
    return load_compare_result_value(compare_result_value)


def _safe_maya_node_name(name, fallback="sync_layer"):
    """把外部 compare_result 的 layer_name 收口成 Maya 可创建节点名。"""
    cleaned = re.sub(r"[^0-9A-Za-z_]", "_", str(name or ""))
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned.strip("_"):
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"L_{cleaned}"
    return cleaned


def _sync_receipt(status, start_time, phase, error="", items=None,
                  summary_action="", recovery_hint="", report_content="",
                  output=None, summary_count=0, summary_label="资产同步"):
    if error and phase:
        error = f"[{phase}] {error}"
    return make_receipt(
        API_ID, status, start_time,
        summary_action=summary_action or (f"{phase} 失败" if status != "SUCCESS" else phase),
        summary_count=summary_count,
        summary_label=summary_label,
        items=items or [],
        output=output or {},
        error=error,
        recovery_hint=recovery_hint,
        report_content=report_content,
    )


def _merge_full_source_info(light_info, full_info):
    merged = dict(full_info or {})
    merged["source_file"] = (full_info or {}).get("source_file") or (light_info or {}).get("source_file", "")
    merged["textures"] = (full_info or {}).get("textures") or (light_info or {}).get("textures", {})
    merged["materials"] = (full_info or {}).get("materials") or (light_info or {}).get("materials", {})

    full_meshes = (full_info or {}).get("meshes", {})
    merged_meshes = {}
    for dag, light_entry in (light_info or {}).get("meshes", {}).items():
        entry = dict(light_entry or {})
        if dag in full_meshes:
            entry.update(full_meshes[dag] or {})
        merged_meshes[dag] = entry
    for dag, full_entry in full_meshes.items():
        if dag not in merged_meshes:
            merged_meshes[dag] = full_entry
    merged["meshes"] = merged_meshes
    return merged

def _project_point_to_triangle(point, tri):
    import numpy as np
    a, b_t, c = tri[0], tri[1], tri[2]
    ap = point - a
    ab, ac = b_t - a, c - a
    d00 = np.dot(ab, ab)
    d01 = np.dot(ab, ac)
    d11 = np.dot(ac, ac)
    d20 = np.dot(ap, ab)
    d21 = np.dot(ap, ac)
    denom = d00 * d11 - d01 * d01
    if abs(denom) > 1e-12:
        bv = (d11 * d20 - d01 * d21) / denom
        bw = (d00 * d21 - d01 * d20) / denom
        bu = 1.0 - bv - bw
        bu = max(0.0, min(1.0, bu))
        bv = max(0.0, min(1.0, bv))
        bw = max(0.0, min(1.0, bw))
        s = bu + bv + bw
        if s > 0:
            bu /= s; bv /= s; bw /= s
        return bu * a + bv * b_t + bw * c
    return tri.mean(axis=0)

def _classify_layer_depth(points, normals, mesh_ray, face_source_ids, num_sources,
                          point_source_ids=None):
    """
    用固定方向射线 + 奇偶规则判断每个点被多少个"其他"源 mesh 包裹。
    point_source_ids: 每个点属于哪个源 mesh（用于排除自身贡献），None 则不排除。
    """
    import numpy as np
    n = len(points)
    if n == 0:
        return np.zeros(0, dtype=np.int32)

    direction = np.array([0.4395064455, 0.617598629942, 0.652231566745])
    directions = np.tile(direction, (n, 1))

    index_tri, index_ray = mesh_ray.intersects_id(
        ray_origins=points,
        ray_directions=directions,
        multiple_hits=True,
    )
    if len(index_ray) == 0:
        return np.zeros(n, dtype=np.int32)

    layer_depth = np.zeros(n, dtype=np.int32)
    hit_sources = face_source_ids[index_tri]

    for src_id in range(num_sources):
        src_mask = hit_sources == src_id
        if not np.any(src_mask):
            continue
        src_ray_ids = index_ray[src_mask]
        counts = np.bincount(src_ray_ids, minlength=n)
        inside = (counts % 2).astype(np.int32)
        if point_source_ids is not None:
            inside[point_source_ids == src_id] = 0
        layer_depth += inside

    return layer_depth




def _add_prefix_recursive(node, prefix=RIG_PREFIX):
    children = cmds.listRelatives(node, children=True, fullPath=True) or []
    for child in children:
        _add_prefix_recursive(child, prefix)
    short = node.split("|")[-1]
    if not short.startswith(prefix):
        cmds.rename(node, prefix + short)



def _ensure_collectable_shape_orig(transform):
    """确保新建 mesh 有标准且可被 asset_info_collector 采集的 ShapeOrig。"""
    from dccs.maya.asset_info_collector import get_shape_orig

    if not transform or not cmds.objExists(transform):
        return False, "transform 不存在"
    transform = (cmds.ls(transform, long=True, type="transform") or [transform])[0]
    transform_name = transform.split("|")[-1]
    expected_shape = transform_name + "Shape"
    expected_orig = transform_name + "ShapeOrig"

    shapes = cmds.listRelatives(transform, shapes=True, type="mesh", fullPath=True) or []
    visible_shapes = [s for s in shapes if not cmds.getAttr(s + ".intermediateObject")]
    if len(visible_shapes) != 1:
        return False, f"可见 Shape 数量不是 1: {len(visible_shapes)}"

    visible = visible_shapes[0]
    if visible.split("|")[-1] != expected_shape:
        try:
            visible = cmds.rename(visible, expected_shape)
            visible = (cmds.ls(visible, long=True) or [visible])[0]
        except Exception as e:
            return False, f"重命名 Shape 失败: {e}"

    if get_shape_orig(visible, transform):
        return True, "exists"

    incoming = cmds.listConnections(
        visible + ".inMesh",
        source=True,
        destination=False,
        plugs=True,
    ) or []
    if incoming:
        return False, f"可见 Shape 已有 inMesh 输入但缺少标准 Orig: {incoming[:3]}"

    shapes = cmds.listRelatives(transform, shapes=True, type="mesh", fullPath=True) or []
    orig_candidates = [
        s for s in shapes
        if s.split("|")[-1] == expected_orig and cmds.getAttr(s + ".intermediateObject")
    ]
    if len(orig_candidates) > 1:
        return False, f"标准 Orig 候选超过 1 个: {len(orig_candidates)}"
    orig_shape = orig_candidates[0] if orig_candidates else None

    if not orig_shape:
        try:
            orig_shape = cmds.createNode("mesh", name=expected_orig, parent=transform)
            orig_shape = (cmds.ls(orig_shape, long=True) or [orig_shape])[0]
        except Exception as e:
            return False, f"创建 ShapeOrig 失败: {e}"

    try:
        existing_out = cmds.listConnections(
            orig_shape + ".outMesh",
            source=False,
            destination=True,
            plugs=True,
        ) or []
        if existing_out:
            return False, f"ShapeOrig 已有输出连接但未通过采集规则: {existing_out[:3]}"

        cmds.connectAttr(visible + ".outMesh", orig_shape + ".inMesh", force=True)
        cmds.getAttr(orig_shape + ".boundingBoxMin")
        cmds.disconnectAttr(visible + ".outMesh", orig_shape + ".inMesh")
        cmds.setAttr(orig_shape + ".intermediateObject", 1)
        cmds.connectAttr(orig_shape + ".outMesh", visible + ".inMesh", force=True)
        cmds.getAttr(visible + ".boundingBoxMin")
    except Exception as e:
        return False, f"初始化 ShapeOrig 失败: {e}"

    return (True, "created") if get_shape_orig(visible, transform) else (False, "创建后仍不可采集")

def _find_skin_cluster(dag_shape_or_transform):
    node_type = cmds.nodeType(dag_shape_or_transform)
    if node_type == "mesh":
        transforms = cmds.listRelatives(dag_shape_or_transform, parent=True, fullPath=True)
        transform = transforms[0] if transforms else None
    else:
        transform = dag_shape_or_transform

    if not transform or not cmds.objExists(transform):
        return None, None

    all_shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
    for shape in all_shapes:
        try:
            history = cmds.listHistory(shape) or []
            skin_nodes = cmds.ls(history, type="skinCluster")
            if skin_nodes:
                return skin_nodes[0], shape
        except Exception:
            pass
        try:
            connections = cmds.listConnections(shape + ".inMesh", type="skinCluster") or []
            if connections:
                return connections[0], shape
        except Exception:
            pass
    return None, None

def _collect_rig_meshes(cache_group=None):
    """采集已加 RIG_ 前缀的 rig mesh 信息，规则与 maya_build_asset_info 共用。"""
    from dccs.maya.asset_info_collector import collect_scene_info

    rig_cache_group = cache_group or f"{RIG_PREFIX}cache"
    return collect_scene_info(
        rig_cache_group,
        include_topology=True,
        precision=4,
    ).get("meshes", {})

def _get_target_name_and_parent(dag_tex):
    clean = dag_tex.replace("ABC|", "").lstrip("|")
    parts = clean.split("|")
    if parts[-1].endswith("Shape"):
        transform_name = parts[-2] if len(parts) >= 2 else parts[-1].replace("Shape", "")
        group_parts = parts[:-2]
    else:
        transform_name = parts[-1]
        group_parts = parts[:-1]
    if group_parts and group_parts[0] == "Group":
        group_parts = group_parts[1:]
    return transform_name, group_parts

def _ensure_hierarchy(group_parts):
    current = ""
    for i, grp in enumerate(group_parts):
        if grp == "Group":
            if cmds.objExists("|Group"):
                current = (cmds.ls("|Group", long=True, type="transform") or ["|Group"])[0]
            else:
                current = cmds.group(em=True, name="Group")
            continue
        elif grp == "Geometry":
            parent = current or (cmds.ls("|Group", long=True, type="transform") or [""])[0]
            target = f"{parent}|Geometry" if parent else "|Geometry"
            if not cmds.objExists(target):
                if parent:
                    cmds.group(em=True, name="Geometry", parent=parent)
                else:
                    cmds.group(em=True, name="Geometry")
            current = target
            continue
        elif grp == "cache":
            geo = cmds.ls("|Group|Geometry", long=True, type="transform") or []
            if geo:
                geo_path = geo[0]
                cache_path = f"{geo_path}|cache"
                if not cmds.objExists(cache_path):
                    cmds.group(em=True, name="cache", parent=geo_path)
                current = cache_path
                continue
            if current:
                cache_path = f"{current}|cache"
                if not cmds.objExists(cache_path):
                    cmds.group(em=True, name="cache", parent=current)
                current = cache_path
                continue
        parent = current if current else None
        target = f"{current}|{grp}" if current else grp
        if not cmds.objExists(target):
            if parent:
                cmds.group(em=True, name=grp, parent=parent)
            else:
                cmds.group(em=True, name=grp)
        current = target
    return current

from api.operations.maya_build_mesh_from_abc.maya_build_mesh_from_abc import (
    create_mesh as _create_mesh_from_abc,
)

def _assign_to_layer(layer_name, nodes, reference_nodes=None, group_id=None,
                     action=None, reason=None, color=None):
    """建或复用 layer，写入 nodes。可选给 reference_nodes 单独标 displayType=Reference。

    给 layer 挂 extra attribute：group_id / action / reason，便于 Attribute Editor 一览。
    颜色按 group_id hash 分配，同一组多次调用颜色一致。
    """
    valid = [n for n in nodes if cmds.objExists(n)]
    ref_valid = [n for n in (reference_nodes or []) if cmds.objExists(n)]
    if not valid and not ref_valid:
        return
    layer_name = _safe_maya_node_name(layer_name, fallback=group_id or "sync_layer")
    if not cmds.objExists(layer_name):
        cmds.createDisplayLayer(name=layer_name, empty=True)
        for attr, val in (("group_id", group_id), ("sync_action", action), ("sync_reason", reason)):
            if val is None:
                continue
            try:
                cmds.addAttr(layer_name, longName=attr, dataType="string")
                cmds.setAttr(f"{layer_name}.{attr}", val, type="string")
            except Exception:
                pass
        if color is None and group_id:
            import hashlib
            h = int(hashlib.md5(group_id.encode("utf-8")).hexdigest(), 16)
            color = (h % 31) + 1  # 1..31，Maya 合法色板范围
        if color is not None:
            try:
                cmds.setAttr(f"{layer_name}.color", int(color))
            except Exception:
                pass
    if valid:
        cmds.editDisplayLayerMembers(layer_name, *valid, noRecurse=True)
    if ref_valid:
        cmds.editDisplayLayerMembers(layer_name, *ref_valid, noRecurse=True)
        # ref 节点走 transform 级 override，不污染 layer 整体 displayType
        for node in ref_valid:
            try:
                cmds.setAttr(f"{node}.overrideEnabled", 1)
                cmds.setAttr(f"{node}.overrideDisplayType", 2)  # 2=Reference，可见不可选
            except Exception:
                pass


def _check_sync_already_done(cache_group="cache"):
    """检测 rig 场景是否已被 sync 处理过。

    判据（二选一命中即算已处理）：
      - cache 组下有 _sync_done 属性标记
      - cache 组下存在"非 RIG_ 前缀且非 abc 来源"的 mesh transform（即不像原始 rig）

    返回 (is_done: bool, reason: str)。
    """
    try:
        from dccs.maya.asset_info_collector import resolve_cache_group
        resolved = resolve_cache_group(cache_group)
        candidates = [resolved] if resolved else []
    except Exception:
        candidates = []
    candidates.extend(cmds.ls("cache", long=True, type="transform") or [])

    seen = set()
    for cache_tr in candidates:
        if not cache_tr or cache_tr in seen:
            continue
        seen.add(cache_tr)
        if cmds.attributeQuery("_sync_done", node=cache_tr, exists=True):
            try:
                if cmds.getAttr(f"{cache_tr}._sync_done"):
                    return True, f"场景标记已 sync 过（{cache_tr}._sync_done=True）"
            except Exception:
                pass
    return False, ""


def _mark_sync_done(cache_node):
    """sync 完成后给 cache 组打 _sync_done 标记，防止重跑。"""
    if not cache_node or not cmds.objExists(cache_node):
        return
    try:
        if not cmds.attributeQuery("_sync_done", node=cache_node, exists=True):
            cmds.addAttr(cache_node, longName="_sync_done", attributeType="bool")
        cmds.setAttr(f"{cache_node}._sync_done", True)
    except Exception:
        pass


def _scan_hardcoded_refs(rig_prefix):
    """扫描场景里可能包含硬编码 `RIG_xxx` 路径的字符串字段。

    搬运 IDENTICAL/ORIG_INJECT 的 RIG_mesh 时节点路径/名会变；如果 rig 文件里有
    expression、scriptNode、notes 属性硬编码 `RIG_hair`，搬后会断。

    不做自动改写，只收集清单上报，交给绑定师人工复核。
    返回 [(node, attr_or_kind, preview)]
    """
    refs = []
    for node in cmds.ls(type="expression") or []:
        try:
            code = cmds.getAttr(f"{node}.expression")
        except Exception:
            continue
        if code and rig_prefix in code:
            refs.append((node, "expression.expression", code[:80]))

    for node in cmds.ls(type="script") or []:
        for attr in ("before", "after"):
            try:
                code = cmds.getAttr(f"{node}.{attr}")
            except Exception:
                continue
            if code and rig_prefix in code:
                refs.append((node, f"script.{attr}", code[:80]))

    # notes / 自定义 string 属性——代价较大，做一次覆盖全场景的粗扫
    try:
        all_nodes = cmds.ls(dag=True) or []
    except Exception:
        all_nodes = []
    for node in all_nodes:
        for attr in cmds.listAttr(node, userDefined=True) or []:
            full = f"{node}.{attr}"
            try:
                if cmds.getAttr(full, type=True) != "string":
                    continue
                val = cmds.getAttr(full)
            except Exception:
                continue
            if val and isinstance(val, str) and rig_prefix in val:
                refs.append((node, attr, val[:80]))
    return refs


def _relocate_rig_mesh(rig_dag, new_name, target_parent, rig_prefix, inject_points=None):
    """IDENTICAL / ORIG_INJECT 共用的"搬运"路径。

    1. reparent rig_dag 的 transform 到 target_parent（按 abc 结构重建的中间组）
    2. rename transform: RIG_xxx → new_name（去前缀）
    3. rename shape:     RIG_xxxShape → new_nameShape
    4. 若 inject_points 不为 None（ORIG_INJECT）：datablock 整块回灌 ShapeOrig 的 base
       （写进底层 .vrts，非 .pnts；skin/BS 会按新 ShapeOrig 自动重算变形）

    所有 Maya 原生连接（skin / BS / constraint / rivet / follicle 等）走 MObject，
    reparent + rename 不断。字符串硬编码引用由 _scan_hardcoded_refs 提前上报。

    返回新路径；失败返回 None。
    """
    import numpy as np
    transform = cmds.listRelatives(rig_dag, parent=True, fullPath=True)
    if not transform:
        return None
    transform = transform[0]
    if not cmds.objExists(transform):
        return None

    # reparent
    try:
        if target_parent and cmds.objExists(target_parent):
            parent_now = cmds.listRelatives(transform, parent=True, fullPath=True)
            if not parent_now or parent_now[0] != target_parent:
                transform = cmds.parent(transform, target_parent)[0]
                transform = cmds.ls(transform, long=True)[0]
    except Exception:
        return None

    # rename transform
    short_now = transform.split("|")[-1]
    if short_now != new_name:
        try:
            transform = cmds.rename(transform, new_name)
            transform = cmds.ls(transform, long=True)[0]
        except Exception:
            pass

    # rename shape（剥 RIG_ 前缀）
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
    for sh in shapes:
        short = sh.split("|")[-1]
        if short.startswith(rig_prefix):
            new_short = short[len(rig_prefix):]
            try:
                cmds.rename(sh, new_short)
            except Exception:
                pass

    # 注：几何注入已移出本函数（旧 inject_points/datablock 路径废弃）。
    # 本函数只做「搬运 + 改名」；ABC 全量注入(点+UV+法线)由 dispatch 调
    # _clean_orig_upstream → _regularize_uvset_to_map1 → _inject_mesh_data_via_plug 完成。
    # inject_points 形参保留仅为签名兼容，已不使用。
    return transform


def _clear_pnts_tweak(shape):
    """清一个 mesh shape 的 .pnts 顶点位移(tweak)残留，一次性归零。

    改的是节点属性值、非 poly 编辑，不生 polyModifier 历史。
    一次性范围 setAttr(.pnts[0:n-1]) 比逐索引快约 9x（实测 3542 点 0.1s vs 0.9s），
    大件(万级点)差距更大；点数用 numVertices 定，覆盖全部顶点（不依赖已分配的稀疏索引）。
    """
    try:
        sel = om2.MSelectionList()
        sel.add(shape)
        n = om2.MFnMesh(sel.getDagPath(0)).numVertices
    except Exception:
        n = 0
    if n <= 0:
        return
    try:
        cmds.setAttr("{}.pnts[0:{}]".format(shape, n - 1),
                     *([0.0, 0.0, 0.0] * n), type="double3")
        return
    except Exception:
        pass
    # 退化：一次性失败时逐索引兜底
    try:
        for i in (cmds.getAttr(shape + ".pnts", multiIndices=True) or []):
            cmds.setAttr("{}.pnts[{}]".format(shape, i), 0.0, 0.0, 0.0, type="double3")
    except Exception:
        pass


# ── 旧注入函数已废弃删除（2026-07-10 重构）──
# _inject_points_via_datablock / _inject_uv_via_sandbox / _inject_abc_uv 三者：
#   点注入 create parent 传 mesh shape 直接炸(静默失败)、且 connect/disconnect 不烘 base；
#   UV 沙盒两趟。统一由 _inject_mesh_data_via_plug(点+UV+法线一次写 cachedInMesh) 替代。


def _clean_orig_upstream(orig_shape):
    """删除 orig 的上游构造历史(polySoftEdge / 上游 mesh 等)，使 orig 成无输入干净头节点。

    ⚠ 禁用 cmds.delete(ch=True)：实测它会连下游 skinCluster/blendShape 一起删（破坏绑定）。
    变形器消费 orig.outMesh(下游)，不在 orig 的上游 history 内；listHistory(orig) 只回上游，
    故删上游构造节点安全、不碰变形器。删完 orig.inMesh 应为空，cachedInMesh 才写得住。
    返回被删节点短名列表。
    """
    incoming = cmds.listConnections(orig_shape + ".inMesh", s=True, d=False) or []
    if not incoming:
        return []  # 已无上游，干净头节点(如 hair orig)
    orig_long = (cmds.ls(orig_shape, long=True) or [orig_shape])[0]
    deformer_types = {
        "skinCluster", "blendShape", "cluster", "ffd", "wrap", "deltaMush",
        "tweak", "softMod", "nonLinear", "sculpt", "wire", "groupParts", "groupId",
    }
    to_del = []
    for n in (cmds.listHistory(orig_shape) or []):
        nl = (cmds.ls(n, long=True) or [None])[0]
        if not nl or nl == orig_long:
            continue
        nt = cmds.nodeType(n)
        if nt in deformer_types:
            continue  # 保变形器
        if nt.startswith("poly") or nt == "mesh":
            to_del.append(n)
    deleted = []
    for n in to_del:
        if cmds.objExists(n):
            try:
                cmds.delete(n)
                deleted.append(n.split("|")[-1])
            except Exception as e:
                cmds.warning("清上游节点失败 %s: %s" % (n, e))
    return deleted


def _regularize_uvset_to_map1(shape):
    """把 shape 的 uvSet 规整成有且仅有一套、名为 map1（真 shape 上操作，须在注入写入之前）。

    data 块 MObject 上 renameUVSet 报 Object does not exist、不可用，故 uvSet 名字类操作走真 shape。
    删多余 set + 把留下的改名 map1；改名/删是节点属性操作，不生 polyModifier 历史。
    """
    sets = cmds.polyUVSet(shape, q=True, allUVSets=True) or []
    if not sets:
        return
    keep = "map1" if "map1" in sets else sets[0]
    for us in sets:
        if us != keep:
            try:
                cmds.polyUVSet(shape, delete=True, uvSet=us)
            except Exception as e:
                cmds.warning("删 uvSet %s 失败: %s" % (us, e))
    if keep != "map1":
        try:
            cmds.polyUVSet(shape, rename=True, uvSet=keep, newUVSet="map1")
        except Exception as e:
            cmds.warning("uvSet 改名 map1 失败: %s" % e)


def _inject_mesh_data_via_plug(orig_shape, abc_entry):
    """把 ABC 完整几何(点+拓扑+UV+法线)一次写进 orig 的 base(cachedInMesh plug)。

    无临时 DAG mesh：内存 MFnMeshData 承载 ABC 几何 → 直接 setMObject 到 cachedInMesh。
    - 点：abc_entry['vert_positions'](abc_reader 已乘 world_matrix，含 m→cm 缩放，世界坐标)
    - UV：abc_entry u/v/uv_indices（单套 map1）
    - 法线：abc_entry['normals_fv'](facevarying，保硬边)；无则不设 → Maya 按拓扑重算
    写 cachedInMesh 而非 inMesh：无历史 mesh 的 outMesh 从 cachedInMesh 读；写 inMesh 无连接不触发重算。
    调用前提：orig 已无上游 inMesh 连接（见 _clean_orig_upstream），否则上游会覆盖本次写入。
    返回 True=已写。
    """
    vp = abc_entry.get("vert_positions", [])
    fc = abc_entry.get("face_counts", [])
    fi = list(abc_entry.get("face_indices", []))
    if not vp or not fc or not fi:
        return False
    pts = om2.MPointArray()
    for i in range(0, len(vp), 3):
        pts.append(om2.MPoint(vp[i], vp[i + 1], vp[i + 2]))
    counts = om2.MIntArray(fc)
    u = abc_entry.get("u_array", []); v = abc_entry.get("v_array", [])
    uvi = list(abc_entry.get("uv_indices", []))
    nfv = list(abc_entry.get("normals_fv", []))

    def _build_data_object():
        data_object = om2.MFnMeshData().create()
        om2.MFnMesh().create(
            pts, counts, om2.MIntArray(fi), parent=data_object
        )
        data_fn = om2.MFnMesh(data_object)
        if u and v and uvi:
            setname = (data_fn.getUVSetNames() or ["map1"])[0]
            data_fn.setUVs(om2.MFloatArray(u), om2.MFloatArray(v), setname)
            data_fn.assignUVs(counts, om2.MIntArray(uvi), setname)
        if nfv and len(nfv) == len(fi) * 3:
            normals = om2.MVectorArray()
            for i in range(0, len(nfv), 3):
                normals.append(om2.MVector(nfv[i], nfv[i + 1], nfv[i + 2]))
            face_ids = om2.MIntArray(); vtx_ids = om2.MIntArray()
            idx = 0
            for face_id, face_count in enumerate(fc):
                for _unused in range(face_count):
                    face_ids.append(face_id)
                    vtx_ids.append(fi[idx])
                    idx += 1
            try:
                data_fn.setFaceVertexNormals(normals, face_ids, vtx_ids)
            except Exception as exc:
                cmds.warning("setFaceVertexNormals 失败，回退 Maya 重算: %s" % exc)
        return data_object

    data = _build_data_object()
    sl = om2.MSelectionList(); sl.add(orig_shape)
    dep = om2.MFnDependencyNode(sl.getDependNode(0))
    dep.findPlug("cachedInMesh", False).setMObject(data)
    cmds.getAttr(orig_shape + ".boundingBoxMin")

    # ── UV 必须直写 orig 的持久属性，不能只靠 cachedInMesh ──
    # Maya 已知坑（实测 + Autodesk 论坛 "Modify all UVs using Python API 2"）：
    # setMObject(cachedInMesh) 能把点/拓扑/法线烘进节点持久几何属性（存盘 OK），
    # 但 UV 分配不落进持久 uv 属性(uvpt)——只活在 cache 数据块里，save→reopen 后
    # Maya 用节点旧 uv 属性重建 → UV 散乱（maYouD 一开就乱、修好保存重开又乱的根因）。
    # 修法：点/法线走上面 cachedInMesh；UV 在 cachedInMesh 写完、拓扑就位后，
    # 用 setUVs+assignUVs 直写 orig DAG shape 的持久属性。前提同上：orig 无上游 inMesh。
    # 两条都是 OpenMaya 底层直写、均不建 history。
    if u and v and uvi:
        try:
            dag = sl.getDagPath(0); dag.extendToShape()
            ofn = om2.MFnMesh(dag)
            oset = (ofn.getUVSetNames() or ["map1"])[0]
            ofn.setUVs(om2.MFloatArray(u), om2.MFloatArray(v), oset)
            ofn.assignUVs(counts, om2.MIntArray(uvi), oset)
        except Exception as e:
            cmds.warning("UV 直写 orig 持久属性失败: %s" % e)
    return True


# ═══════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════

def _infer_project_from_path(path: str) -> str:
    """从文件路径推断项目名。失败时默认 'ysj'。"""
    import re
    if not path:
        return "ysj"
    norm = path.replace("\\", "/").lower()
    # X:/Project/ysj/... 或 .../assets/ysj/... 模式
    m = re.search(r'[/\\]project[/\\]([a-z0-9_]+)[/\\]', norm)
    if m:
        return m.group(1)
    m = re.search(r'/([a-z]+)_[a-z]+_[a-z0-9]+_(rig|tex|lyrig|lib)_', norm)
    if m:
        return m.group(1)
    return "ysj"


def _derive_report_dir(rig_path: str, tex_src: str) -> str:
    """为本次同步推导报告输出目录：<PROJECT_ROOT>/projects/<project>/<timestamp>_<asset>_sync/"""
    import re
    from datetime import datetime

    stem = os.path.splitext(os.path.basename(tex_src or rig_path or "sync"))[0]
    asset_parts = stem.split("_")
    asset = asset_parts[2] if len(asset_parts) > 2 else stem
    project = _infer_project_from_path(rig_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_asset = re.sub(r"[^A-Za-z0-9_-]+", "_", asset)
    return os.path.join(_PROJECT_ROOT, "projects", project, f"{ts}_{safe_asset}_sync")


# Maya 默认着色/渲染列表单例节点——新建 shader / file 纹理 / utility / SG / light
# 会自动连入它们的 multi 属性。locked-asset 交付的 rig 可能把这些用 lockUnpublished 锁住。
_DEFAULT_SHADING_NODES = (
    "defaultShaderList1", "defaultTextureList1", "defaultRenderUtilityList1",
    "defaultLightList1", "defaultRenderingList1", "lightLinker1",
)


def _ensure_shading_nodes_unlocked():
    """清除默认着色/渲染节点的 lockUnpublished 锁（建/复制 SG 与材质前调用）。

    某些 rig 发布时用 ``lockNode -lockUnpublished on`` 把 renderPartition /
    defaultTextureList1 / defaultShaderList1 等默认着色基础节点锁成 "locked container"
    语义，导致其成员 multi 属性不可改；sync 重建 mesh 后新建 SG / file 纹理 / utility
    自动连入这些节点的 sets/textures/shaders[-1] 时会因 'Destination is locked' 崩溃并
    整步回滚。lockUnpublished 与普通 lock 是两个独立标志（节点 lock 可为 False 而
    unpublished 锁仍在），cmds.setAttr / OpenMaya isLocked 都清不掉，必须用
    lockNode(..., lockUnpublished=False)。正常资产无此锁，本函数为无副作用空操作。"""
    targets = list(cmds.ls(type="partition") or [])  # renderPartition 等
    targets += [n for n in _DEFAULT_SHADING_NODES if cmds.objExists(n)]
    found_lock = False
    for node in targets:
        try:
            if (cmds.lockNode(node, q=True, lockUnpublished=True) or [False])[0]:
                cmds.lockNode(node, lockUnpublished=False)
                found_lock = True
            if (cmds.lockNode(node, q=True, lock=True) or [False])[0]:
                cmds.lockNode(node, lock=False)
        except Exception as e:
            logger.debug("清 %s lockUnpublished 失败: %s", node, e)
    # 命中默认节点 lockUnpublished（locked-asset 交付的 rig）才全量兜底扫一遍，
    # 清掉命名集外其它挡住着色连接的锁定节点；正常资产首轮无锁，不触发全扫。
    if found_lock:
        for node in (cmds.ls() or []):
            try:
                if (cmds.lockNode(node, q=True, lockUnpublished=True) or [False])[0]:
                    cmds.lockNode(node, lockUnpublished=False)
            except Exception:
                pass


def execute(payload: dict) -> dict:
    import numpy as np

    _plog("sync execute() entered")
    t0 = time.time()
    sync_inputs = parse_sync_inputs(payload)
    input_errors = validate_sync_inputs(sync_inputs)
    if input_errors:
        return _sync_receipt(
            "ERROR", t0, "input_contract",
            error="; ".join(input_errors),
            recovery_hint="先运行 maya_compare_asset_in_scene 生成 compare_result，并传入 target rig 的 source_path。",
        )

    file_errors = validate_input_files(sync_inputs)
    if file_errors:
        return _sync_receipt(
            "ERROR", t0, "input_files",
            error="; ".join(file_errors),
            recovery_hint="确认 workflow 段间 output_path 已解析，且 source ABC / compare_result 均位于任务沙盒 .info。",
        )

    abc_path = sync_inputs.source_abc
    tex_json = sync_inputs.source_info
    compare_result_path = sync_inputs.compare_result_path
    compare_result_value = sync_inputs.compare_result_data or compare_result_path
    cache_group = sync_inputs.cache_group
    rig_path = sync_inputs.rig_path

    # ── 加载项目 profile（所有算法阈值） ──
    project = sync_inputs.project or _infer_project_from_path(rig_path)
    try:
        from core.config_loader import get_rig_sync_profile
        profile = get_rig_sync_profile(project)
    except Exception:
        from core.config_loader import get_default_rig_sync_profile
        profile = get_default_rig_sync_profile()

    # Profile 覆盖全局常量（仅本次 execute 作用域）
    rig_prefix = profile.get("rig_prefix", RIG_PREFIX)
    min_dot = profile.get("min_dot", MIN_DOT)  # noqa: F841 — 留作未来精化投射使用
    max_k_search = profile.get("max_k_search", MAX_K_SEARCH)  # noqa: F841
    thresh_identical = profile.get("classification", {}).get("precision_exact", THRESH_IDENTICAL)  # noqa: F841

    items = []
    external_report = None
    sync_md_content = ""
    sync_output_details = {}
    all_new_nodes = []
    action_counts = {}

    # ── 读取 source 数据 / 外部 compare_result ──
    try:
        _, external_report, light_tex_info = _load_compare_result(compare_result_value)
        if abc_path:
            from core.abc_reader import read_abc_as_info
            full_tex_info = read_abc_as_info(abc_path)
            if light_tex_info.get("meshes"):
                tex_info = _merge_full_source_info(light_tex_info, full_tex_info)
            else:
                tex_info = full_tex_info
        else:
            if not light_tex_info.get("meshes"):
                with open(tex_json, "r", encoding="utf-8") as f:
                    tex_info = json.load(f)
            else:
                tex_info = light_tex_info
        compare_label = os.path.basename(compare_result_path) if compare_result_path else "上游 output.compare_result"
        items.append(make_item("CompareResult", f"使用对比结果: {compare_label}"))
    except SyncContractError as e:
        return _sync_receipt(
            "ERROR", t0, "compare_result_contract",
            error=str(e),
            recovery_hint="重新运行前置对比 API，确保 compare_result.v1 来自当前版本。",
        )
    except Exception as e:
        return _sync_receipt("ERROR", t0, "source_load", error=f"读取源数据失败: {e}")

    # Sandbox mode: no backup required.

    cmds.undoInfo(openChunk=True, chunkName="sync_rig_incremental_v9")
    try:
        # 个别 rig 发布时把默认着色节点（renderPartition/defaultTextureList1/defaultShaderList1 等）
        # 用 lockNode -lockUnpublished 锁成 locked container，导致 sync 建新 SG / 纹理 / utility
        # 自动连入时 'Destination is locked' 崩溃整步回滚；建着色前先清这些锁（正常资产无锁，无副作用）。
        _ensure_shading_nodes_unlocked()

        # ── Phase 0: 扫描硬编码路径引用（只报告，不改写）──
        hardcoded_refs = _scan_hardcoded_refs(rig_prefix)
        if hardcoded_refs:
            items.append(make_item(
                "HardcodedRefs",
                f"⚠ 场景内有 {len(hardcoded_refs)} 处字符串引用 {rig_prefix}xxx，"
                f"IDENTICAL/ORIG_INJECT 搬运后这些引用将失效，请人工复核。"
            ))
            for node, kind, preview in hardcoded_refs[:20]:  # 最多列 20 条避免淹没
                items.append(make_item(f"ref:{node}", f"{kind}: {preview}"))

        # ── Phase 1: cache 组加 RIG_ 前缀 ──
        from dccs.maya.asset_info_collector import resolve_cache_group
        cache_node = resolve_cache_group(cache_group)
        if not cache_node:
            cmds.undoInfo(closeChunk=True)
            return _sync_receipt(
                "ERROR", t0, "target_preflight",
                error=f"场景中未找到 cache_group: {cache_group}",
                items=items,
                recovery_hint="检查 workflow 的 cache_group 是否来自项目配置，并确认 target rig 场景层级未被改名。",
            )

        cache_leaf = cache_node.strip("|").split("|")[-1]
        rig_cache_group = cache_leaf if cache_leaf.startswith(rig_prefix) else rig_prefix + cache_leaf
        _plog(f"Phase1 start: _add_prefix_recursive on {cache_node}")
        _add_prefix_recursive(cache_node, rig_prefix)
        _plog("Phase1 done: prefix added")

        _plog("Phase2: _collect_rig_meshes start")
        rig_meshes = _collect_rig_meshes(rig_cache_group)
        _plog(f"Phase2: rig_meshes collected, n={len(rig_meshes)}")
        empty_rig_dags = [
            dag for dag, entry in rig_meshes.items()
            if int(entry.get("vertices") or 0) <= 0 or not entry.get("vert_positions")
        ]
        # 采不到 ShapeOrig 的件统一改判“从 tex 重建、不绑定”（Phase 3 消费 unbound_rig_dags）。sync 只做几何。
        unbound_rig_dags = set(empty_rig_dags)
        if empty_rig_dags:
            items.append(make_item(
                "target_collect",
                f"{len(empty_rig_dags)} 个 rig 件采不到 ShapeOrig → 改判从 tex 重建；"
                f"样例: {[d.split('|')[-1] for d in empty_rig_dags[:8]]}"
            ))

        # ── 获取同步指令：只消费前置 compare_result ──
        if external_report is not None:
            report = external_report
        else:
            raise RuntimeError("compare_result 读取失败，缺少外部对比结果。")
        pairing_groups = report["pairing_groups"]
        target_only_dags = report["target_only_dags"]
        action_counts = summarize_sync_actions(report)
        sync_output_details = build_sync_output_details(
            report,
            action_counts,
            compare_result_path=compare_result_path,
            source_abc=abc_path,
            source_info=tex_json,
            cache_group=cache_group,
        )

        # ── 对比摘要写入 receipt/report_content，不额外落散文件 ──
        items.append(make_item(
            "PairingResult",
            f"groups={len(pairing_groups)} target_only={len(target_only_dags)}"
        ))

        try:
            from core.compare_result_io import generate_report
            sync_md_content = generate_report(
                report, abc_path or tex_json, cmds.file(q=True, sn=True) or "current_maya_scene",
                'tex', 'rig',
                abc_path or tex_json, cmds.file(q=True, sn=True)
            )
        except Exception as e:
            items.append(make_item('Report', f'报告生成失败: {e}'))



        # ── 预处理：tex mesh 表（key 保持原样，与 pairing_groups.abc_dags 一致）──
        tex_meshes = dict(tex_info.get("meshes", {}))

        # compare 的 rig_dag 可能来自同步前的真实 Maya fullPath。
        # 预同步修复会把旧 |asset|geo 归一到 |Group|Geometry|RIG_geo，
        # 所以根节点自身的 RIG_ 需要保留一组 lookup，同时子节点继续去前缀。
        rig_dag_lookup = {}

        def _register_rig_lookup(key, full_path):
            if not key:
                return
            rig_dag_lookup[key] = full_path
            if not key.startswith("|"):
                rig_dag_lookup["|" + key] = full_path

        def _strip_rig_parts(parts_seq, keep_first=False, keep_abs_index=None):
            stripped = []
            for local_index, part_data in enumerate(parts_seq):
                if isinstance(part_data, tuple):
                    abs_index, part = part_data
                else:
                    abs_index, part = local_index, part_data
                if not part:
                    continue
                keep_this = (keep_first and local_index == 0) or (keep_abs_index is not None and abs_index == keep_abs_index)
                if keep_this:
                    stripped.append(part)
                elif part.startswith(rig_prefix):
                    stripped.append(part[len(rig_prefix):])
                else:
                    stripped.append(part)
            return stripped

        for full_path in rig_meshes:
            parts = full_path.split("|")
            rig_start = -1
            for i, p in enumerate(parts):
                if p == rig_cache_group:
                    rig_start = i
                    break
            if rig_start >= 0:
                rel_source = parts[rig_start:]
                rel_parts = _strip_rig_parts(rel_source)
                rel_keep_root_parts = _strip_rig_parts(rel_source, keep_first=True)
                _register_rig_lookup("|".join(rel_parts), full_path)
                _register_rig_lookup("|".join(rel_keep_root_parts), full_path)

            indexed_parts = list(enumerate(parts))
            all_rel_parts = _strip_rig_parts(indexed_parts)
            all_keep_root_parts = _strip_rig_parts(indexed_parts, keep_abs_index=rig_start if rig_start >= 0 else None)
            _register_rig_lookup("|".join(all_rel_parts), full_path)
            _register_rig_lookup("|".join(all_keep_root_parts), full_path)

        reused_rig_dags = set()
        voting_pool_tex = {}
        group_records = {}   # group_id → {layer_name, action, abc_nodes, rig_nodes, reason}
        unpaired_nodes = []  # UNPAIRED 新建 mesh 累积（进 _source_only）
        target_only_rig_nodes = []  # target_only 的 rig transform 累积（进 _target_only）

        def _translate_rig(compare_dag):
            """compare 侧 rig_dag → Maya fullPath。

            compare 的 dag_b 就是 rig_meshes 的 key（通常是 Maya fullPath），
            但为兼容归一化/相对路径差异，再走一次前缀 lookup 作为 fallback。
            """
            if compare_dag in rig_meshes:
                return compare_dag
            full = rig_dag_lookup.get(compare_dag)
            if full and full in rig_meshes:
                return full
            return None

        if external_report is not None:
            referenced_rigs = set(target_only_dags)
            for group in pairing_groups:
                referenced_rigs.update(group.get("rig_dags", []))
            missing_rigs = sorted(r for r in referenced_rigs if not _translate_rig(r))
            if missing_rigs:
                cmds.undoInfo(closeChunk=True)
                cmds.undo()
                return _sync_receipt(
                    "AUDIT_FAILED", t0, "compare_target_match",
                    error=(
                        "compare_result 与当前 target rig 不匹配，已撤销本步。"
                        f"缺失 rig 引用: {missing_rigs[:20]}"
                    ),
                    items=items,
                    recovery_hint="不要复用旧 compare_result；请在当前 target rig 场景上重新运行 maya_compare_asset_in_scene。",
                )

        # ── Phase 3: 按 pairing_groups 分发（4 标签）──
        # IDENTICAL    → 搬运 RIG_xxx 到新 cache 对应层级 + 去前缀（不建独立 layer，报告清单）
        # ORIG_INJECT  → 同搬运 + datablock 灌 abc 坐标进 base（1 个独立 layer）
        # PAIRED (M→N) → 每个 abc 进 voting pool，挂 _pairing_group_id + _pairing_rig_candidates
        #                Phase 4 投射权重后，新 mesh 和组内 rig 共同进 1 个 layer
        # UNPAIRED     → 进 voting pool，无 rig 源；Phase 4 走 Chamfer 自动配对；全部进 _source_only
        from dccs.maya.asset_info_collector import get_deform_input
        _plog(f"Phase3: dispatch {len(pairing_groups)} pairing groups")
        for _gi, group in enumerate(pairing_groups):
            if _gi % 10 == 0:
                _plog(f"  Phase3 progress: group {_gi}/{len(pairing_groups)}")
            gid = group["group_id"]
            action = group["action"]
            layer_name = group.get("layer_name", "")
            abc_dags_g = group["abc_dags"]
            rig_dags_g = group["rig_dags"]
            reason = group.get("reason", "")

            rig_fulls = [f for f in (_translate_rig(r) for r in rig_dags_g) if f]

            # IDENTICAL / ORIG_INJECT：1→1 搬运
            if action in ("IDENTICAL", "ORIG_INJECT"):
                if len(abc_dags_g) != 1 or len(rig_fulls) != 1:
                    for dag in abc_dags_g:
                        td = tex_meshes.get(dag)
                        if td:
                            voting_pool_tex[dag] = td
                    items.append(make_item(
                        f"group:{gid}",
                        f"降级 voting pool：{action} 需 1→1 实得 {len(abc_dags_g)}→{len(rig_fulls)}"
                    ))
                    continue

                abc_dag = abc_dags_g[0]
                rig_full = rig_fulls[0]
                tex_data = tex_meshes.get(abc_dag)
                if not tex_data:
                    items.append(make_item(f"group:{gid}", f"{action} 源 abc {abc_dag} 未在 tex_info"))
                    continue

                target_name, group_parts = _get_target_name_and_parent(abc_dag)
                parent_path = _ensure_hierarchy(group_parts)

                # IDENTICAL / ORIG_INJECT 统一：搬运改名 + 从 ABC 全量注入 orig 的 base。
                # 点数不符则降级 voting pool 重建（保留原防呆）。
                rig_data = rig_meshes[rig_full]
                num_v_new = tex_data.get("vertices", 0)
                if num_v_new <= 0 or num_v_new != rig_data.get("vertices", 0):
                    voting_pool_tex[abc_dag] = tex_data
                    items.append(make_item(
                        target_name,
                        f"降级 voting pool：点数不符 (tex={num_v_new} rig={rig_data.get('vertices', 0)})"
                    ))
                    continue

                new_transform = _relocate_rig_mesh(
                    rig_full, target_name, parent_path, rig_prefix
                )
                if new_transform:
                    reused_rig_dags.add(rig_full)
                    # 取变形输入 shape(orig)；无 orig 的静态件回退可见 shape
                    vis_sh, orig_sh = get_deform_input(new_transform)
                    target_sh = orig_sh or vis_sh
                    injected = False
                    if target_sh:
                        _clean_orig_upstream(target_sh)            # 清上游构造历史（禁 delete ch）
                        _regularize_uvset_to_map1(target_sh)       # uvSet 收成唯一 map1（写入前）
                        injected = _inject_mesh_data_via_plug(target_sh, tex_data)  # ABC 点+UV+法线一次写 base
                    items.append(make_item(
                        target_name,
                        f"{action}: 搬运 + ABC注入(点/UV/法线)" + ("" if injected else " [注入跳过]")
                    ))
                    # IDENTICAL/ORIG_INJECT 都不建 layer：仅层级与命名修复，后续 QC 统一标注。
                else:
                    voting_pool_tex[abc_dag] = tex_data
                    items.append(make_item(target_name, f"{action}: 搬运失败，降级 voting pool"))
                continue

            # PAIRED：M→N，每个 abc 进池；Phase 4 新建几何 + 投射组内 rig 权重
            if action == "PAIRED":
                record = {
                    "layer_name": layer_name,
                    "action": action,
                    "abc_nodes": [],  # Phase 4 追加新建的 mesh
                    "rig_nodes": [],  # 填入组内所有 RIG_xxx transform
                    "reason": reason,
                }
                for rf in rig_fulls:
                    tr = cmds.listRelatives(rf, parent=True, fullPath=True)
                    if tr and cmds.objExists(tr[0]):
                        record["rig_nodes"].append(tr[0])
                    reused_rig_dags.add(rf)
                group_records[gid] = record

                # abc 进池，挂组内上下文
                for abc_dag in abc_dags_g:
                    td = tex_meshes.get(abc_dag)
                    if not td:
                        continue
                    if rig_fulls:
                        td["_paired_rig_dag"] = rig_fulls[0]  # 先指向第一个；Phase 4 Chamfer 会 refine
                        td["_pairing_rig_candidates"] = rig_fulls
                    td["_pairing_group_id"] = gid
                    voting_pool_tex[abc_dag] = td

                items.append(make_item(
                    f"group:{gid}({layer_name})",
                    f"PAIRED: {len(abc_dags_g)}abc × {len(rig_fulls)}rig"
                ))
                continue

            # UNPAIRED：abc 独有，进池 + 统一进 _source_only
            if action == "UNPAIRED":
                for abc_dag in abc_dags_g:
                    td = tex_meshes.get(abc_dag)
                    if not td:
                        continue
                    td["_unpaired"] = True
                    voting_pool_tex[abc_dag] = td
                items.append(make_item(
                    f"group:{gid}",
                    f"UNPAIRED: {len(abc_dags_g)} abc → _source_only"
                ))
                continue

            raise RuntimeError(f"compare_result action 未被 sync 分发: group={gid}, action={action}")

        # ── target_only：rig 独有，原位保留（不删！）──
        # 例外（几何-only）：采不到 ShapeOrig 的未绑定件(unbound_rig_dags)无权重可留，
        # 其几何已由 tex 侧 UNPAIRED 在 cache 重建；此处删除旧件，避免 RIG_geo 残留
        # 与 cache 重建件几何重复（去重闸只查 cache 干净路径，看不见 RIG_ 前缀那份）。
        for tod in target_only_dags:
            rig_full = _translate_rig(tod)
            if not rig_full:
                continue
            reused_rig_dags.add(rig_full)  # 防止被当 super mesh 源
            transform = cmds.listRelatives(rig_full, parent=True, fullPath=True)
            if rig_full in unbound_rig_dags:
                if transform and cmds.objExists(transform[0]):
                    try:
                        cmds.delete(transform[0])
                    except Exception:
                        pass
                items.append(make_item(tod.split("|")[-1], "target_only(未绑定): 删除，几何交由 tex 重建"))
                continue
            if transform and cmds.objExists(transform[0]):
                target_only_rig_nodes.append(transform[0])
            items.append(make_item(tod.split("|")[-1], "target_only: 原位保留"))



        # ── Phase 4: 按 voting pool 重建几何（纯几何，不传权重/BS，交绑定师手绑）──
        _plog(f"Phase4: create {len(voting_pool_tex)} new meshes (geometry only)")
        sync_nodes = []
        new_nodes = []

        for _pi, (dag_tex, tex_data) in enumerate(voting_pool_tex.items()):
            _plog(f"  Phase4 mesh {_pi+1}/{len(voting_pool_tex)}: {dag_tex.split('|')[-1]}")
            target_name, group_parts = _get_target_name_and_parent(dag_tex)
            parent_path = _ensure_hierarchy(group_parts)

            exist_path = f"{parent_path}|{target_name}" if parent_path else target_name
            if cmds.objExists(exist_path):
                try:
                    cmds.delete(exist_path)
                except Exception as e:
                    cmds.warning("删除重名路径 %s 失败: %s" % (exist_path, e))

            new_mesh, build_warnings = _create_mesh_from_abc(target_name, tex_data, parent_path)
            if not new_mesh:
                continue
            if build_warnings:
                items.append(make_item(target_name, "WARN: " + "; ".join(build_warnings)[:200]))

            _gid = tex_data.get("_pairing_group_id")
            if _gid and _gid in group_records:
                group_records[_gid]["abc_nodes"].append(new_mesh)
            elif tex_data.get("_unpaired"):
                unpaired_nodes.append(new_mesh)

            new_nodes.append(new_mesh)
            items.append(make_item(target_name, "重建几何，未绑定（交绑定师手绑）"))

        # 只给真正带绑定（有 skinCluster）的新 mesh 补齐标准 ShapeOrig，便于后置 compare 采集；
        # 没匹配/未绑定的 mesh（UNPAIRED 等）保持干净，不注 ShapeOrig（用户 2026-06-30 拍板·R1）。
        created_mesh_nodes = []
        for rec in group_records.values():
            created_mesh_nodes.extend(rec.get("abc_nodes", []))
        created_mesh_nodes.extend(unpaired_nodes)
        created_mesh_nodes = sorted({n for n in created_mesh_nodes if n and cmds.objExists(n)})
        orig_init_errors = []
        orig_created = 0
        for node in created_mesh_nodes:
            skin, _ = _find_skin_cluster(node)
            if not skin:
                continue  # 未绑定 mesh 不注 ShapeOrig，保持干净
            ok, detail = _ensure_collectable_shape_orig(node)
            if not ok:
                orig_init_errors.append(f"{node}: {detail}")
            elif detail == "created":
                orig_created += 1
        if orig_init_errors:
            raise RuntimeError(
                "新建 mesh ShapeOrig 初始化失败: "
                + "; ".join(orig_init_errors[:20])
            )
        if orig_created:
            items.append(make_item("ShapeOrig 初始化", f"为 {orig_created} 个新建 mesh 补齐可采集 ShapeOrig"))
        # ── Phase 5: Layer 分配（按 pairing_groups 组织）──
        _plog(f"Phase5: assign layers, {len(group_records)} groups")
        # 一组一 layer（IDENTICAL 不建）、UNPAIRED 统一进 _source_only、target_only 统一进 _target_only
        # RIG_xxx 节点 displayType=Reference：绑定师可见不可选
        n_group_layers = 0
        for gid, rec in group_records.items():
            lname = rec.get("layer_name") or gid
            abc_nodes_rec = rec.get("abc_nodes", [])
            rig_nodes_rec = rec.get("rig_nodes", [])
            if not abc_nodes_rec and not rig_nodes_rec:
                continue
            # layer 名冲突：已存在就加后缀
            base = lname
            suffix_i = 1
            while cmds.objExists(lname) and cmds.nodeType(lname) != "displayLayer":
                lname = f"{base}_{suffix_i}"
                suffix_i += 1
            _assign_to_layer(
                lname, abc_nodes_rec,
                reference_nodes=rig_nodes_rec,
                group_id=gid, action=rec.get("action"), reason=rec.get("reason"),
            )
            n_group_layers += 1

        if unpaired_nodes:
            _assign_to_layer("_source_only", unpaired_nodes,
                             group_id="_source_only", action="UNPAIRED",
                             reason="abc 独有（无 rig 源）")
        if target_only_rig_nodes:
            _assign_to_layer("_target_only", [],
                             reference_nodes=list(set(target_only_rig_nodes)),
                             group_id="_target_only", action="TARGET_ONLY",
                             reason="rig 独有（abc 侧已无对应）")

        # ── 清理 RIG_ 前缀残留空组（被搬走后可能留空的中间层级）──
        for rc in cmds.ls(rig_cache_group, long=True, type="transform") or []:
            if cmds.objExists(rc):
                live = [m for m in (cmds.listRelatives(rc, allDescendents=True, type="mesh", fullPath=True) or [])
                        if not cmds.getAttr(m + ".intermediateObject")]
                if not live:
                    cmds.delete(rc)

        # ── Phase 6: 汇总新节点；材质由 workflow 的显式 assign_materials 唯一负责 ──
        _plog("Phase6: collect new nodes (material pass is external)")
        all_new_nodes = []
        for rec in group_records.values():
            all_new_nodes.extend(rec.get("abc_nodes", []))
        all_new_nodes.extend(unpaired_nodes)

        # ── 标记 sync 已完成，防重跑 ──
        new_cache_node = None
        for node in cmds.ls("cache", long=True, type="transform") or []:
            if "|cache" in node or node == "cache":
                new_cache_node = node
                break

        # ── 统一清理 .pnts：所有几何写完后，对 cache 组下每片 mesh(可见 + ShapeOrig)一次性清零 ──
        # 终态要求：真实几何存 base(.vrts)、.pnts 恒空、orig 正确再经变形器传给可见 shape
        # （用户 2026-07-09 拍板）。注入已由 _inject_mesh_data_via_plug 把 ABC 点+UV+法线写进
        # base(cachedInMesh)，base 本身即正确；这里清掉一切 .pnts 残留，使 orig=base=ABC、pnts=0。
        # 因 base 已对，清 .pnts 只会归位、不会露脏 base——这正是旧 setPoints/datablock 注入
        # （base 未真写、几何靠 .pnts/上游撑）会被此清理弄塌的根因，改直写 cachedInMesh 后消除。
        # 覆盖 IDENTICAL 与 ORIG_INJECT（已合并统一注入）；新建件本就无残留，清零为 no-op。
        _pnts_cleared = 0
        if new_cache_node and cmds.objExists(new_cache_node):
            for _m in cmds.listRelatives(new_cache_node, allDescendents=True,
                                         type="mesh", fullPath=True) or []:
                _clear_pnts_tweak(_m)
                _pnts_cleared += 1
        if _pnts_cleared:
            items.append(make_item("pnts 清理", f"清零 {_pnts_cleared} 个 mesh shape 的顶点偏移残留"))
        _plog(f"pnts sweep done: {_pnts_cleared} shapes")
        _plog("sync main body done")

    except Exception as e:
        import traceback as _tb
        try:
            with open(os.path.join(str(_PROJECT_ROOT), "logs", "sync_crash.log"), "a", encoding="utf-8", errors="replace") as _f:
                _f.write(f"\n===== SYNC CRASH rig={rig_path} =====\n{_tb.format_exc()}\n")
        except Exception:
            pass
        cmds.undoInfo(closeChunk=True)
        return _sync_receipt(
            "ERROR", t0, "execute",
            error=f"执行崩溃，已撤销: {e}",
            items=items,
            recovery_hint="查看统一任务报告中的 sync items 和 worker 日志，优先定位最后一个 Phase 日志。",
            report_content=sync_md_content,
            output=sync_output_details,
        )

    cmds.undoInfo(closeChunk=True)
    _plog("undo chunk closed")

    return make_receipt(
        API_ID, "SUCCESS", t0,
        input={
            "source_path": rig_path,
            "source_abc": abc_path,
            "source_info": tex_json,
            "compare_result": compare_result_path or "output.compare_result",
            "cache_group": cache_group,
        },
        output={
            "scene": "current_maya_scene",
            "actions": action_counts,
            "new_node_count": len(all_new_nodes),
            **sync_output_details,
        },
        summary_action=format_action_summary(action_counts),
        summary_count=len(all_new_nodes),
        summary_label="资产同步",
        items=items,
        outputs={},
        report_content=sync_md_content,
    )
