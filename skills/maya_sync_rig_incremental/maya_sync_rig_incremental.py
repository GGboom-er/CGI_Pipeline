# skills/sync_rig_incremental.py
# ── Rig-Asset 增量同步 v10 空间投票制 ──
#
# 核心流程：
#   Phase 1: cache 组批量加 RIG_ 前缀
#   Phase 2: 构建全局 Super Mesh 与面索引映射表
#   Phase 3: 提取所有旧 RIG 的 SkinCluster 骨骼与权重
#   Phase 4: 新 Mesh 射频投影 (Spatial Voting)，动态合并/分割 SkinCluster 骨骼，插值权重
#   Phase 5: 显示层分配与清理
#
# v10 核心变更：法线过滤精确投影、多 BS 节点支持、Live Target 深度复刻、阈值参数化。

import os
import json
import time
import gc
import sys
import logging
import re

import numpy as np
import maya.cmds as cmds
from maya.api import OpenMaya as om2
from maya.api import OpenMayaAnim as oma2

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT
from core.live_bs_transfer import compose_live_target_weights
from core.receipt import make_receipt, make_item
from skills.maya_sync_rig_incremental.sync_contract import (
    SKILL_ID,
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
        SKILL_ID, status, start_time,
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



def _build_adjacency(num_verts, face_indices, face_counts):
    adjacency = [set() for _ in range(num_verts)]
    idx = 0
    for count in face_counts:
        face_verts = face_indices[idx:idx+count]
        for i in range(count):
            v1 = face_verts[i]
            v2 = face_verts[(i+1)%count]
            adjacency[v1].add(v2)
            adjacency[v2].add(v1)
        idx += count
    return [list(s) for s in adjacency]

def _topological_smooth(V, adj, data, iterations=30, blend=0.5):
    """
    使用纯 Numpy 的无向边向量化平滑（np.add.at），彻底消除 Python 大循环瓶颈。
    :param V: 顶点数量
    :param adj: 邻接表 [list(nbrs), ...]
    :param data: 需要平滑的矩阵，如 weights (V, J) 或 BS deltas (V, 3)
    """
    import numpy as np
    
    # 构建双向边列表 (E, 2)
    u_list, v_list = [], []
    for i, nbrs in enumerate(adj):
        for nbr in nbrs:
            u_list.append(i)
            v_list.append(nbr)
            
    if not u_list:
        return data  # 没有任何边（散点云），直接返回
        
    edges = np.column_stack((u_list, v_list))
    
    # 预计算每个顶点的度数倒数
    degrees = np.bincount(edges[:, 0], minlength=V)[:, None]
    inv_deg = np.zeros_like(degrees, dtype=np.float64)
    valid_mask = degrees > 0
    inv_deg[valid_mask] = 1.0 / degrees[valid_mask]
    
    data_curr = data.copy()
    for _ in range(iterations):
        data_sum = np.zeros_like(data_curr)
        # 核心：将相邻顶点的 data 加到中心顶点上
        np.add.at(data_sum, edges[:, 0], data_curr[edges[:, 1]])
        data_avg = data_sum * inv_deg
        
        # 对于有邻居的顶点，应用混合平滑；没有邻居的顶点保持原样
        data_new = data_curr.copy()
        data_new[valid_mask[:, 0]] = data_curr[valid_mask[:, 0]] * (1.0 - blend) + data_avg[valid_mask[:, 0]] * blend
        data_curr = data_new
        
    return data_curr


def _mesh_object_points(shape):
    sel = om2.MSelectionList()
    sel.add(shape)
    fn_mesh = om2.MFnMesh(sel.getDagPath(0))
    pts = fn_mesh.getPoints(om2.MSpace.kObject)
    return np.asarray([[p.x, p.y, p.z] for p in pts], dtype=np.float64)


def _alias_weight_index_map(bs_node):
    alias_list = cmds.aliasAttr(bs_node, query=True) or []
    out = {}
    for i in range(0, len(alias_list), 2):
        attr_ref = str(alias_list[i + 1])
        match = re.search(r"weight\[(\d+)\]", attr_ref)
        if match:
            out[str(alias_list[i])] = int(match.group(1))
    return out


def _disconnect_incoming_plugs(attr_path):
    sources = cmds.listConnections(attr_path, source=True, destination=False, plugs=True) or []
    for src in sources:
        try:
            cmds.disconnectAttr(src, attr_path)
        except RuntimeError:
            logger.debug("断开连接失败 %s -> %s", src, attr_path)
    return sources


def _restore_incoming_plugs(sources, attr_path):
    for src in sources or []:
        if not cmds.objExists(src) or not cmds.objExists(attr_path):
            continue
        try:
            cmds.connectAttr(src, attr_path, force=True)
        except RuntimeError:
            logger.debug("恢复连接失败 %s -> %s", src, attr_path)


def _set_static_blendshape_delta(bs_node, target_name, target_index, item_index, delta):
    sel_bs = om2.MSelectionList()
    sel_bs.add(bs_node)
    fn_bs = om2.MFnDependencyNode(sel_bs.getDependNode(0))
    fn_bs.findPlug("weight", False).elementByLogicalIndex(target_index)
    try:
        cmds.aliasAttr(target_name, f"{bs_node}.weight[{target_index}]")
    except RuntimeError:
        pass

    it_plug = fn_bs.findPlug("inputTarget", False)
    geom_indices = it_plug.getExistingArrayAttributeIndices()
    geom_idx = geom_indices[0] if geom_indices else 0
    itg_plug = it_plug.elementByLogicalIndex(geom_idx).child(0)
    tgt_plug = itg_plug.elementByLogicalIndex(target_index)
    iti_plug = tgt_plug.child(0).elementByLogicalIndex(item_index)

    ipt_plug = None
    ict_plug = None
    for ci in range(iti_plug.numChildren()):
        child = iti_plug.child(ci)
        attr_name = om2.MFnAttribute(child.attribute()).name
        if attr_name == "inputPointsTarget":
            ipt_plug = child
        elif attr_name == "inputComponentsTarget":
            ict_plug = child
    if not ipt_plug or not ict_plug:
        return 0

    delta = np.asarray(delta, dtype=np.float64)
    sparse_ids_arr = np.where(np.linalg.norm(delta, axis=1) > 1e-7)[0]
    if len(sparse_ids_arr) == 0:
        return 0

    sparse_pts = om2.MPointArray()
    for si in sparse_ids_arr:
        d = delta[si]
        sparse_pts.append(om2.MPoint(float(d[0]), float(d[1]), float(d[2])))

    ipt_plug.setMObject(om2.MFnPointArrayData().create(sparse_pts))
    fn_comp = om2.MFnSingleIndexedComponent()
    comp_obj = fn_comp.create(om2.MFn.kMeshVertComponent)
    fn_comp.addElements(sparse_ids_arr.tolist())
    fn_comp_data = om2.MFnComponentListData()
    comp_list_obj = fn_comp_data.create()
    fn_comp_data.add(comp_obj)
    ict_plug.setMObject(comp_list_obj)
    return int(len(sparse_ids_arr))


def _interpolate_field_from_kd(num_new_verts, old_field, i_idx, k_idx, local_v, w_gauss):
    old_field = np.asarray(old_field, dtype=np.float64)
    if old_field.ndim == 1:
        old_field = old_field[:, None]
    out = np.zeros((num_new_verts, old_field.shape[1]), dtype=np.float64)
    if len(i_idx) == 0 or old_field.shape[0] == 0:
        return out
    valid = (local_v >= 0) & (local_v < old_field.shape[0])
    if not np.any(valid):
        return out
    i_ok = i_idx[valid]
    k_ok = k_idx[valid]
    l_ok = local_v[valid]
    w_val = w_gauss[i_ok, k_ok]
    np.add.at(out, i_ok, old_field[l_ok] * w_val[:, None])
    return out


def _interpolate_field_directed(num_new_verts, old_field, query_points, use_tnb, matched_face_idx,
                                bary_coords, src_faces, src_verts,
                                nearest_indices=None, w_gauss=None):
    """按定向投射结果映射任意 per-vertex field。"""
    old_field = np.asarray(old_field, dtype=np.float64)
    if old_field.ndim == 1:
        old_field = old_field[:, None]
    out = np.zeros((num_new_verts, old_field.shape[1]), dtype=np.float64)
    if old_field.shape[0] == 0:
        return out

    if use_tnb and matched_face_idx is not None and old_field.shape[0] == len(src_verts):
        from core.spatial_transfer import barycentric_delta_transfer
        mapped, _ = barycentric_delta_transfer(
            matched_face_idx, bary_coords, src_faces, old_field
        )
        return np.asarray(mapped, dtype=np.float64).reshape(num_new_verts, old_field.shape[1])

    if nearest_indices is not None and w_gauss is not None:
        k_eff = min(nearest_indices.shape[1], w_gauss.shape[1])
        idx = nearest_indices[:, :k_eff]
        wg = w_gauss[:, :k_eff]
        valid = (idx >= 0) & (idx < old_field.shape[0])
        for ki in range(k_eff):
            ok = valid[:, ki]
            if np.any(ok):
                out[ok] += old_field[idx[ok, ki]] * wg[ok, ki:ki + 1]
        return out

    from scipy.spatial import cKDTree
    src_subset = np.asarray(src_verts[:old_field.shape[0]], dtype=np.float64)
    tree = cKDTree(src_subset)
    _, idx = tree.query(np.asarray(query_points, dtype=np.float64), k=1)
    idx = np.clip(idx, 0, old_field.shape[0] - 1)
    return old_field[idx]


def _triangulate_indexed_faces(face_indices, face_counts):
    if not face_indices or not face_counts:
        return np.zeros((0, 3), dtype=np.int64)
    from core.spatial_transfer import triangulate_faces
    faces, _ = triangulate_faces(face_indices, face_counts)
    return np.asarray(faces, dtype=np.int64)


def _semantic_delta_override(src_vertices, src_faces, src_delta,
                             query_vertices, query_faces, base_delta,
                             source_weights=None, source_joints=None,
                             target_weights=None):
    """用语义支持域覆盖嘴部 BS delta，避免 upper/lower lip 最近点互串。"""
    if source_weights is None or source_joints is None or target_weights is None:
        return None

    source_weights = np.asarray(source_weights, dtype=np.float64)
    target_weights = np.asarray(target_weights, dtype=np.float64)
    if (
        source_weights.ndim != 2
        or target_weights.ndim != 2
        or source_weights.shape[0] != src_vertices.shape[0]
        or target_weights.shape[0] != query_vertices.shape[0]
        or source_weights.shape[1] != target_weights.shape[1]
    ):
        return None

    try:
        from core.topology_support_matcher import (
            DEFAULT_DEFORMATION_FAMILY_RULES,
            TopologySupportMatcher,
            compute_family_scores,
        )
    except Exception as exc:
        logger.debug("语义 delta 覆盖不可用: %s", exc)
        return None

    source_joints = list(source_joints)
    families, source_scores, _ = compute_family_scores(
        source_weights, source_joints, DEFAULT_DEFORMATION_FAMILY_RULES
    )
    _, target_scores, _ = compute_family_scores(
        target_weights, source_joints, DEFAULT_DEFORMATION_FAMILY_RULES
    )
    if source_scores.size == 0 or target_scores.size == 0:
        return None

    family_index = {family: idx for idx, family in enumerate(families)}
    mouth_family_ids = [
        family_index[name]
        for name in ("upper_lip", "lower_lip", "jaw")
        if name in family_index
    ]
    if not mouth_family_ids:
        return None

    has_source_mouth = any(float(source_scores[:, idx].max()) > 0.30 for idx in mouth_family_ids)
    has_target_mouth = any(float(target_scores[:, idx].max()) > 0.30 for idx in mouth_family_ids)
    if not (has_source_mouth and has_target_mouth):
        return None

    top = np.argmax(target_scores, axis=1)
    sorted_scores = np.sort(target_scores, axis=1)
    margin = sorted_scores[:, -1] - sorted_scores[:, -2] if target_scores.shape[1] > 1 else sorted_scores[:, -1]
    top_score = target_scores[np.arange(target_scores.shape[0]), top]

    seeds = {}
    for vertex_index, family_id in enumerate(top):
        if int(family_id) not in mouth_family_ids:
            continue
        if top_score[vertex_index] < 0.55 or margin[vertex_index] < 0.15:
            continue
        seeds[int(vertex_index)] = families[int(family_id)]

    if len(seeds) < 8:
        return None

    try:
        matcher = TopologySupportMatcher(
            source_vertices=src_vertices,
            source_faces=src_faces,
            source_weights=source_weights,
            joint_names=source_joints,
            family_rules=DEFAULT_DEFORMATION_FAMILY_RULES,
            support_threshold=0.30,
            min_island_vertices=3,
        )
        result = matcher.transfer_values(
            src_delta,
            query_vertices,
            query_faces,
            target_seed_labels=seeds,
            k=8,
            auto_seed=False,
            normal_weight=0.0,
            motion_weight=0.0,
        )
    except Exception as exc:
        logger.warning("语义支持域 BS delta 覆盖失败: %s", exc)
        return None

    mouth_mask = np.isin(top, np.asarray(mouth_family_ids, dtype=np.int64))
    mouth_mask &= top_score >= 0.20
    mouth_mask &= result.confidence >= 0.20
    if not np.any(mouth_mask):
        return None

    mixed = np.asarray(base_delta, dtype=np.float64).copy()
    mixed[mouth_mask] = np.asarray(result.values, dtype=np.float64)[mouth_mask]
    logger.info(
        "Semantic BS delta override: %d/%d mouth vertices, seeds=%d",
        int(np.sum(mouth_mask)),
        int(query_vertices.shape[0]),
        len(seeds),
    )
    return mixed


def _sample_delta_deformation_field(src_vertices, src_faces, src_delta,
                                    query_vertices, query_faces, name,
                                    source_weights=None, source_joints=None,
                                    target_weights=None):
    """用统一 DeformationField 采样 BS delta，避免动态 BS 走 TNB 近似。"""
    src_vertices = np.ascontiguousarray(src_vertices, dtype=np.float64)
    src_faces = np.ascontiguousarray(src_faces, dtype=np.int64)
    src_delta = np.ascontiguousarray(src_delta, dtype=np.float64)
    query_vertices = np.ascontiguousarray(query_vertices, dtype=np.float64)
    query_faces = np.ascontiguousarray(query_faces, dtype=np.int64)
    if (
        src_vertices.shape[0] == 0
        or src_faces.shape[0] == 0
        or src_delta.shape[0] != src_vertices.shape[0]
        or query_vertices.shape[0] == 0
        or query_faces.shape[0] == 0
    ):
        return np.zeros((query_vertices.shape[0], 3), dtype=np.float64)

    from core.deformation_field import DeformationField
    from core.spatial_transfer import compute_vertex_normals

    rig_data = {
        "all_joints": ["__bs_probe_root__"],
        "meshes": [{
            "vertices": src_vertices,
            "faces": src_faces,
            "normals": np.ascontiguousarray(compute_vertex_normals(src_vertices, src_faces), dtype=np.float64),
            "weights": np.zeros((src_vertices.shape[0], 1), dtype=np.float64),
            "bs_deltas": {name: src_delta},
        }],
    }
    field = DeformationField(rig_data)
    sampled = field.sample_bs_deltas(
        query_vertices,
        query_faces,
        new_normals=np.ascontiguousarray(compute_vertex_normals(query_vertices, query_faces), dtype=np.float64),
        use_winding=False,
        idw_k=4,
        idw_blend=0.3,
        use_deformation_gradient=True,
    )
    base_delta = np.ascontiguousarray(sampled.get(name, np.zeros((query_vertices.shape[0], 3))), dtype=np.float64)
    semantic_delta = _semantic_delta_override(
        src_vertices,
        src_faces,
        src_delta,
        query_vertices,
        query_faces,
        base_delta,
        source_weights=source_weights,
        source_joints=source_joints,
        target_weights=target_weights,
    )
    if semantic_delta is not None:
        return np.ascontiguousarray(semantic_delta, dtype=np.float64)
    return base_delta


def _sample_weight_deformation_field(src_vertices, src_faces, src_weights,
                                     joints, query_vertices, query_faces):
    """用统一 DeformationField 采样 live target skin 权重。"""
    src_vertices = np.ascontiguousarray(src_vertices, dtype=np.float64)
    src_faces = np.ascontiguousarray(src_faces, dtype=np.int64)
    src_weights = np.ascontiguousarray(src_weights, dtype=np.float64)
    query_vertices = np.ascontiguousarray(query_vertices, dtype=np.float64)
    query_faces = np.ascontiguousarray(query_faces, dtype=np.int64)
    if (
        src_vertices.shape[0] == 0
        or src_faces.shape[0] == 0
        or src_weights.shape[0] != src_vertices.shape[0]
        or query_vertices.shape[0] == 0
        or query_faces.shape[0] == 0
    ):
        return np.zeros((query_vertices.shape[0], len(joints)), dtype=np.float64)

    from core.deformation_field import DeformationField
    from core.spatial_transfer import compute_vertex_normals

    rig_data = {
        "all_joints": list(joints),
        "meshes": [{
            "vertices": src_vertices,
            "faces": src_faces,
            "normals": np.ascontiguousarray(compute_vertex_normals(src_vertices, src_faces), dtype=np.float64),
            "weights": src_weights,
        }],
    }
    field = DeformationField(rig_data)
    weights, _ = field.sample_weights(
        query_vertices,
        query_faces,
        new_normals=np.ascontiguousarray(compute_vertex_normals(query_vertices, query_faces), dtype=np.float64),
        use_winding=False,
        idw_k=4,
        idw_blend=0.7,
        smooth_iterations=0,
    )
    return np.ascontiguousarray(weights, dtype=np.float64)


def _duplicate_clean_mesh(mesh, name):
    dup = cmds.duplicate(
        mesh,
        name=name,
        inputConnections=False,
        upstreamNodes=False,
    )[0]
    history = cmds.listHistory(dup) or []
    for node_type in ("skinCluster", "blendShape"):
        nodes = cmds.ls(history, type=node_type) or []
        if nodes:
            try:
                cmds.delete(nodes)
            except RuntimeError:
                logger.debug("清理复制 mesh 历史失败: %s", nodes)
    return dup


def _bind_mesh_weights(mesh, skin_name, joints, weights):
    weights = np.asarray(weights, dtype=np.float64)
    if weights.ndim != 2 or weights.shape[0] == 0 or weights.shape[1] != len(joints):
        return None

    active_cols = [
        idx for idx, joint in enumerate(joints)
        if cmds.objExists(joint) and float(weights[:, idx].max()) > 0.0
    ]
    if not active_cols:
        return None

    bind_joints = [joints[idx] for idx in active_cols]
    compact_w = weights[:, active_cols]
    row_sums = compact_w.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    compact_w = compact_w / row_sums

    skin = cmds.skinCluster(
        mesh, bind_joints, toSelectedBones=True,
        bindMethod=0, skinMethod=0, normalizeWeights=1,
        name=skin_name,
    )[0]

    shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True, noIntermediate=True)
    if not shapes:
        return skin

    sel_skin = om2.MSelectionList()
    sel_skin.add(skin)
    fn_skin = oma2.MFnSkinCluster(sel_skin.getDependNode(0))

    sel_shape = om2.MSelectionList()
    sel_shape.add(shapes[0])
    dag = sel_shape.getDagPath(0)
    num_verts = om2.MFnMesh(dag).numVertices
    comp = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
    om2.MFnSingleIndexedComponent(comp).setCompleteData(num_verts)

    inf_idx = om2.MIntArray(list(range(len(bind_joints))))
    fn_skin.setWeights(dag, comp, inf_idx, om2.MDoubleArray(compact_w.flatten().tolist()))
    return skin


