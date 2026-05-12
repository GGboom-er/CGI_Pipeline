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

import maya.cmds as cmds
from maya.api import OpenMaya as om2
from maya.api import OpenMayaAnim as oma2

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT
from core.receipt import make_receipt, make_item

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

def _load_compare_result(compare_result_path):
    with open(compare_result_path, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    if data.get("schema_version") != "compare_result.v1":
        raise ValueError(f"不支持的 compare_result schema: {data.get('schema_version')}")
    report = data.get("compare")
    source_info = data.get("source_info") or {"meshes": {}, "textures": {}, "source_file": ""}
    if not isinstance(report, dict):
        raise ValueError("compare_result 缺少 compare 字典")
    if not isinstance(source_info, dict):
        raise ValueError("compare_result.source_info 类型错误")
    for field in ("pairing_groups", "target_only_dags"):
        if field not in report:
            raise ValueError(f"compare_result.compare 缺少字段: {field}")
    return data, report, source_info


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
                                        for ki in range(3):
                                            if valid_v[ni, ki]:
                                                new_delta[ni] += old_delta[nearest_indices[ni, ki]] * w_gauss[ni, ki]

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
            existing = cmds.ls("Group", long=True, type="transform")
            if existing:
                current = existing[0]
                continue
        elif grp == "cache":
            geo = cmds.ls("Geometry", long=True, type="transform")
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

    # ORIG_INJECT：注坐标到 ShapeOrig
    if inject_points is not None:
        orig_shape = _find_orig_shape(transform)
        if orig_shape:
            try:
                sel = om2.MSelectionList()
                sel.add(orig_shape)
                fn_mesh = om2.MFnMesh(sel.getDagPath(0))
                pa = om2.MPointArray()
                arr = np.asarray(inject_points).reshape(-1, 3)
                for p in arr:
                    pa.append(om2.MPoint(float(p[0]), float(p[1]), float(p[2])))
                fn_mesh.setPoints(pa)
            except Exception:
                pass

    return transform


def _assign_materials_from_info(materials_info, all_mesh_nodes, tex_meshes_keys):
    """根据 Blender 采集的材质信息为 Maya mesh 创建 lambert 并按面分配。

    委托给独立技能 maya_apply_materials.apply_materials 执行。
    """
    from skills.maya_apply_materials.maya_apply_materials import apply_materials
    return apply_materials(materials_info, all_mesh_nodes)

def _extract_blendshape_data(rig_dag):
    """提取旧 RIG mesh 上所有 BlendShape 数据（含 in-between、Live-Link 蒙皮），返回列表。"""
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
            except:
                pass
        
        targets = []
        for t_idx in target_indices:
            t_name = idx_to_name.get(t_idx, f"target_{t_idx}")
            
            attr_path = f"{bs_node}.{t_name}"
            if not cmds.objExists(attr_path):
                attr_path = f"{bs_node}.weight[{t_idx}]"
            weight_val = 0.0
            try:
                weight_val = cmds.getAttr(attr_path)
            except:
                pass
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
                                    fn_si = om2.MFnSingleIndexedComponent(fn_comp_list[ci])
                                    ids.extend(fn_si.getElements())
                        except:
                            pass
                        
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
                except:
                    pass
                
                # Live Target 检测与深度信息采集
                if not has_data:
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
                                
                                for vi in range(min(num_base, len(live_pts))):
                                    sparse_delta[vi] = [
                                        live_pts[vi].x - base_pts[vi].x,
                                        live_pts[vi].y - base_pts[vi].y,
                                        live_pts[vi].z - base_pts[vi].z
                                    ]
                                has_data = True
                                
                                # 采集 Live Mesh 的蒙皮数据（深度复刻用）
                                live_tr = cmds.listRelatives(live_shape, parent=True, fullPath=True)
                                live_skin_info = None
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
                                        except:
                                            pass
                                live_info = {
                                    "transform": live_tr[0] if live_tr else None,
                                    "shape": live_shape,
                                    "skin": live_skin_info,
                                }
                    except:
                        pass
                
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


def execute(payload: dict) -> dict:
    import numpy as np
    import trimesh

    _plog("sync execute() entered")
    t0 = time.time()
    params = payload.get("parameters", {})
    # 节点化命名（推荐）；老键名保持向后兼容
    abc_path = (params.get("source_abc") or params.get("abc_path", "")).strip()
    tex_json = (params.get("source_info") or params.get("tex_json", "")).strip()
    compare_result_path = params.get("compare_result", "")
    compare_result_path = (compare_result_path or "").strip()
    cache_group = (params.get("cache_group") or "cache").strip()
    rig_path = payload.get("source_path", "")

    if not compare_result_path:
        return make_receipt("maya_sync_rig_incremental", "ERROR", t0,
                            error="缺少必填参数: compare_result。请先用 maya_compare_asset_in_scene 或 pipeline_compare_asset 生成对比结果。")
    if not abc_path and not tex_json:
        return make_receipt("maya_sync_rig_incremental", "ERROR", t0,
                            error="缺少必填参数: source_abc 或 source_info。拼装推荐使用 source_abc。")
    if not rig_path:
        return make_receipt("maya_sync_rig_incremental", "ERROR", t0,
                            error="缺少必填参数: source_path（target 侧 rig 场景）")

    # ── 加载项目 profile（所有算法阈值） ──
    project = payload.get("project") or params.get("project") or _infer_project_from_path(rig_path)
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
    compare_result_data = None

    # ── 读取 source 数据 / 外部 compare_result ──
    try:
        compare_result_data, external_report, light_tex_info = _load_compare_result(compare_result_path)
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
        items.append(make_item("CompareResult", f"使用对比结果: {os.path.basename(compare_result_path)}"))
    except Exception as e:
        return make_receipt("maya_sync_rig_incremental", "ERROR", t0, error=f"读取源数据失败: {e}")

    # Sandbox mode: no backup required.

    cmds.undoInfo(openChunk=True, chunkName="sync_rig_incremental_v9")
    try:
        # ── 幂等保护：已跑过直接拒绝 ──
        already_done, done_reason = _check_sync_already_done(cache_group)
        if already_done:
            cmds.undoInfo(closeChunk=True)
            return make_receipt(
                "maya_sync_rig_incremental", "ERROR", t0,
                error=f"场景似乎已被 sync 处理过，请从原始 rig 场景重新开始。原因：{done_reason}"
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
            return make_receipt(
                "maya_sync_rig_incremental", "ERROR", t0,
                error=f"场景中未找到 cache_group: {cache_group}"
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
        if empty_rig_dags:
            cmds.undoInfo(closeChunk=True)
            cmds.undo()
            return make_receipt(
                "maya_sync_rig_incremental", "AUDIT_FAILED", t0,
                error=(
                    "target rig 中存在无法采集 ShapeOrig 几何的 mesh，已撤销本步。"
                    f"问题节点: {empty_rig_dags[:20]}"
                ),
            )

        # ── 获取同步指令：只消费前置 compare_result ──
        sync_md_content = ""
        rig_info = {"meshes": rig_meshes, "textures": {}}
        if external_report is not None:
            report = external_report
        else:
            raise RuntimeError("compare_result 读取失败，缺少外部对比结果。")
        pairing_groups = report.get("pairing_groups", [])
        target_only_dags = report.get("target_only_dags", [])

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


        # ── Phase 2: 提取旧 RIG 骨骼与权重 ──
        _plog(f"Phase2a: extract skin data for {len(rig_meshes)} rig meshes")
        rig_skin_data = {}
        for i, (rig_dag, rig_data) in enumerate(rig_meshes.items()):
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

        # ── Phase 2b: 提取旧 RIG BlendShape 数据 ──
        _plog(f"Phase2b: extract BS data for {len(rig_meshes)} rig meshes")
        rig_bs_data = {}
        for i, rig_dag in enumerate(rig_meshes.keys()):
            if i % 10 == 0:
                _plog(f"  Phase2b progress: {i}/{len(rig_meshes)}")
            bs_infos = _extract_blendshape_data(rig_dag)
            if bs_infos:
                rig_bs_data[rig_dag] = bs_infos
        _plog(f"Phase2b done: {len(rig_bs_data)} meshes have BS data")

        # ── 预处理：tex mesh 表（key 保持原样，与 pairing_groups.abc_dags 一致）──
        tex_meshes = dict(tex_info.get("meshes", {}))

        # compare 的 rig_dag 是相对路径格式 (如 "cache|grp|meshShape")
        # rig_meshes 的 key 是 Maya fullPath (如 "|Group|RIG_cache|RIG_grp|RIG_meshShape")
        # 建立反向映射：去掉 RIG_ 前缀的相对路径 → fullPath
        rig_dag_lookup = {}
        for full_path in rig_meshes:
            parts = full_path.split("|")
            rig_start = -1
            for i, p in enumerate(parts):
                if p == rig_cache_group:
                    rig_start = i
                    break
            if rig_start >= 0:
                rel_parts = [p[len(rig_prefix):] if p.startswith(rig_prefix) else p
                             for p in parts[rig_start:]]
                rig_dag_lookup["|".join(rel_parts)] = full_path

            all_rel_parts = [
                p[len(rig_prefix):] if p.startswith(rig_prefix) else p
                for p in parts
                if p
            ]
            if all_rel_parts:
                all_rel_key = "|".join(all_rel_parts)
                rig_dag_lookup[all_rel_key] = full_path
                rig_dag_lookup["|" + all_rel_key] = full_path

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
                return make_receipt(
                    "maya_sync_rig_incremental", "AUDIT_FAILED", t0,
                    error=(
                        "compare_result 与当前 target rig 不匹配，已撤销本步。"
                        f"缺失 rig 引用: {missing_rigs[:20]}"
                    ),
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
                    items.append(make_item(target_name, f"{action}: 搬运 + 改名"))
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

            # 未识别兜底
            items.append(make_item(f"group:{gid}", f"未识别 action={action}，降级 voting pool"))
            for abc_dag in abc_dags_g:
                td = tex_meshes.get(abc_dag)
                if td:
                    voting_pool_tex[abc_dag] = td

        # ── target_only：rig 独有，原位保留（不删！）──
        for tod in target_only_dags:
            rig_full = _translate_rig(tod)
            if not rig_full:
                continue
            reused_rig_dags.add(rig_full)  # 防止被当 super mesh 源
            transform = cmds.listRelatives(rig_full, parent=True, fullPath=True)
            if transform and cmds.objExists(transform[0]):
                target_only_rig_nodes.append(transform[0])
            items.append(make_item(tod.split("|")[-1], "target_only: 原位保留"))


        # ── Tier 2: 构建全局 Super Mesh (GaussK3 空间包裹制) ──
        _plog(f"Tier2: build Super Mesh (reused={len(reused_rig_dags)}, pool={len(voting_pool_tex)})")
        super_verts = []
        vert_to_rig_map = []
        rig_vert_offsets = {}
        vert_offset = 0

        for rig_dag, rig_data in rig_meshes.items():
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

        # ── Phase 3.5: Chamfer Distance 自动配对 ──
        auto_pairings = _auto_pair_by_chamfer(voting_pool_tex, rig_meshes, rig_skin_data, reused_rig_dags, profile=profile)
        for dag_tex, best_rig_dag in auto_pairings.items():
            voting_pool_tex[dag_tex]["_paired_rig_dag"] = best_rig_dag
        if auto_pairings:
            items.append(make_item("Chamfer 自动配对", f"为 {len(auto_pairings)} 个 mesh 找到定向投射源"))

        # ── Phase 4: 空间包裹与新节点创建 ──
        _plog(f"Phase4: create {len(voting_pool_tex)} new meshes + weight transfer")
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
                                        
                                        # -------------------------------------------------------------
                                        # [终极防御] 拓扑保边平滑 (BS Delta 同频平滑)
                                        # -------------------------------------------------------------
                                        if len(adj_list) > 0:
                                            new_delta = _topological_smooth(num_new_verts, adj_list, new_delta, iterations=30, blend=0.5)
                                        
                                        # ── Live Target 深度复刻 ──
                                        if live_info and live_info.get("transform"):
                                            try:
                                                live_dup = cmds.duplicate(new_mesh, name=f"{target_name}_live_{t_idx}")[0]
                                                sel_ld = om2.MSelectionList()
                                                ld_shapes = cmds.listRelatives(live_dup, shapes=True, fullPath=True, noIntermediate=True)
                                                sel_ld.add(ld_shapes[0])
                                                fn_ld = om2.MFnMesh(sel_ld.getDagPath(0))
                                                base_live_pts = fn_ld.getPoints(om2.MSpace.kObject)
                                                for vi in range(min(num_new_verts, len(base_live_pts))):
                                                    base_live_pts[vi].x += new_delta[vi][0]
                                                    base_live_pts[vi].y += new_delta[vi][1]
                                                    base_live_pts[vi].z += new_delta[vi][2]
                                                fn_ld.setPoints(base_live_pts, om2.MSpace.kObject)
                                                
                                                # 如果旧活体有蒙皮，投射权重到新活体
                                                live_skin = live_info.get("skin")
                                                if live_skin and live_skin.get("joints"):
                                                    l_joints = [j for j in live_skin["joints"] if cmds.objExists(j)]
                                                    l_weights = live_skin["weights"]
                                                    if l_joints and l_weights.shape[0] > 0:
                                                        l_joint_idx = {j: i for i, j in enumerate(live_skin["joints"])}
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
                                                                    
                                                        # -------------------------------------------------------------
                                                        # [终极防御] 拓扑保边平滑 (Live Target 权重同频)
                                                        # -------------------------------------------------------------
                                                        if len(adj_list) > 0:
                                                            live_new_w = _topological_smooth(num_new_verts, adj_list, live_new_w, iterations=30, blend=0.5)
                                                            
                                                        live_new_w[live_new_w < 0.01] = 0.0
                                                        
                                                        lrs = live_new_w.sum(axis=1, keepdims=True)
                                                        lrs[lrs == 0] = 1.0
                                                        live_new_w /= lrs
                                                        
                                                        l_active = np.where(live_new_w.max(axis=0) > 0.0)[0]
                                                        if len(l_active) > 0:
                                                            l_bind = [l_joints[c] for c in l_active]
                                                            l_cw = live_new_w[:, l_active]
                                                            l_sk = cmds.skinCluster(live_dup, l_bind, toSelectedBones=True,
                                                                                    bindMethod=0, skinMethod=0, normalizeWeights=1,
                                                                                    name=live_dup + "_skinCluster")[0]
                                                            sel_lsk = om2.MSelectionList()
                                                            sel_lsk.add(l_sk)
                                                            fn_lsk = oma2.MFnSkinCluster(sel_lsk.getDependNode(0))
                                                            sel_lsh2 = om2.MSelectionList()
                                                            sel_lsh2.add(ld_shapes[0])
                                                            l_dag = sel_lsh2.getDagPath(0)
                                                            l_nv = om2.MFnMesh(l_dag).numVertices
                                                            l_comp = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
                                                            om2.MFnSingleIndexedComponent(l_comp).setCompleteData(l_nv)
                                                            l_inf = om2.MIntArray(list(range(len(l_bind))))
                                                            fn_lsk.setWeights(l_dag, l_comp, l_inf, om2.MDoubleArray(l_cw.flatten().tolist()))
                                                
                                                iti_plug_live = iti_array_plug.elementByLogicalIndex(item_idx)
                                                for ci in range(iti_plug_live.numChildren()):
                                                    child = iti_plug_live.child(ci)
                                                    if om2.MFnAttribute(child.attribute()).name == "inputGeomTarget":
                                                        live_out_shapes = cmds.listRelatives(live_dup, shapes=True, fullPath=True, noIntermediate=True)
                                                        if live_out_shapes:
                                                            cmds.connectAttr(f"{live_out_shapes[0]}.worldMesh[0]", child.name(), force=True)
                                                        break
                                                continue
                                            except Exception:
                                                pass
                                        
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

        # 新建的 ABC mesh 必须补齐标准 ShapeOrig，确保后置 compare 仍按严格采集规则工作。
        created_mesh_nodes = []
        for rec in group_records.values():
            created_mesh_nodes.extend(rec.get("abc_nodes", []))
        created_mesh_nodes.extend(unpaired_nodes)
        created_mesh_nodes = sorted({n for n in created_mesh_nodes if n and cmds.objExists(n)})
        orig_init_errors = []
        orig_created = 0
        for node in created_mesh_nodes:
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
        return make_receipt("maya_sync_rig_incremental", "ERROR", t0, error=f"执行崩溃，已撤销: {e}")

    cmds.undoInfo(closeChunk=True)
    _plog("undo chunk closed")

    # receipt 统计按 4 类组 + target_only
    n_identical   = sum(1 for g in pairing_groups if g["action"] == "IDENTICAL")
    n_orig_inject = sum(1 for g in pairing_groups if g["action"] == "ORIG_INJECT")
    n_paired      = sum(1 for g in pairing_groups if g["action"] == "PAIRED")
    n_unpaired    = sum(1 for g in pairing_groups if g["action"] == "UNPAIRED")
    n_target_only = len(target_only_dags)

    return make_receipt(
        "maya_sync_rig_incremental", "SUCCESS", t0,
        summary_action=(
            f"IDENTICAL: {n_identical} | ORIG_INJECT: {n_orig_inject} | "
            f"PAIRED: {n_paired} | UNPAIRED: {n_unpaired} | target_only: {n_target_only}"
        ),
        summary_count=len(all_new_nodes),
        summary_label="资产同步",
        items=items,
        outputs={},
        report_content=sync_md_content,
    )