def _extract_live_inner_blendshapes(live_transform, body_shape, outer_attr):
    """采集 live target 内部 BS，delta 以最终 body 输出为准。"""
    inner_nodes = sorted(set(cmds.ls(cmds.listHistory(live_transform, pruneDagObjects=True) or [], type="blendShape") or []))
    if not inner_nodes:
        return []

    outer_original = None
    outer_can_set = cmds.objExists(outer_attr)
    if outer_can_set:
        try:
            outer_original = cmds.getAttr(outer_attr)
            cmds.setAttr(outer_attr, 1.0)
        except RuntimeError:
            outer_can_set = False

    original_values = {}
    incoming_by_attr = {}
    targets = []
    try:
        for node in inner_nodes:
            for alias in _alias_weight_index_map(node):
                attr = f"{node}.{alias}"
                if not cmds.objExists(attr):
                    continue
                incoming_by_attr[attr] = _disconnect_incoming_plugs(attr)
                try:
                    original_values[attr] = cmds.getAttr(attr)
                    cmds.setAttr(attr, 0.0)
                except RuntimeError:
                    original_values[attr] = 0.0

        body_outer = _mesh_object_points(body_shape)
        for node in inner_nodes:
            for alias, target_index in sorted(_alias_weight_index_map(node).items(), key=lambda item: item[1]):
                attr = f"{node}.{alias}"
                if not cmds.objExists(attr):
                    continue
                try:
                    cmds.setAttr(attr, 1.0)
                except RuntimeError:
                    continue
                body_delta = _mesh_object_points(body_shape) - body_outer
                try:
                    cmds.setAttr(attr, 0.0)
                except RuntimeError:
                    pass
                if np.any(np.abs(body_delta) > 1e-7):
                    targets.append({
                        "node": node,
                        "name": alias,
                        "target_index": int(target_index),
                        "item_index": 6000,
                        "body_delta": body_delta,
                        "upstream_plugs": list(incoming_by_attr.get(attr, [])),
                    })
    finally:
        for attr, value in original_values.items():
            if cmds.objExists(attr):
                try:
                    cmds.setAttr(attr, value)
                except RuntimeError:
                    pass
                _restore_incoming_plugs(incoming_by_attr.get(attr), attr)
        if outer_can_set and outer_original is not None and cmds.objExists(outer_attr):
            try:
                cmds.setAttr(outer_attr, outer_original)
            except RuntimeError:
                pass

    return targets


def _apply_live_inner_blendshapes_mapped(live_dup, live_info, map_field, active_mask):
    inner_targets = (live_info or {}).get("inner_blendshapes") or []
    if not inner_targets:
        return 0

    inner_bs = None
    written = 0
    for target in inner_targets:
        if inner_bs is None:
            old_node = str(target.get("node") or "blendShape").replace(RIG_PREFIX, "")
            inner_bs = cmds.blendShape(
                live_dup,
                name=f"{live_dup}_{old_node}",
                frontOfChain=True,
                origin="world",
            )[0]

        old_delta = target.get("body_delta")
        if old_delta is None:
            continue
        new_delta = np.asarray(map_field(old_delta), dtype=np.float64)
        if active_mask is not None and len(active_mask) == new_delta.shape[0]:
            new_delta[~active_mask] = 0.0
        sparse_count = _set_static_blendshape_delta(
            inner_bs,
            target.get("name") or f"target_{target.get('target_index', written)}",
            int(target.get("target_index", written)),
            int(target.get("item_index", 6000)),
            new_delta,
        )
        if sparse_count <= 0:
            continue

        attr_path = f"{inner_bs}.{target.get('name')}"
        connected = False
        for src_plug in target.get("upstream_plugs") or []:
            if cmds.objExists(src_plug) and cmds.objExists(attr_path):
                try:
                    cmds.connectAttr(src_plug, attr_path, force=True)
                    connected = True
                except RuntimeError:
                    pass
        if cmds.objExists(attr_path) and not connected:
            try:
                cmds.setAttr(attr_path, 0.0)
            except RuntimeError:
                pass
        written += 1
    return written


def _apply_live_inner_blendshapes(live_dup, live_info, num_new_verts,
                                  i_idx, k_idx, local_v, w_gauss, active_mask):
    def _map_field(old_delta):
        return _interpolate_field_from_kd(
            num_new_verts, old_delta, i_idx, k_idx, local_v, w_gauss
        )
    return _apply_live_inner_blendshapes_mapped(
        live_dup, live_info, _map_field, active_mask
    )


def _directed_weight_transfer(new_mesh, new_verts, num_new_verts, new_normals, tex_data,
                              paired_rig_dag, rig_meshes, rig_skin_data, rig_bs_data,
                              target_name, items, sync_nodes, new_nodes, profile=None):
    import numpy as np

    # Profile 解析（可空 → 默认值）
    tnb_cfg = (profile or {}).get("tnb_projection", {})
    diff_cfg = (profile or {}).get("diffuse", {})
    max_angle = tnb_cfg.get("max_normal_angle_deg", 90.0)
    k_cand = tnb_cfg.get("k_candidates", 8)
    diff_backend = diff_cfg.get("backend", "scipy")
    fb_sigma = diff_cfg.get("fallback_sigma", 0.05)
    fb_k = diff_cfg.get("fallback_k", 3)

    sk_data = rig_skin_data[paired_rig_dag]
    src_joints = sk_data["joints"]
    src_weights = sk_data["weights"]
    num_src_verts = src_weights.shape[0]

    rig_entry = rig_meshes[paired_rig_dag]
    src_verts_flat = rig_entry.get("vert_positions", [])
    if not src_verts_flat:
        return False

    src_verts = np.array(src_verts_flat, dtype=np.float64).reshape(-1, 3)

    # 获取源 mesh 三角面
    src_face_indices = rig_entry.get("face_indices", [])
    src_face_counts = rig_entry.get("face_counts", [])

    # 获取目标 mesh 三角面
    dst_face_indices = tex_data.get("face_indices", [])
    dst_face_counts = tex_data.get("face_counts", [])

    # 尝试使用 TNB 投射引擎（需要三角面信息）
    use_tnb = (len(src_face_indices) > 0 and len(src_face_counts) > 0 and
               len(dst_face_indices) > 0 and len(dst_face_counts) > 0)
    src_tri_faces_for_field = _triangulate_indexed_faces(src_face_indices, src_face_counts)
    dst_tri_faces_for_field = _triangulate_indexed_faces(dst_face_indices, dst_face_counts)
    matched_face_idx = None
    bary_coords = None
    nearest_indices = None
    w_gauss = None

    if use_tnb:
        from core.spatial_transfer import (
            triangulate_faces, transfer_weights_tnb,
            find_closest_triangle_with_normal, barycentric_delta_transfer,
            compute_face_normals,
        )

        src_faces, _ = triangulate_faces(src_face_indices, src_face_counts)
        dst_faces, _ = triangulate_faces(dst_face_indices, dst_face_counts)

        new_weights, stats = transfer_weights_tnb(
            src_verts, src_faces, src_weights,
            new_verts, dst_faces,
            dst_normals=new_normals if new_normals is not None else None,
            max_normal_angle_deg=max_angle,
            k_candidates=k_cand,
            diffuse_backend=diff_backend,
        )
        n_projected = stats["n_projected"]
        transfer_method = f"TNB ({n_projected}/{num_new_verts} projected)"

        # 缓存投射结果用于 BS delta
        src_face_normals = compute_face_normals(src_verts, src_faces)
        from core.spatial_transfer import compute_vertex_normals
        if new_normals is None:
            dst_normals_arr = compute_vertex_normals(new_verts, dst_faces)
        else:
            dst_normals_arr = np.asarray(new_normals, dtype=np.float64)
        from core.spatial_transfer import find_closest_triangle_with_normal as _find_tri
        matched_face_idx, bary_coords, _ = _find_tri(
            new_verts, dst_normals_arr,
            src_verts, src_faces, src_face_normals,
            max_normal_angle_deg=max_angle, k_candidates=k_cand
        )
    else:
        # Fallback: 旧版 KDTree K=k + 高斯加权
        from scipy.spatial import cKDTree
        kd_tree = cKDTree(src_verts)
        k_eff = min(fb_k, len(src_verts))
        dists, nearest_indices = kd_tree.query(new_verts, k=k_eff)
        if k_eff == 1:
            dists = dists[:, None]
            nearest_indices = nearest_indices[:, None]

        sigma = fb_sigma
        w_gauss = np.exp(-(dists**2) / (2 * sigma**2))
        row_sums = w_gauss.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        w_gauss /= row_sums

        src_w = src_weights[nearest_indices]
        new_weights = np.sum(src_w * w_gauss[:, :, None], axis=1)

        valid_mask = dists[:, 0] <= fb_sigma
        valid_idx = np.where(valid_mask)[0]
        if 0 < len(valid_idx) < num_new_verts:
            from core.laplacian_diffuse import laplacian_diffuse
            adjacency = _build_adjacency(num_new_verts, dst_face_indices, dst_face_counts)
            new_weights = laplacian_diffuse(adjacency, valid_idx, new_weights[valid_idx], num_new_verts, len(src_joints))
        elif len(valid_idx) == 0:
            return False

        n_projected = int((dists[:, 0] <= fb_sigma).sum()) if 'dists' in dir() else num_new_verts
        transfer_method = f"GaussK{fb_k} ({n_projected}/{num_new_verts} verts)"
        matched_face_idx = None  # 标记不可用

    # Normalize
    row_sums = new_weights.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    new_weights = new_weights / row_sums

    # 写入 SkinCluster
    active_cols = np.where(new_weights.max(axis=0) > 0.0)[0]
    if len(active_cols) == 0:
        return False

    bind_joints = [src_joints[c] for c in active_cols]
    existing_joints = [j for j in bind_joints if cmds.objExists(j)]
    if not existing_joints:
        return False

    if len(existing_joints) < len(bind_joints):
        exist_mask = np.array([cmds.objExists(src_joints[c]) for c in active_cols])
        active_cols = active_cols[exist_mask]
        bind_joints = [src_joints[c] for c in active_cols]

    compact_w = new_weights[:, active_cols]

    try:
        new_skin = cmds.skinCluster(
            new_mesh, bind_joints, toSelectedBones=True,
            bindMethod=0, skinMethod=0, normalizeWeights=1,
            name=target_name + "_skinCluster")[0]

        sel_ns = om2.MSelectionList()
        sel_ns.add(new_skin)
        fn_ns = oma2.MFnSkinCluster(sel_ns.getDependNode(0))

        new_shapes = cmds.listRelatives(new_mesh, shapes=True, fullPath=True, noIntermediate=True)
        sel_nsh = om2.MSelectionList()
        sel_nsh.add(new_shapes[0])
        nd = sel_nsh.getDagPath(0)

        nnv = om2.MFnMesh(nd).numVertices
        nc = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
        om2.MFnSingleIndexedComponent(nc).setCompleteData(nnv)

        inf_idx = om2.MIntArray(list(range(len(bind_joints))))
        fn_ns.setWeights(nd, nc, inf_idx, om2.MDoubleArray(compact_w.flatten().tolist()))

        sync_nodes.append(new_mesh)
        items.append(make_item(target_name,
            f"DIRECTED: {transfer_method} 从 {paired_rig_dag.split('|')[-1]} 投射"))

        # BlendShape 定向投射
        if paired_rig_dag in rig_bs_data:
            bs_info_list = rig_bs_data[paired_rig_dag]
            bs_count = 0
            for bs_info in bs_info_list:
                if not bs_info["targets"]:
                    continue
                old_bs_name = bs_info.get("bs_node", "blendShape")
                new_bs_suffix = old_bs_name.replace(RIG_PREFIX, "")
                try:
                    new_bs = cmds.blendShape(new_mesh,
                                             name=f"{target_name}_{new_bs_suffix}",
                                             frontOfChain=True)[0]
                    sel_bs = om2.MSelectionList()
                    sel_bs.add(new_bs)
                    fn_bs = om2.MFnDependencyNode(sel_bs.getDependNode(0))
                    it_plug = fn_bs.findPlug("inputTarget", False)
                    geom_indices = it_plug.getExistingArrayAttributeIndices()
                    geom_idx = geom_indices[0] if geom_indices else 0
                    itg_plug = it_plug.elementByLogicalIndex(geom_idx).child(0)

                    for tgt in bs_info["targets"]:
                        t_idx = tgt["index"]
                        w_plug = fn_bs.findPlug("weight", False)
                        w_plug.elementByLogicalIndex(t_idx)
                        try:
                            cmds.aliasAttr(tgt["name"], f"{new_bs}.weight[{t_idx}]")
                        except:
                            pass

                        tgt_plug = itg_plug.elementByLogicalIndex(t_idx)
                        iti_array_plug = tgt_plug.child(0)

                        for item_data in tgt["items"]:
                            item_idx = item_data["item_index"]
                            old_delta = item_data["delta"]
                            live_info = item_data.get("live_info")

                            num_old_delta = len(old_delta)
                            new_delta = np.zeros((num_new_verts, 3), dtype=np.float64)

                            # BS Delta 插值：优先用重心坐标，fallback 到 KDTree
                            if use_tnb and matched_face_idx is not None:
                                old_delta_arr = np.array(old_delta, dtype=np.float64).reshape(-1, 3)
                                if len(old_delta_arr) == num_src_verts:
                                    from core.spatial_transfer import barycentric_delta_transfer
                                    new_delta, _ = barycentric_delta_transfer(
                                        matched_face_idx, bary_coords, src_faces, old_delta_arr
                                    )
                                else:
                                    # delta 数组长度不匹配源顶点数，用最近点
                                    from scipy.spatial import cKDTree as _cKDTree
                                    _tree = _cKDTree(src_verts[:len(old_delta_arr)])
                                    _d, _idx = _tree.query(new_verts, k=1)
                                    new_delta = old_delta_arr[_idx]
                            else:
                                # Fallback: 旧版 KDTree K=3 高斯插值
                                valid_v = (nearest_indices >= 0) & (nearest_indices < num_old_delta)
                                if np.any(valid_v):
                                    for ni in range(num_new_verts):
                                        for ki in range(nearest_indices.shape[1]):
                                            if valid_v[ni, ki]:
                                                new_delta[ni] += old_delta[nearest_indices[ni, ki]] * w_gauss[ni, ki]

                            live_delta_raw = new_delta.copy()
                            if live_info and src_tri_faces_for_field.shape[0] and dst_tri_faces_for_field.shape[0]:
                                live_delta_raw = _sample_delta_deformation_field(
                                    src_verts,
                                    src_tri_faces_for_field,
                                    np.asarray(old_delta, dtype=np.float64).reshape(-1, 3),
                                    new_verts,
                                    dst_tri_faces_for_field,
                                    tgt["name"],
                                    source_weights=src_weights,
                                    source_joints=src_joints,
                                    target_weights=new_weights,
                                )

                            # Live Target 深度复刻：复制动态目标 mesh，迁移 live skin 与内部 BS。
                            if live_info and live_info.get("transform"):
                                try:
                                    live_query_verts = new_verts + live_delta_raw
                                    active_mask = np.linalg.norm(live_delta_raw, axis=1) > 1e-7
                                    live_dup = _duplicate_clean_mesh(new_mesh, f"{target_name}_live_{t_idx}")
                                    ld_shapes = cmds.listRelatives(live_dup, shapes=True, fullPath=True, noIntermediate=True)
                                    if not ld_shapes:
                                        raise RuntimeError("live duplicate 缺少可见 shape")
                                    sel_ld = om2.MSelectionList()
                                    sel_ld.add(ld_shapes[0])
                                    fn_ld = om2.MFnMesh(sel_ld.getDagPath(0))
                                    base_live_pts = fn_ld.getPoints(om2.MSpace.kObject)
                                    for vi in range(min(num_new_verts, len(base_live_pts))):
                                        base_live_pts[vi].x += live_delta_raw[vi][0]
                                        base_live_pts[vi].y += live_delta_raw[vi][1]
                                        base_live_pts[vi].z += live_delta_raw[vi][2]
                                    fn_ld.setPoints(base_live_pts, om2.MSpace.kObject)

                                    live_skin = live_info.get("skin")
                                    if live_skin and live_skin.get("joints"):
                                        old_live_joints = list(live_skin["joints"])
                                        l_joints = [j for j in old_live_joints if cmds.objExists(j)]
                                        l_weights = np.asarray(live_skin["weights"], dtype=np.float64)
                                        if l_joints and l_weights.shape[0] > 0:
                                            l_old_indices = [old_live_joints.index(j) for j in l_joints]
                                            live_geo = live_info.get("geometry") or {}
                                            live_src_faces = _triangulate_indexed_faces(
                                                live_geo.get("face_indices") or [],
                                                live_geo.get("face_counts") or [],
                                            )
                                            live_vertices_value = live_geo.get("vertices")
                                            live_src_vertices = np.asarray(
                                                live_vertices_value if live_vertices_value is not None else [],
                                                dtype=np.float64,
                                            )
                                            if live_src_faces.shape[0] and live_src_vertices.shape[0]:
                                                live_new_w = _sample_weight_deformation_field(
                                                    live_src_vertices,
                                                    live_src_faces,
                                                    l_weights[:, l_old_indices],
                                                    l_joints,
                                                    live_query_verts,
                                                    dst_tri_faces_for_field,
                                                )
                                            else:
                                                live_new_w = _interpolate_field_directed(
                                                    num_new_verts,
                                                    l_weights[:, l_old_indices],
                                                    new_verts,
                                                    use_tnb,
                                                    matched_face_idx,
                                                    bary_coords,
                                                    src_faces if use_tnb else None,
                                                    src_verts,
                                                    nearest_indices,
                                                    w_gauss,
                                                )
                                            active_mask = active_mask & (live_new_w.sum(axis=1) > 1e-8)
                                            live_union_joints, live_union_w = compose_live_target_weights(
                                                src_joints,
                                                new_weights,
                                                l_joints,
                                                live_new_w,
                                                active_mask,
                                            )
                                            _bind_mesh_weights(
                                                live_dup,
                                                live_dup + "_skinCluster",
                                                live_union_joints,
                                                live_union_w,
                                            )

                                    def _map_live_field(old_field):
                                        if src_tri_faces_for_field.shape[0] and dst_tri_faces_for_field.shape[0]:
                                            outer_source_delta = np.asarray(old_delta, dtype=np.float64).reshape(-1, 3)
                                            inner_source_vertices = src_verts
                                            if outer_source_delta.shape[0] == src_verts.shape[0]:
                                                inner_source_vertices = src_verts + outer_source_delta
                                            return _sample_delta_deformation_field(
                                                inner_source_vertices,
                                                src_tri_faces_for_field,
                                                np.asarray(old_field, dtype=np.float64).reshape(-1, 3),
                                                live_query_verts,
                                                dst_tri_faces_for_field,
                                                str(tgt["name"]),
                                                source_weights=src_weights,
                                                source_joints=src_joints,
                                                target_weights=new_weights,
                                            )
                                        return _interpolate_field_directed(
                                            num_new_verts,
                                            old_field,
                                            new_verts,
                                            use_tnb,
                                            matched_face_idx,
                                            bary_coords,
                                            src_faces if use_tnb else None,
                                            src_verts,
                                            nearest_indices,
                                            w_gauss,
                                        )

                                    inner_count = _apply_live_inner_blendshapes_mapped(
                                        live_dup, live_info, _map_live_field, active_mask
                                    )

                                    iti_plug_live = iti_array_plug.elementByLogicalIndex(item_idx)
                                    for ci in range(iti_plug_live.numChildren()):
                                        child = iti_plug_live.child(ci)
                                        if om2.MFnAttribute(child.attribute()).name == "inputGeomTarget":
                                            cmds.connectAttr(
                                                f"{ld_shapes[0]}.worldMesh[0]",
                                                child.name(),
                                                force=True,
                                            )
                                            break
                                    bs_count += 1 + inner_count
                                    continue
                                except Exception as exc:
                                    logger.warning("Live BS directed 复刻失败 %s.%s: %s", new_mesh, tgt["name"], exc)

                            # 写入 delta 到 BS plug
                            norms_d = np.linalg.norm(new_delta, axis=1)
                            sparse_ids_arr = np.where(norms_d > 0.00001)[0]
                            if len(sparse_ids_arr) == 0:
                                continue

                            sparse_pts = om2.MPointArray()
                            for si in sparse_ids_arr:
                                d = new_delta[si]
                                sparse_pts.append(om2.MPoint(d[0], d[1], d[2]))

                            iti_plug_d = iti_array_plug.elementByLogicalIndex(item_idx)
                            ipt_plug = None
                            ict_plug = None
                            for ci in range(iti_plug_d.numChildren()):
                                child = iti_plug_d.child(ci)
                                attr_name = om2.MFnAttribute(child.attribute()).name
                                if attr_name == "inputPointsTarget":
                                    ipt_plug = child
                                elif attr_name == "inputComponentsTarget":
                                    ict_plug = child
                            if ipt_plug and ict_plug:
                                ipt_plug.setMObject(om2.MFnPointArrayData().create(sparse_pts))
                                fn_comp = om2.MFnSingleIndexedComponent()
                                comp_obj = fn_comp.create(om2.MFn.kMeshVertComponent)
                                fn_comp.addElements(sparse_ids_arr.tolist())
                                fn_comp_data = om2.MFnComponentListData()
                                comp_list_obj = fn_comp_data.create()
                                fn_comp_data.add(comp_obj)
                                ict_plug.setMObject(comp_list_obj)
                            bs_count += 1

                        # 设置权重值
                        attr_path = f"{new_bs}.{tgt['name']}"
                        if not cmds.objExists(attr_path):
                            attr_path = f"{new_bs}.weight[{t_idx}]"
                        try:
                            cmds.setAttr(attr_path, tgt["weight_value"])
                        except:
                            pass
                except Exception:
                    pass
            if bs_count > 0:
                items.append(make_item(target_name, f"  BS: {bs_count} targets transferred (GaussK3)"))

        return True

    except Exception as e:
        items.append(make_item(target_name, f"DIRECTED FAILED: {str(e)[:80]}, fallback to global"))
        try:
            skins = cmds.ls(cmds.listHistory(new_mesh) or [], type='skinCluster')
            if skins: cmds.delete(skins)
        except: pass
        return False

def _add_prefix_recursive(node, prefix=RIG_PREFIX):
    children = cmds.listRelatives(node, children=True, fullPath=True) or []
    for child in children:
        _add_prefix_recursive(child, prefix)
    short = node.split("|")[-1]
    if not short.startswith(prefix):
        cmds.rename(node, prefix + short)

def _find_orig_shape(rig_dag):
    transform = cmds.listRelatives(rig_dag, parent=True, fullPath=True)
    if not transform:
        return None
    shapes = cmds.listRelatives(transform[0], shapes=True, fullPath=True) or []
    for s in shapes:
        if cmds.getAttr(s + ".intermediateObject"):
            return s
    for s in shapes:
        if not cmds.getAttr(s + ".intermediateObject"):
            return s
    return None


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

from skills.maya_build_mesh_from_abc.maya_build_mesh_from_abc import create_mesh as _create_mesh_from_abc

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
    4. 若 inject_points 不为 None（ORIG_INJECT）：setPoints 到 ShapeOrig 注入新坐标
       （skin/BS 会按新 ShapeOrig 自动重算变形）

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

    # ORIG_INJECT：把正式资产坐标刷进该 transform 下所有 mesh shape（可见 shape 与 orig 都刷），
    # 用 kWorld 按世界坐标换算到各自局部，确保两者所有点坐标一模一样（用户 2026-06-30 拍板·R5）。
    # inject_points 是世界坐标（abc_reader 已乘 world_matrix）；点数一致才刷（ORIG_INJECT 保证点序点数同）。
    if inject_points is not None:
        arr = np.asarray(inject_points).reshape(-1, 3)
        pa = om2.MPointArray()
        for p in arr:
            pa.append(om2.MPoint(float(p[0]), float(p[1]), float(p[2])))
        for sh in cmds.listRelatives(transform, shapes=True, type="mesh", fullPath=True) or []:
            sel = om2.MSelectionList()
            sel.add(sh)
            fn_mesh = om2.MFnMesh(sel.getDagPath(0))
            if fn_mesh.numVertices == len(arr):
                fn_mesh.setPoints(pa, om2.MSpace.kWorld)

    return transform


def _inject_abc_uv(transform, tex_data):
    """把 ABC 的 UV(map1) 刷进 transform 下拓扑匹配的 mesh shape。

    ORIG_INJECT/IDENTICAL 复用旧绑定 mesh 时，顶点位置由 setPoints 注入，
    但 UV 不随 setPoints 变；资产只改了 UV（位置没变）时需在此显式注入，
    否则 rig 留旧 UV。UV 数据来自 tex_data（read_abc_as_info full 模式已读）。
    面数不一致（拓扑不符）的 shape 跳过，不硬写。
    """
    u_arr = tex_data.get("u_array", [])
    v_arr = tex_data.get("v_array", [])
    uv_ids = tex_data.get("uv_indices", [])
    fc = tex_data.get("face_counts", [])
    if not (u_arr and v_arr and uv_ids and fc):
        return False
    fc_int = om2.MIntArray(fc)
    uv_i_int = om2.MIntArray(uv_ids)
    u_f = om2.MFloatArray(u_arr)
    v_f = om2.MFloatArray(v_arr)
    wrote = False
    for sh in cmds.listRelatives(transform, shapes=True, type="mesh", fullPath=True) or []:
        sel = om2.MSelectionList()
        sel.add(sh)
        fn_mesh = om2.MFnMesh(sel.getDagPath(0))
        if fn_mesh.numPolygons != len(fc):
            continue
        fn_mesh.setUVs(u_f, v_f, "map1")
        fn_mesh.assignUVs(fc_int, uv_i_int, "map1")
        wrote = True
    return wrote


def _assign_materials_from_info(materials_info, all_mesh_nodes, tex_meshes_keys):
    """根据 Blender 采集的材质信息为 Maya mesh 创建 lambert 并按面分配。

    委托给独立技能 maya_apply_materials.apply_materials 执行。
    """
    from skills.maya_apply_materials.maya_apply_materials import apply_materials
    return apply_materials(materials_info, all_mesh_nodes)

def _plug_node(plug_name):
    """从 Maya plug 字符串中取节点名。"""
    return str(plug_name or "").split(".", 1)[0]


def _long_node_name(node):
    matches = cmds.ls(node, long=True) or []
    return matches[0] if matches else str(node or "")


def _long_mesh_shape_set(shapes):
    result = set()
    for shape in shapes or []:
        result.update(cmds.ls(shape, long=True, type="mesh") or [])
    return result


def _blendshape_reaches_target_shapes(bs_node, target_shapes, max_depth=12):
    """只接受 output 链最终落到当前 mesh shape 的 BS，避免误采 target 上游 BS。"""
    target_shape_set = _long_mesh_shape_set(target_shapes)
    if not target_shape_set:
        return False

    start_plugs = cmds.listConnections(
        f"{bs_node}.outputGeometry",
        source=False,
        destination=True,
        plugs=True,
    ) or []
    queue = [(_plug_node(plug), 0) for plug in start_plugs]
    visited = set()

    while queue:
        node, depth = queue.pop(0)
        if not node or node in visited or depth > max_depth:
            continue
        visited.add(node)

        try:
            node_type = cmds.nodeType(node)
        except Exception:
            continue

        if node_type == "mesh":
            if _long_node_name(node) in target_shape_set:
                return True
            continue

        next_plugs = cmds.listConnections(
            node,
            source=False,
            destination=True,
            plugs=True,
        ) or []
        for plug in next_plugs:
            next_node = _plug_node(plug)
            if next_node and next_node not in visited:
                queue.append((next_node, depth + 1))

    return False


def _extract_blendshape_data(rig_dag):
    """提取直接作用在旧 RIG mesh 上的 BlendShape 数据（含 in-between、Live-Link 蒙皮）。"""
    import numpy as np
    transform = cmds.listRelatives(rig_dag, parent=True, fullPath=True)
    if not transform:
        return []
    all_shapes = cmds.listRelatives(transform[0], shapes=True, fullPath=True) or []
    
    # 收集所有 BS 节点（不再只取第一个）
    all_bs_nodes = set()
    for shape in all_shapes:
        try:
            history = cmds.listHistory(shape) or []
            found = cmds.ls(history, type="blendShape")
            all_bs_nodes.update(found)
        except:
            pass
    if not all_bs_nodes:
        return []
    
    # 获取 base shape 顶点数
    orig_shape = _find_orig_shape(rig_dag)
    base_shape = orig_shape if orig_shape else rig_dag
    sel_base = om2.MSelectionList()
    sel_base.add(base_shape)
    fn_base = om2.MFnMesh(sel_base.getDagPath(0))
    num_base = fn_base.numVertices
    base_pts = fn_base.getPoints(om2.MSpace.kObject)
    
    results = []
    for bs_node in sorted(all_bs_nodes):
        if not _blendshape_reaches_target_shapes(bs_node, all_shapes):
            continue

        sel_bs = om2.MSelectionList()
        sel_bs.add(bs_node)
        fn_bs = om2.MFnDependencyNode(sel_bs.getDependNode(0))
        
        it_plug = fn_bs.findPlug("inputTarget", False)
        geom_indices = it_plug.getExistingArrayAttributeIndices()
        if not geom_indices:
            continue
        geom_idx = geom_indices[0]
        itg_plug = it_plug.elementByLogicalIndex(geom_idx).child(0)
        
        # 构建 weight[idx] -> alias_name 映射
        target_indices = itg_plug.getExistingArrayAttributeIndices()
        alias_list = cmds.aliasAttr(bs_node, query=True) or []
        idx_to_name = {}
        for i in range(0, len(alias_list), 2):
            attr_ref = alias_list[i+1]
            try:
                wi = int(attr_ref.split("[")[1].rstrip("]"))
                idx_to_name[wi] = alias_list[i]
            except Exception as exc:
                logger.debug("BlendShape alias 解析跳过 %s.%s: %s", bs_node, attr_ref, exc)
        
        targets = []
        for t_idx in target_indices:
            t_name = idx_to_name.get(t_idx, f"target_{t_idx}")
            
            attr_path = f"{bs_node}.{t_name}"
            if not cmds.objExists(attr_path):
                attr_path = f"{bs_node}.weight[{t_idx}]"
            weight_val = 0.0
            try:
                weight_val = cmds.getAttr(attr_path)
            except Exception as exc:
                logger.debug("BlendShape 权重读取失败 %s: %s", attr_path, exc)
            in_conns = cmds.listConnections(attr_path, source=True, destination=False, plugs=True) or []
            
            tgt_plug = itg_plug.elementByLogicalIndex(t_idx)
            iti_array_plug = tgt_plug.child(0)
            item_indices = iti_array_plug.getExistingArrayAttributeIndices()
            
            items_data = []
            for item_idx in item_indices:
                iti_plug = iti_array_plug.elementByLogicalIndex(item_idx)
                
                ipt_plug = None
                ict_plug = None
                for ci in range(iti_plug.numChildren()):
                    child = iti_plug.child(ci)
                    attr_name = om2.MFnAttribute(child.attribute()).name
                    if attr_name == "inputPointsTarget":
                        ipt_plug = child
                    elif attr_name == "inputComponentsTarget":
                        ict_plug = child
                
                if not ipt_plug or not ict_plug:
                    continue
                
                sparse_delta = np.zeros((num_base, 3), dtype=np.float64)
                has_data = False
                live_info = None  # Live Target 信息
                try:
                    pts_data = ipt_plug.asMDataHandle().data()
                    if not pts_data.isNull():
                        pts = om2.MFnPointArrayData(pts_data).array()
                        ids = []
                        try:
                            comp_data = ict_plug.asMDataHandle().data()
                            if not comp_data.isNull():
                                fn_comp_list = om2.MFnComponentListData(comp_data)
                                for ci in range(fn_comp_list.length()):
                                    fn_si = om2.MFnSingleIndexedComponent(fn_comp_list.get(ci))
                                    ids.extend(fn_si.getElements())
                        except Exception as exc:
                            logger.warning("BlendShape component target 解析失败 %s.%s[%s]: %s", bs_node, t_name, item_idx, exc)
                        
                        if ids and len(ids) == len(pts):
                            for i, vid in enumerate(ids):
                                if vid < num_base:
                                    sparse_delta[vid] = [pts[i].x, pts[i].y, pts[i].z]
                            has_data = True
                        elif len(pts) == num_base:
                            for i in range(num_base):
                                sparse_delta[i] = [pts[i].x, pts[i].y, pts[i].z]
                            has_data = True
                        elif len(pts) > 0 and not ids:
                            for i in range(min(len(pts), num_base)):
                                sparse_delta[i] = [pts[i].x, pts[i].y, pts[i].z]
                            has_data = np.any(np.abs(sparse_delta) > 1e-7)
                except Exception as exc:
                    logger.warning("BlendShape static target 提取失败 %s.%s[%s]: %s", bs_node, t_name, item_idx, exc)
                
                # Live Target 检测与深度信息采集。
                # 即使 Maya 同时写入了 inputPointsTarget，也必须保留 inputGeomTarget 连接，
                # 否则 live target 自身的 skinCluster/驱动链会被误降级成静态 delta。
                try:
                    igt_plug = None
                    for ci in range(iti_plug.numChildren()):
                        child = iti_plug.child(ci)
                        attr_name = om2.MFnAttribute(child.attribute()).name
                        if attr_name == "inputGeomTarget":
                            igt_plug = child
                            break

                    if igt_plug and igt_plug.isConnected:
                        conns = cmds.listConnections(igt_plug.name(), source=True, destination=False, shapes=True) or []
                        if conns:
                            live_shape = conns[0]
                            sel_live = om2.MSelectionList()
                            sel_live.add(live_shape)
                            fn_live = om2.MFnMesh(sel_live.getDagPath(0))
                            live_pts = fn_live.getPoints(om2.MSpace.kObject)
                            live_counts, live_indices = fn_live.getVertices()
                            live_vertices = np.asarray(
                                [[p.x, p.y, p.z] for p in live_pts],
                                dtype=np.float64,
                            )

                            live_delta = np.zeros((num_base, 3), dtype=np.float64)
                            for vi in range(min(num_base, len(live_pts))):
                                live_delta[vi] = [
                                    live_pts[vi].x - base_pts[vi].x,
                                    live_pts[vi].y - base_pts[vi].y,
                                    live_pts[vi].z - base_pts[vi].z
                                ]
                            sparse_delta = live_delta
                            has_data = np.any(np.abs(sparse_delta) > 1e-7)

                            # 采集 Live Mesh 的蒙皮数据（深度复刻用）
                            live_tr = cmds.listRelatives(live_shape, parent=True, fullPath=True)
                            live_skin_info = None
                            inner_blendshapes = []
                            if live_tr:
                                lsk, lsh = _find_skin_cluster(live_tr[0])
                                if lsk:
                                    try:
                                        lj = cmds.skinCluster(lsk, query=True, influence=True)
                                        sel_lsk = om2.MSelectionList()
                                        sel_lsk.add(lsk)
                                        fn_lsk = oma2.MFnSkinCluster(sel_lsk.getDependNode(0))
                                        sel_lsh = om2.MSelectionList()
                                        sel_lsh.add(lsh)
                                        ld = sel_lsh.getDagPath(0)
                                        lnv = om2.MFnMesh(ld).numVertices
                                        lcomp = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
                                        om2.MFnSingleIndexedComponent(lcomp).setCompleteData(lnv)
                                        lw, _ = fn_lsk.getWeights(ld, lcomp)
                                        live_skin_info = {
                                            "joints": lj,
                                            "weights": np.array(lw).reshape(lnv, len(lj))
                                        }
                                    except Exception as exc:
                                        logger.warning("Live BS skin 提取失败 %s: %s", live_tr[0], exc)
                                inner_blendshapes = _extract_live_inner_blendshapes(
                                    live_tr[0], rig_dag, attr_path
                                )
                            live_info = {
                                "transform": live_tr[0] if live_tr else None,
                                "shape": live_shape,
                                "skin": live_skin_info,
                                "inner_blendshapes": inner_blendshapes,
                                "geometry": {
                                    "vertices": live_vertices,
                                    "face_counts": list(live_counts),
                                    "face_indices": list(live_indices),
                                },
                            }
                except Exception as exc:
                    logger.warning("Live BS target 提取失败 %s.%s[%s]: %s", bs_node, t_name, item_idx, exc)
                
                if not has_data:
                    continue
                
                items_data.append({
                    "item_index": item_idx,
                    "delta": sparse_delta,
                    "live_info": live_info,
                })
            
            if items_data:
                targets.append({
                    "name": t_name,
                    "index": t_idx,
                    "weight_value": weight_val,
                    "items": items_data,
                    "upstream_plugs": in_conns,
                })
        
        if targets:
            results.append({
                "bs_node": bs_node,
                "num_base": num_base,
                "targets": targets,
            })
    
    return results


def _chamfer_distance(verts_a, verts_b, sample_max=500):
    """双向平均最近点距离，用于 mesh 配对评分。采样加速避免大 mesh 卡顿。"""
    import numpy as np
    if len(verts_a) > sample_max:
        idx = np.linspace(0, len(verts_a)-1, sample_max, dtype=int)
        verts_a = verts_a[idx]
    if len(verts_b) > sample_max:
        idx = np.linspace(0, len(verts_b)-1, sample_max, dtype=int)
        verts_b = verts_b[idx]
    # A→B
    diff_ab = verts_a[:, None, :] - verts_b[None, :, :]
    d_ab = np.sqrt(np.min(np.sum(diff_ab * diff_ab, axis=2), axis=1))
    # B→A
    diff_ba = verts_b[:, None, :] - verts_a[None, :, :]
    d_ba = np.sqrt(np.min(np.sum(diff_ba * diff_ba, axis=2), axis=1))
    return (d_ab.mean() + d_ba.mean()) / 2.0


def _bbox_iou_3d(verts_a, verts_b):
    """3D BBox IoU，用于快速过滤不可能配对的 mesh。"""
    import numpy as np
    min_a, max_a = verts_a.min(axis=0), verts_a.max(axis=0)
    min_b, max_b = verts_b.min(axis=0), verts_b.max(axis=0)
    inter_min = np.maximum(min_a, min_b)
    inter_max = np.minimum(max_a, max_b)
    inter_size = np.maximum(inter_max - inter_min, 0)
    inter_vol = inter_size[0] * inter_size[1] * inter_size[2]
    size_a = max_a - min_a
    size_b = max_b - min_b
    vol_a = size_a[0] * size_a[1] * size_a[2]
    vol_b = size_b[0] * size_b[1] * size_b[2]
    union_vol = vol_a + vol_b - inter_vol
    if union_vol < 1e-12:
        return 0.0
    return inter_vol / union_vol


def _auto_pair_by_chamfer(voting_pool_tex, rig_meshes, rig_skin_data, reused_rig_dags, profile=None):
    """
    对 voting pool 中没有 _paired_rig_dag 的 mesh，用 Chamfer Distance 自动配对最佳源。
    只配对 BBox IoU > bbox_iou_min 且 Chamfer Distance 最小的源。
    阈值通过 profile 覆盖：chamfer_threshold / bbox_iou_min。
    """
    import numpy as np
    pairing_cfg = (profile or {}).get("pairing", {})
    CHAMFER_THRESHOLD = pairing_cfg.get("chamfer_threshold", 5.0)
    BBOX_IOU_MIN = pairing_cfg.get("bbox_iou_min", 0.01)

    available_rigs = {}
    for rig_dag, rig_data in rig_meshes.items():
        if rig_dag in reused_rig_dags:
            continue
        if rig_dag not in rig_skin_data:
            continue
        vp = rig_data.get("vert_positions", [])
        if not vp:
            continue
        available_rigs[rig_dag] = np.array(vp, dtype=np.float64).reshape(-1, 3)

    if not available_rigs:
        return {}

    pairings = {}
    for dag_tex, tex_data in voting_pool_tex.items():
        if tex_data.get("_paired_rig_dag"):
            continue
        vp = tex_data.get("vert_positions", [])
        if not vp:
            continue
        tex_verts = np.array(vp, dtype=np.float64).reshape(-1, 3)

        best_dag = None
        best_cd = CHAMFER_THRESHOLD

        for rig_dag, rig_verts in available_rigs.items():
            iou = _bbox_iou_3d(tex_verts, rig_verts)
            if iou < BBOX_IOU_MIN:
                continue
            cd = _chamfer_distance(tex_verts, rig_verts)
            if cd < best_cd:
                best_cd = cd
                best_dag = rig_dag

        if best_dag:
            pairings[dag_tex] = best_dag

    return pairings


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
            recovery_hint="重新运行前置对比节点，确保 compare_result.v1 由当前版本 compare skill 生成。",
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
        # ── 幂等保护：已跑过直接拒绝 ──
        already_done, done_reason = _check_sync_already_done(cache_group)
        if already_done:
            cmds.undoInfo(closeChunk=True)
            return _sync_receipt(
                "ERROR", t0, "preflight",
                error=f"场景似乎已被 sync 处理过，请从原始 rig 场景重新开始。原因：{done_reason}",
                items=items,
                recovery_hint="使用沙盒内原始 rig 副本重新执行 workflow；不要在已同步场景上重复运行 sync。",
            )

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
        # 采不到 ShapeOrig 的件（未绑定 / origin 坏；二者等价：都无权重可传）。
        # 几何-only 模式（transfer_weights=False）不再整步回滚，改判为"从 tex 重建、不绑定"
        # （Phase 3 分发处消费 unbound_rig_dags 完成改判）。
        # 绑定模式仍依赖有效 origin 做权重/BS 传递，维持原审计闸，不在本次放行范围。
        geom_only = not bool(profile.get("transfer_weights", True))
        unbound_rig_dags = set()
        if empty_rig_dags:
            if geom_only:
                unbound_rig_dags = set(empty_rig_dags)
                items.append(make_item(
                    "target_collect",
                    f"{len(empty_rig_dags)} 个 rig 件采不到 ShapeOrig（未绑定/origin 坏）→ 改判从 tex 重建；"
                    f"样例: {[d.split('|')[-1] for d in empty_rig_dags[:8]]}"
                ))
            else:
                cmds.undoInfo(closeChunk=True)
                cmds.undo()
                return _sync_receipt(
                    "AUDIT_FAILED", t0, "target_collect",
                    error=(
                        "target rig 中存在无法采集 ShapeOrig 几何的 mesh，已撤销本步。"
                        f"问题节点: {empty_rig_dags[:20]}"
                    ),
                    items=items,
                    recovery_hint="先运行 Shape/Orig 清理 workflow 或 maya_fix_shape_names，再重新生成 compare_result 后执行 sync。",
                )

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


        # ── Phase 2: 提取旧 RIG 骨骼与权重（geom_only 跳过：纯对比模式不传权重，提取是白工）──
        _skin_src = {} if geom_only else rig_meshes
        _plog(f"Phase2a: extract skin data for {len(_skin_src)} rig meshes (geom_only={geom_only})")
        rig_skin_data = {}
        for i, (rig_dag, rig_data) in enumerate(_skin_src.items()):
            if i % 10 == 0:
                _plog(f"  Phase2a progress: {i}/{len(rig_meshes)}")
            skin_node, src_shape = _find_skin_cluster(rig_dag)
            if skin_node:
                try:
                    joints = cmds.skinCluster(skin_node, query=True, influence=True)
                    sel_skin = om2.MSelectionList()
                    sel_skin.add(skin_node)
                    fn_skin = oma2.MFnSkinCluster(sel_skin.getDependNode(0))
                    
                    sel_shape = om2.MSelectionList()
                    sel_shape.add(src_shape)
                    dag_shape = sel_shape.getDagPath(0)
                    
                    num_verts = om2.MFnMesh(dag_shape).numVertices
                    comp = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
                    om2.MFnSingleIndexedComponent(comp).setCompleteData(num_verts)
                    
                    weights, _ = fn_skin.getWeights(dag_shape, comp)
                    wt_arr = np.array(weights).reshape(num_verts, len(joints))
                    
                    rig_skin_data[rig_dag] = {
                        "joints": joints,
                        "weights": wt_arr
                    }
                except Exception as e:
                    msg = f"旧绑定权重读取失败: {rig_dag} ({e})"
                    _plog(msg)
                    items.append(make_item(rig_dag.split("|")[-1] or rig_dag, msg[:200]))

        # ── Phase 2b: 提取旧 RIG BlendShape 数据（geom_only 跳过：纯对比模式不复刻 BS）──
        _bs_src = [] if geom_only else list(rig_meshes.keys())
        _plog(f"Phase2b: extract BS data for {len(_bs_src)} rig meshes (geom_only={geom_only})")
        rig_bs_data = {}
        for i, rig_dag in enumerate(_bs_src):
            if i % 10 == 0:
                _plog(f"  Phase2b progress: {i}/{len(rig_meshes)}")
            bs_infos = _extract_blendshape_data(rig_dag)
            if bs_infos:
                rig_bs_data[rig_dag] = bs_infos
        _plog(f"Phase2b done: {len(rig_bs_data)} meshes have BS data")

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
        # ORIG_INJECT  → 同搬运 + setPoints 注入 abc 坐标（1 个独立 layer）
        # PAIRED (M→N) → 每个 abc 进 voting pool，挂 _pairing_group_id + _pairing_rig_candidates
        #                Phase 4 投射权重后，新 mesh 和组内 rig 共同进 1 个 layer
        # UNPAIRED     → 进 voting pool，无 rig 源；Phase 4 走 Chamfer 自动配对；全部进 _source_only
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

                inject_pts = None
                if action == "ORIG_INJECT":
                    rig_data = rig_meshes[rig_full]
                    v_new = tex_data.get("vert_positions", [])
                    num_v_new = tex_data.get("vertices", 0)
                    if num_v_new > 0 and num_v_new == rig_data.get("vertices", 0):
                        inject_pts = np.array(v_new, dtype=np.float64).reshape(-1, 3)
                    else:
                        voting_pool_tex[abc_dag] = tex_data
                        items.append(make_item(
                            target_name,
                            f"降级 voting pool：ORIG_INJECT 点数不符 "
                            f"(tex={num_v_new} rig={rig_data.get('vertices', 0)})"
                        ))
                        continue

                new_transform = _relocate_rig_mesh(
                    rig_full, target_name, parent_path, rig_prefix, inject_points=inject_pts
                )
                if new_transform:
                    reused_rig_dags.add(rig_full)
                    uv_done = _inject_abc_uv(new_transform, tex_data)
                    items.append(make_item(target_name, f"{action}: 搬运 + 改名" + (" + 注 UV" if uv_done else "")))
                    # IDENTICAL 与 ORIG_INJECT 都不建 layer：几何一致/宽松一致，仅做层级与命名修复，
                    # 绑定师整体校验时再由后续 QC 技能统一标注，避免 outliner 被单物体 layer 撑满。
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


        # ── Tier 2: 构建全局 Super Mesh (GaussK3 空间包裹制) ──
        _plog(f"Tier2: build Super Mesh (reused={len(reused_rig_dags)}, pool={len(voting_pool_tex)})")
        super_verts = []
        vert_to_rig_map = []
        rig_vert_offsets = {}
        vert_offset = 0

        for rig_dag, rig_data in (({} if geom_only else rig_meshes).items()):  # geom_only 跳过：不投射权重，无需 SuperMesh
            if rig_dag in reused_rig_dags:
                continue
            verts = np.array(rig_data["vert_positions"]).reshape(-1, 3)
            num_v = len(verts)

            rig_vert_offsets[rig_dag] = vert_offset
            vert_to_rig_map.extend([rig_dag] * num_v)

            super_verts.append(verts)
            vert_offset += num_v

        super_tree = None
        if super_verts:
            from scipy.spatial import cKDTree
            sv_arr = np.vstack(super_verts)
            vert_to_rig_map = np.array(vert_to_rig_map)
            super_tree = cKDTree(sv_arr)
            items.append(make_item("SuperWrap 初始化", f"构建全局 KDTree: {len(sv_arr)} 个源顶点参与融合包裹"))

        # ── Phase 3.5: Chamfer Distance 自动配对（geom_only 跳过：不借权重，无需配对）──
        auto_pairings = {} if geom_only else _auto_pair_by_chamfer(voting_pool_tex, rig_meshes, rig_skin_data, reused_rig_dags, profile=profile)
        for dag_tex, best_rig_dag in auto_pairings.items():
            voting_pool_tex[dag_tex]["_paired_rig_dag"] = best_rig_dag
        if auto_pairings:
            items.append(make_item("Chamfer 自动配对", f"为 {len(auto_pairings)} 个 mesh 找到定向投射源"))

        # ── Phase 4: 空间包裹与新节点创建 ──
        transfer_weights = bool(profile.get("transfer_weights", True))
        _plog(f"Phase4: create {len(voting_pool_tex)} new meshes (transfer_weights={transfer_weights})")
        sync_nodes = []
        new_nodes = []

        for _pi, (dag_tex, tex_data) in enumerate(voting_pool_tex.items()):
            _plog(f"  Phase4 mesh {_pi+1}/{len(voting_pool_tex)}: {dag_tex.split('|')[-1]}")
            target_name, group_parts = _get_target_name_and_parent(dag_tex)
            parent_path = _ensure_hierarchy(group_parts)

            exist_path = f"{parent_path}|{target_name}" if parent_path else target_name
            if cmds.objExists(exist_path):
                try: cmds.delete(exist_path)
                except: pass

            new_mesh, build_warnings = _create_mesh_from_abc(target_name, tex_data, parent_path)
            if not new_mesh:
                continue
            if build_warnings:
                items.append(make_item(target_name, "WARN: " + "; ".join(build_warnings)[:200]))

            # 按组归属登记新 mesh（PAIRED 进组 layer；UNPAIRED 进 _source_only）
            _gid = tex_data.get("_pairing_group_id")
            if _gid and _gid in group_records:
                group_records[_gid]["abc_nodes"].append(new_mesh)
            elif tex_data.get("_unpaired"):
                unpaired_nodes.append(new_mesh)

            # 没匹配（UNPAIRED）的 mesh 不自动借权重/绑定：输出干净几何交绑定师手绑（用户 2026-06-30 拍板·R2）。
            # 放在权重投射前拦截，使 Chamfer 给它的 _paired_rig_dag 与 Super-Wrap 都不生效。
            if tex_data.get("_unpaired"):
                new_nodes.append(new_mesh)
                items.append(make_item(target_name, "UNPAIRED: 干净几何，不自动绑定（交绑定师手绑）"))
                continue

            # 按配置跳过权重/BS 复刻：只搭几何，网格未绑定，交给绑定师手绑
            if not transfer_weights:
                new_nodes.append(new_mesh)
                items.append(make_item(target_name, "GEOM ONLY: 跳过权重/BS 复刻（transfer_weights=False），网格未绑定"))
                continue

            paired_rig_dag = tex_data.get("_paired_rig_dag")

            if super_tree is None and not paired_rig_dag:
                new_nodes.append(new_mesh)
                items.append(make_item(target_name, "NEW (无旧场景)"))
                continue

            new_verts = np.array(tex_data["vert_positions"]).reshape(-1, 3)
            num_new_verts = len(new_verts)

            # ── 定向投射 ──
            if paired_rig_dag and paired_rig_dag in rig_skin_data and paired_rig_dag in rig_meshes:
                _ok = _directed_weight_transfer(
                    new_mesh, new_verts, num_new_verts, None, tex_data,
                    paired_rig_dag, rig_meshes, rig_skin_data, rig_bs_data,
                    target_name, items, sync_nodes, new_nodes, profile=profile
                )
                if _ok: continue

            if super_tree is None:
                new_nodes.append(new_mesh)
                items.append(make_item(target_name, "NEW (无投射源)"))
                continue
            
            # --- 全局 GaussK 空间包裹 (Super Wrap) ---
            _sw_k = profile.get("diffuse", {}).get("fallback_k", 3)
            _sw_k = min(_sw_k, super_tree.data.shape[0])
            kd_dists, kd_indices = super_tree.query(new_verts, k=_sw_k)
            if kd_indices.ndim == 1:
                kd_indices = kd_indices.reshape(-1, 1)
                kd_dists = kd_dists.reshape(-1, 1)

            sigma = profile.get("diffuse", {}).get("fallback_sigma", 0.05)
            w_gauss = np.exp(-(kd_dists**2) / (2 * sigma**2))
            row_sums = w_gauss.sum(axis=1, keepdims=True)
            row_sums[row_sums == 0] = 1.0
            w_gauss /= row_sums
            
            # 找到涉及的所有的 Rig Dags
            hit_dags = vert_to_rig_map[kd_indices] # shape: (N, 3)
            unique_dags = np.unique(hit_dags)
            
            union_joints_set = set()
            for dag in unique_dags:
                if dag in rig_skin_data:
                    union_joints_set.update(rig_skin_data[dag]["joints"])
                    
            if not union_joints_set:
                new_nodes.append(new_mesh)
                items.append(make_item(target_name, "NEW (源无绑定)"))
                continue
                
            union_joints = sorted(list(union_joints_set))
            joint_idx_map = {j: i for i, j in enumerate(union_joints)}
            num_union = len(union_joints)
            
            # 向量化混合权重
            new_weights = np.zeros((num_new_verts, num_union), dtype=np.float64)
            for src_dag in unique_dags:
                if src_dag not in rig_skin_data:
                    continue
                sk_data = rig_skin_data[src_dag]
                v_offset = rig_vert_offsets[src_dag]
                src_joints = sk_data["joints"]
                src_weights = sk_data["weights"]
                
                src_to_union = np.array([joint_idx_map[j] for j in src_joints], dtype=np.int32)
                
                # kd_indices 中属于当前源的掩码
                dag_mask = (hit_dags == src_dag)
                i_idx, k_idx = np.where(dag_mask)
                
                if len(i_idx) == 0:
                    continue
                    
                local_v = kd_indices[i_idx, k_idx] - v_offset
                w_val = w_gauss[i_idx, k_idx]
                
                s_w = src_weights[local_v] * w_val[:, None]
                
                for src_j in range(len(src_joints)):
                    np.add.at(new_weights[:, src_to_union[src_j]], i_idx, s_w[:, src_j])

            # Laplacian 兜底平滑 (针对离源资产很远的点)
            valid_mask = kd_dists[:, 0] <= sigma
            valid_idx = np.where(valid_mask)[0]
            if 0 < len(valid_idx) < num_new_verts:
                from core.laplacian_diffuse import laplacian_diffuse
                adjacency = _build_adjacency(num_new_verts, tex_data.get("face_indices", []), tex_data.get("face_counts", []))
                new_weights = laplacian_diffuse(adjacency, valid_idx, new_weights[valid_idx], num_new_verts, num_union)
            
            # Normalize
            row_sums = new_weights.sum(axis=1, keepdims=True)
            row_sums[row_sums == 0] = 1.0
            new_weights = new_weights / row_sums
            
            # -------------------------------------------------------------
            # [终极防御] 拓扑保边平滑 (Topological Laplacian Smoothing)
            # 解决"多根头发合体导致相邻顶点被投射到不同骨骼从而产生蜘蛛网撕裂"的致命痛点
            # -------------------------------------------------------------
            logger.info("Applying Topological Laplacian Smoothing to prevent tearing...")
            adj_list = []
            try:
                sel_sm = om2.MSelectionList(); sel_sm.add(new_mesh)
                itv = om2.MItMeshVertex(sel_sm.getDagPath(0))
                while not itv.isDone():
                    adj_list.append(list(itv.getConnectedVertices()))
                    itv.next()
                
                # 30轮局部平滑，信息传播30个拓扑距离，足以熨平一切撕裂爆点
                new_weights = _topological_smooth(num_new_verts, adj_list, new_weights, iterations=30, blend=0.5)
                    
                # -------------------------------------------------------------
                # [引擎防线] 权重净化 (Prune Micro-Weights)
                # 防止平滑造成的墨水晕染，剔除微小噪点，避免突破最大影响骨骼数
                # -------------------------------------------------------------
                new_weights[new_weights < 0.01] = 0.0
                
                # 再次 Normalize
                row_sums = new_weights.sum(axis=1, keepdims=True)
                row_sums[row_sums == 0] = 1.0
                new_weights = new_weights / row_sums
            except Exception as e:
                logger.warning(f"Failed to apply topological smoothing: {e}")
            
            # 写入 SkinCluster
            active_cols = np.where(new_weights.max(axis=0) > 0.0)[0]
            if len(active_cols) > 0:
                bind_joints = [union_joints[c] for c in active_cols]
                compact_w = new_weights[:, active_cols]
                
                try:
                    new_skin = cmds.skinCluster(
                        new_mesh, bind_joints, toSelectedBones=True,
                        bindMethod=0, skinMethod=0, normalizeWeights=1,
                        name=target_name + "_skinCluster")[0]
                        
                    sel_ns = om2.MSelectionList()
                    sel_ns.add(new_skin)
                    fn_ns = oma2.MFnSkinCluster(sel_ns.getDependNode(0))
                    
                    new_shapes = cmds.listRelatives(new_mesh, shapes=True, fullPath=True, noIntermediate=True)
                    sel_nsh = om2.MSelectionList()
                    sel_nsh.add(new_shapes[0])
                    nd = sel_nsh.getDagPath(0)
                    
                    nnv = om2.MFnMesh(nd).numVertices
                    nc = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
                    om2.MFnSingleIndexedComponent(nc).setCompleteData(nnv)
                    
                    inf_idx = om2.MIntArray(list(range(len(bind_joints))))
                    fn_ns.setWeights(nd, nc, inf_idx, om2.MDoubleArray(compact_w.flatten().tolist()))
                    
                    sync_nodes.append(new_mesh)
                    items.append(make_item(target_name, f"GLOBAL WRAP: 高斯空间融合 ({len(bind_joints)} joints)"))

                    # ── BlendShape Delta Super Wrap ──
                    bs_transferred = 0
                    for src_dag in unique_dags:
                        if src_dag not in rig_bs_data:
                            continue
                        bs_info_list = rig_bs_data[src_dag]
                        v_offset = rig_vert_offsets.get(src_dag, 0)
                        
                        for bs_info in bs_info_list:
                            if not bs_info["targets"]:
                                continue
                            
                            old_bs_name = bs_info.get("bs_node", "blendShape")
                            new_bs_suffix = old_bs_name.replace(RIG_PREFIX, "")
                            
                            try:
                                new_bs = cmds.blendShape(new_mesh,
                                                         name=f"{target_name}_{new_bs_suffix}",
                                                         frontOfChain=True)[0]
                                
                                sel_bs = om2.MSelectionList()
                                sel_bs.add(new_bs)
                                fn_bs = om2.MFnDependencyNode(sel_bs.getDependNode(0))
                                it_plug = fn_bs.findPlug("inputTarget", False)
                                geom_indices = it_plug.getExistingArrayAttributeIndices()
                                geom_idx = geom_indices[0] if geom_indices else 0
                                itg_plug = it_plug.elementByLogicalIndex(geom_idx).child(0)
                                
                                for tgt in bs_info["targets"]:
                                    t_idx = tgt["index"]
                                    w_plug = fn_bs.findPlug("weight", False)
                                    w_plug.elementByLogicalIndex(t_idx)
                                    try: cmds.aliasAttr(tgt["name"], f"{new_bs}.weight[{t_idx}]")
                                    except: pass
                                    
                                    tgt_plug = itg_plug.elementByLogicalIndex(t_idx)
                                    iti_array_plug = tgt_plug.child(0)
                                    
                                    for item_data in tgt["items"]:
                                        item_idx = item_data["item_index"]
                                        old_delta = item_data["delta"]
                                        num_old_delta = len(old_delta)
                                        live_info = item_data.get("live_info")
                                        
                                        # 提取 Delta 并混合
                                        new_delta = np.zeros((num_new_verts, 3), dtype=np.float64)
                                        dag_mask = (hit_dags == src_dag)
                                        i_idx, k_idx = np.where(dag_mask)
                                        if len(i_idx) > 0:
                                            local_v = kd_indices[i_idx, k_idx] - v_offset
                                            valid_v = (local_v >= 0) & (local_v < num_old_delta)
                                            if np.any(valid_v):
                                                i_ok = i_idx[valid_v]
                                                k_ok = k_idx[valid_v]
                                                l_ok = local_v[valid_v]
                                                w_val = w_gauss[i_ok, k_ok]
                                                
                                                d_val = old_delta[l_ok] * w_val[:, None]
                                                np.add.at(new_delta, i_ok, d_val)
                                        live_delta_raw = new_delta.copy()
                                        
                                        # -------------------------------------------------------------
                                        # [终极防御] 拓扑保边平滑 (BS Delta 同频平滑)
                                        # -------------------------------------------------------------
                                        if len(adj_list) > 0:
                                            new_delta = _topological_smooth(num_new_verts, adj_list, new_delta, iterations=30, blend=0.5)
                                        
                                        # ── Live Target 深度复刻 ──
                                        if live_info and live_info.get("transform"):
                                            try:
                                                active_mask = np.linalg.norm(live_delta_raw, axis=1) > 1e-7
                                                live_dup = _duplicate_clean_mesh(new_mesh, f"{target_name}_live_{t_idx}")
                                                ld_shapes = cmds.listRelatives(live_dup, shapes=True, fullPath=True, noIntermediate=True)
                                                if not ld_shapes:
                                                    raise RuntimeError("live duplicate 缺少可见 shape")
                                                sel_ld = om2.MSelectionList()
                                                sel_ld.add(ld_shapes[0])
                                                fn_ld = om2.MFnMesh(sel_ld.getDagPath(0))
                                                base_live_pts = fn_ld.getPoints(om2.MSpace.kObject)
                                                for vi in range(min(num_new_verts, len(base_live_pts))):
                                                    base_live_pts[vi].x += live_delta_raw[vi][0]
                                                    base_live_pts[vi].y += live_delta_raw[vi][1]
                                                    base_live_pts[vi].z += live_delta_raw[vi][2]
                                                fn_ld.setPoints(base_live_pts, om2.MSpace.kObject)
                                                
                                                # 如果旧活体有蒙皮，active 区域清空 body 权重后写入 live 权重。
                                                live_skin = live_info.get("skin")
                                                if live_skin and live_skin.get("joints"):
                                                    old_live_joints = list(live_skin["joints"])
                                                    l_joints = [j for j in old_live_joints if cmds.objExists(j)]
                                                    l_weights = np.asarray(live_skin["weights"], dtype=np.float64)
                                                    if l_joints and l_weights.shape[0] > 0:
                                                        l_joint_idx = {j: i for i, j in enumerate(old_live_joints)}
                                                        live_new_w = np.zeros((num_new_verts, len(l_joints)), dtype=np.float64)
                                                        for li, lj in enumerate(l_joints):
                                                            oi = l_joint_idx.get(lj, -1)
                                                            if oi < 0: continue
                                                            if len(i_idx) > 0:
                                                                valid_lv = (local_v >= 0) & (local_v < l_weights.shape[0])
                                                                if np.any(valid_lv):
                                                                    i_ok = i_idx[valid_lv]
                                                                    k_ok = k_idx[valid_lv]
                                                                    l_ok = local_v[valid_lv]
                                                                    w_v = w_gauss[i_ok, k_ok]
                                                                    sw_v = l_weights[l_ok, oi] * w_v
                                                                    np.add.at(live_new_w[:, li], i_ok, sw_v)

                                                        active_mask = active_mask & (live_new_w.sum(axis=1) > 1e-8)
                                                        live_union_joints, live_union_w = compose_live_target_weights(
                                                            union_joints,
                                                            new_weights,
                                                            l_joints,
                                                            live_new_w,
                                                            active_mask,
                                                        )
                                                        _bind_mesh_weights(
                                                            live_dup,
                                                            live_dup + "_skinCluster",
                                                            live_union_joints,
                                                            live_union_w,
                                                        )

                                                local_v_for_live = local_v if len(i_idx) > 0 else np.asarray([], dtype=np.int64)
                                                inner_count = _apply_live_inner_blendshapes(
                                                    live_dup,
                                                    live_info,
                                                    num_new_verts,
                                                    i_idx,
                                                    k_idx,
                                                    local_v_for_live,
                                                    w_gauss,
                                                    active_mask,
                                                )
                                                
                                                iti_plug_live = iti_array_plug.elementByLogicalIndex(item_idx)
                                                for ci in range(iti_plug_live.numChildren()):
                                                    child = iti_plug_live.child(ci)
                                                    if om2.MFnAttribute(child.attribute()).name == "inputGeomTarget":
                                                        live_out_shapes = cmds.listRelatives(live_dup, shapes=True, fullPath=True, noIntermediate=True)
                                                        if live_out_shapes:
                                                            cmds.connectAttr(f"{live_out_shapes[0]}.worldMesh[0]", child.name(), force=True)
                                                        break
                                                bs_transferred += 1 + inner_count
                                                continue
                                            except Exception as exc:
                                                logger.warning("Live BS global 复刻失败 %s.%s: %s", new_mesh, tgt["name"], exc)
                                        
                                        # 写入静态 delta
                                        norms = np.linalg.norm(new_delta, axis=1)
                                        sparse_mask = norms > 0.00001
                                        sparse_ids_arr = np.where(sparse_mask)[0]
                                        
                                        if len(sparse_ids_arr) == 0:
                                            continue
                                        
                                        sparse_pts = om2.MPointArray()
                                        for si in sparse_ids_arr:
                                            d = new_delta[si]
                                            sparse_pts.append(om2.MPoint(d[0], d[1], d[2]))
                                        
                                        iti_plug = iti_array_plug.elementByLogicalIndex(item_idx)
                                        ipt_plug = None
                                        ict_plug = None
                                        for ci in range(iti_plug.numChildren()):
                                            child = iti_plug.child(ci)
                                            attr_name = om2.MFnAttribute(child.attribute()).name
                                            if attr_name == "inputPointsTarget":
                                                ipt_plug = child
                                            elif attr_name == "inputComponentsTarget":
                                                ict_plug = child
                                        if ipt_plug and ict_plug:
                                            ipt_plug.setMObject(om2.MFnPointArrayData().create(sparse_pts))
                                            fn_comp = om2.MFnSingleIndexedComponent()
                                            comp_obj = fn_comp.create(om2.MFn.kMeshVertComponent)
                                            fn_comp.addElements(sparse_ids_arr.tolist())
                                            fn_comp_data = om2.MFnComponentListData()
                                            comp_list_obj = fn_comp_data.create()
                                            fn_comp_data.add(comp_obj)
                                            ict_plug.setMObject(comp_list_obj)
                                        
                                        bs_transferred += 1
                                        
                                    attr_path = f"{new_bs}.{tgt['name']}"
                                    if not cmds.objExists(attr_path):
                                        attr_path = f"{new_bs}.weight[{t_idx}]"
                                    try: cmds.setAttr(attr_path, tgt["weight_value"])
                                    except: pass
                            except Exception as e:
                                pass
                        if bs_transferred > 0:
                            items.append(make_item(target_name, f"  BS: {bs_transferred} targets transferred"))
                except Exception as e:
                    items.append(make_item(target_name, f"GLOBAL WRAP FAILED: {str(e)[:80]}"))

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

        # ── Phase 6: 材质分配（从 Blender 材质信息）──
        _plog("Phase6: materials")
        all_new_nodes = []
        for rec in group_records.values():
            all_new_nodes.extend(rec.get("abc_nodes", []))
        all_new_nodes.extend(unpaired_nodes)

        materials_info = tex_info.get("materials", {})
        if materials_info:
            mat_results = _assign_materials_from_info(
                materials_info, all_new_nodes, list(tex_meshes.keys())
            )
            if mat_results:
                items.append(make_item("材质分配", f"已分配 {len(mat_results)} 组材质"))

        # ── 标记 sync 已完成，防重跑 ──
        new_cache_node = None
        for node in cmds.ls("cache", long=True, type="transform") or []:
            if "|cache" in node or node == "cache":
                new_cache_node = node
                break
        _mark_sync_done(new_cache_node)
        _plog("sync main body done")

    except Exception as e:
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
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
        SKILL_ID, "SUCCESS", t0,
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
