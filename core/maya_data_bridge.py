"""
Maya 数据桥 — Maya ↔ NumPy 序列化层。

职责：
  1. 在 Maya 进程内提取 rig mesh 的完整几何+形变数据 → NumPy 数组
  2. 序列化为 NPZ 供 Celery Worker 消费
  3. 将 Worker 计算结果注入回 Maya (SkinCluster / BlendShape)

所有函数仅依赖 maya.api.OpenMaya 2.0 + numpy，在 Maya 内执行。
"""

import os
import numpy as np
import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════════

def _get_dag(node_path):
    """从 Maya 路径获取 MFnMesh 需要的 MDagPath。"""
    import maya.api.OpenMaya as om2
    sel = om2.MSelectionList()
    sel.add(node_path)
    return sel.getDagPath(0)


def _find_skin_cluster(dag_path):
    """查找 mesh 上的 SkinCluster 节点，返回 (skin_node_name, shape_path) 或 (None, None)。"""
    import maya.cmds as cmds
    node_type = cmds.nodeType(dag_path)
    if node_type == "mesh":
        transforms = cmds.listRelatives(dag_path, parent=True, fullPath=True)
        transform = transforms[0] if transforms else None
    else:
        transform = dag_path
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
    return None, None


def _find_orig_shape(transform):
    """找到 ShapeOrig（绑定前冻结拓扑）节点。

    委托权威 get_deform_input（deformableShape 图关系，只认 Maya 连接、不猜名字）；
    找不到唯一 orig 返回 None，调用方回退可见 shape。
    """
    from dccs.maya.asset_info_collector import get_deform_input
    _, orig = get_deform_input(transform)
    return orig


def _triangulate_poly(face_counts, face_indices):
    """
    将多边形面扇形三角化为 (M, 3) int 数组。

    Parameters
    ----------
    face_counts : 每面顶点数列表
    face_indices : 顶点索引列表（连续排列）

    Returns
    -------
    triangles : ndarray (M, 3) int
    """
    triangles = []
    idx = 0
    for count in face_counts:
        v0 = face_indices[idx]
        for i in range(1, count - 1):
            triangles.append([v0, face_indices[idx + i], face_indices[idx + i + 1]])
        idx += count
    if not triangles:
        return np.zeros((0, 3), dtype=np.intp)
    return np.array(triangles, dtype=np.intp)


# ═══════════════════════════════════════════════════════════════
# Phase 1: 提取
# ═══════════════════════════════════════════════════════════════

def extract_mesh_geometry(dag_path):
    """
    从 Maya DAG 节点提取几何数据 (V, F, N)。

    优先从 ShapeOrig 取绑定姿态顶点。

    Returns
    -------
    dict : {
        'vertices': ndarray (N, 3) float64,
        'faces': ndarray (M, 3) int — 三角化后,
        'normals': ndarray (N, 3) float64,
        'face_counts': list[int] — 原始多边形每面顶点数,
        'face_indices': list[int] — 原始多边形顶点索引,
    }
    """
    import maya.api.OpenMaya as om2
    import maya.cmds as cmds

    # 确定 transform
    node_type = cmds.nodeType(dag_path)
    if node_type == "mesh":
        transforms = cmds.listRelatives(dag_path, parent=True, fullPath=True)
        transform = transforms[0] if transforms else dag_path
    else:
        transform = dag_path

    # 优先用 ShapeOrig（绑定前拓扑）
    orig = _find_orig_shape(transform)
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True,
                                noIntermediate=True) or []
    vis_shape = shapes[0] if shapes else dag_path
    geom_src = orig if orig else vis_shape

    dag = _get_dag(geom_src)
    fn = om2.MFnMesh(dag)

    # 顶点
    pts = fn.getPoints(om2.MSpace.kWorld)
    verts = np.array([[p.x, p.y, p.z] for p in pts], dtype=np.float64)

    # 面
    fc, fi = fn.getVertices()
    face_counts = list(fc)
    face_indices = list(fi)
    faces = _triangulate_poly(face_counts, face_indices)

    # 法线
    try:
        normals_raw = fn.getVertexNormals(False, om2.MSpace.kWorld)
        normals = np.array([[n.x, n.y, n.z] for n in normals_raw], dtype=np.float64)
    except Exception:
        # 后备：从三角面计算
        normals = _compute_vertex_normals(verts, faces)

    return {
        'vertices': verts,
        'faces': faces,
        'normals': normals,
        'face_counts': face_counts,
        'face_indices': face_indices,
    }


def extract_skin_weights(dag_path):
    """
    提取 mesh 的蒙皮权重矩阵。

    Returns
    -------
    dict or None : {
        'joints': list[str] — 骨骼名列表,
        'weights': ndarray (N, J) float64 — 权重矩阵,
    }
    """
    import maya.api.OpenMaya as om2
    import maya.api.OpenMayaAnim as oma2
    import maya.cmds as cmds

    skin_node, shape = _find_skin_cluster(dag_path)
    if not skin_node:
        return None

    joints = cmds.skinCluster(skin_node, query=True, influence=True)
    if not joints:
        return None

    sel_skin = om2.MSelectionList()
    sel_skin.add(skin_node)
    fn_skin = oma2.MFnSkinCluster(sel_skin.getDependNode(0))

    sel_shape = om2.MSelectionList()
    sel_shape.add(shape)
    dag_shape = sel_shape.getDagPath(0)

    num_verts = om2.MFnMesh(dag_shape).numVertices
    comp = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
    om2.MFnSingleIndexedComponent(comp).setCompleteData(num_verts)

    weights_flat, _ = fn_skin.getWeights(dag_shape, comp)
    weights = np.array(weights_flat).reshape(num_verts, len(joints))

    return {'joints': joints, 'weights': weights}


def extract_blendshape_deltas(dag_path):
    """
    提取 mesh 上所有 BlendShape 的 delta 数据。

    Returns
    -------
    dict : {target_name: ndarray (N, 3) float64}  — BS delta 字典
    """
    import maya.api.OpenMaya as om2
    import maya.cmds as cmds

    node_type = cmds.nodeType(dag_path)
    if node_type == "mesh":
        transforms = cmds.listRelatives(dag_path, parent=True, fullPath=True)
        transform = transforms[0] if transforms else dag_path
    else:
        transform = dag_path

    all_shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
    bs_nodes = set()
    for shape in all_shapes:
        try:
            history = cmds.listHistory(shape) or []
            bs_nodes.update(cmds.ls(history, type="blendShape"))
        except Exception:
            pass
    if not bs_nodes:
        return {}

    # base 顶点数
    orig = _find_orig_shape(transform)
    base_shape = orig if orig else dag_path
    fn_base = om2.MFnMesh(_get_dag(base_shape))
    num_base = fn_base.numVertices
    base_pts = fn_base.getPoints(om2.MSpace.kObject)

    result = {}
    for bs_node in sorted(bs_nodes):
        sel = om2.MSelectionList()
        sel.add(bs_node)
        fn_bs = om2.MFnDependencyNode(sel.getDependNode(0))
        it_plug = fn_bs.findPlug("inputTarget", False)
        geom_indices = it_plug.getExistingArrayAttributeIndices()
        if not geom_indices:
            continue
        itg_plug = it_plug.elementByLogicalIndex(geom_indices[0]).child(0)

        # alias 映射
        alias_list = cmds.aliasAttr(bs_node, query=True) or []
        idx_to_name = {}
        for i in range(0, len(alias_list), 2):
            try:
                wi = int(alias_list[i + 1].split("[")[1].rstrip("]"))
                idx_to_name[wi] = alias_list[i]
            except Exception:
                pass

        for t_idx in itg_plug.getExistingArrayAttributeIndices():
            t_name = idx_to_name.get(t_idx, f"{bs_node}_target_{t_idx}")
            tgt_plug = itg_plug.elementByLogicalIndex(t_idx)
            iti_plug = tgt_plug.child(0)
            item_indices = iti_plug.getExistingArrayAttributeIndices()
            if not item_indices:
                continue

            # 取 6000 权重项（标准 target）
            for item_idx in item_indices:
                iti = iti_plug.elementByLogicalIndex(item_idx)
                ipt_plug = ict_plug = None
                for ci in range(iti.numChildren()):
                    child = iti.child(ci)
                    attr_name = om2.MFnAttribute(child.attribute()).name
                    if attr_name == "inputPointsTarget":
                        ipt_plug = child
                    elif attr_name == "inputComponentsTarget":
                        ict_plug = child

                if not ipt_plug:
                    continue

                delta = np.zeros((num_base, 3), dtype=np.float64)
                try:
                    pts_data = ipt_plug.asMDataHandle().data()
                    if pts_data.isNull():
                        continue
                    pts = om2.MFnPointArrayData(pts_data).array()
                    ids = []
                    if ict_plug:
                        try:
                            import maya.OpenMaya as om1
                            sel1 = om1.MSelectionList()
                            sel1.add(ict_plug.name())
                            plug1 = om1.MPlug()
                            sel1.getPlug(0, plug1)
                            obj1 = plug1.asMObject()
                            if not obj1.isNull():
                                fn_cl1 = om1.MFnComponentListData(obj1)
                                for ci in range(fn_cl1.length()):
                                    fn_si1 = om1.MFnSingleIndexedComponent(fn_cl1[ci])
                                    elem = om1.MIntArray()
                                    fn_si1.getElements(elem)
                                    ids.extend(list(elem))
                        except Exception as e:
                            import logging
                            logging.error(f"Error parsing components with OM1: {e}")

                    if ids and len(ids) == len(pts):
                        for i, vid in enumerate(ids):
                            if vid < num_base:
                                delta[vid] = [pts[i].x, pts[i].y, pts[i].z]
                    elif len(pts) == num_base:
                        for i in range(num_base):
                            delta[i] = [pts[i].x, pts[i].y, pts[i].z]

                    if np.any(np.abs(delta) > 1e-7):
                        result[t_name] = delta
                except Exception:
                    pass

    return result


def extract_all_rig_meshes(cache_group='cache'):
    """
    批量提取 cache 组下所有 rig mesh 的完整数据。

    Returns
    -------
    dict : {
        'meshes': {dag_path: {geometry, skin, bs}},
        'all_joints': list[str] — 全局关节去重列表,
    }
    """
    import maya.cmds as cmds

    actual_cache = None
    for candidate in [f"|Group|Geometry|{cache_group}", cache_group]:
        if cmds.objExists(candidate):
            actual_cache = candidate
            break
    if not actual_cache:
        raise RuntimeError(f'场景中未找到 cache 组: {cache_group}')

    all_shapes = cmds.listRelatives(
        actual_cache, allDescendents=True, type="mesh", fullPath=True
    ) or []

    meshes = {}
    all_joints_set = set()

    for shape in all_shapes:
        if cmds.getAttr(shape + ".intermediateObject"):
            continue

        geom = extract_mesh_geometry(shape)
        skin = extract_skin_weights(shape)
        bs = extract_blendshape_deltas(shape)

        if skin:
            all_joints_set.update(skin['joints'])

        meshes[shape] = {
            'geometry': geom,
            'skin': skin,
            'bs_deltas': bs,
        }

    all_joints = sorted(all_joints_set)

    # 统一权重矩阵列数（对齐到全局关节列表）
    for dag, data in meshes.items():
        skin = data['skin']
        if not skin:
            continue
        local_joints = skin['joints']
        local_w = skin['weights']
        N = local_w.shape[0]
        global_w = np.zeros((N, len(all_joints)), dtype=np.float64)
        for li, jname in enumerate(local_joints):
            gi = all_joints.index(jname)
            global_w[:, gi] = local_w[:, li]
        skin['weights_global'] = global_w

    logger.info(f"extract_all_rig_meshes: {len(meshes)} meshes, "
                f"{len(all_joints)} joints")
    return {'meshes': meshes, 'all_joints': all_joints}


# ═══════════════════════════════════════════════════════════════
# 序列化
# ═══════════════════════════════════════════════════════════════

def save_rig_data(rig_data, output_path):
    """
    将 extract_all_rig_meshes 的结果序列化为 NPZ 文件。

    NPZ 结构：
      - meta_all_joints: 全局关节名列表 (JSON string)
      - meta_mesh_keys: mesh DAG 路径列表 (JSON string)
      - {i}_vertices, {i}_faces, {i}_normals: 几何
      - {i}_weights: 全局权重矩阵
      - {i}_bs_{name}: BS delta
    """
    import json

    mesh_keys = list(rig_data['meshes'].keys())
    arrays = {
        'meta_all_joints': np.array(json.dumps(rig_data['all_joints'])),
        'meta_mesh_keys': np.array(json.dumps(mesh_keys)),
    }

    for i, (dag, data) in enumerate(rig_data['meshes'].items()):
        geom = data['geometry']
        arrays[f'{i}_vertices'] = geom['vertices']
        arrays[f'{i}_faces'] = geom['faces']
        arrays[f'{i}_normals'] = geom['normals']

        skin = data.get('skin')
        if skin and 'weights_global' in skin:
            arrays[f'{i}_weights'] = skin['weights_global']

        bs = data.get('bs_deltas', {})
        if bs:
            bs_names = list(bs.keys())
            arrays[f'{i}_bs_names'] = np.array(json.dumps(bs_names))
            for bi, (name, delta) in enumerate(bs.items()):
                arrays[f'{i}_bs_{bi}'] = delta

    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    np.savez_compressed(output_path, **arrays)
    logger.info(f"save_rig_data: {len(mesh_keys)} meshes → {output_path}")


def load_rig_data(npz_path):
    """
    从 NPZ 反序列化为 Worker 可消费的 dict。

    Returns
    -------
    dict : {
        'all_joints': list[str],
        'meshes': [{
            'dag': str,
            'vertices': ndarray (N,3),
            'faces': ndarray (M,3),
            'normals': ndarray (N,3),
            'weights': ndarray (N,J) or None,
            'bs_deltas': {name: ndarray (N,3)},
        }]
    }
    """
    import json

    data = np.load(npz_path, allow_pickle=True)
    all_joints = json.loads(str(data['meta_all_joints']))
    mesh_keys = json.loads(str(data['meta_mesh_keys']))

    meshes = []
    for i, dag in enumerate(mesh_keys):
        entry = {
            'dag': dag,
            'vertices': data[f'{i}_vertices'],
            'faces': data[f'{i}_faces'],
            'normals': data[f'{i}_normals'],
            'weights': data.get(f'{i}_weights'),
            'bs_deltas': {},
        }
        bs_names_key = f'{i}_bs_names'
        if bs_names_key in data:
            bs_names = json.loads(str(data[bs_names_key]))
            for bi, name in enumerate(bs_names):
                entry['bs_deltas'][name] = data[f'{i}_bs_{bi}']
        meshes.append(entry)

    return {'all_joints': all_joints, 'meshes': meshes}


# ═══════════════════════════════════════════════════════════════
# Phase 3: 注入
# ═══════════════════════════════════════════════════════════════

def inject_skin_weights(mesh_path, joint_names, weights):
    """
    将权重矩阵写入 Maya SkinCluster。

    Parameters
    ----------
    mesh_path : str — Maya DAG 路径（transform）
    joint_names : list[str] — 骨骼名列表
    weights : ndarray (N, J) — 权重矩阵（已归一化）
    """
    import maya.api.OpenMaya as om2
    import maya.api.OpenMayaAnim as oma2
    import maya.cmds as cmds

    # 过滤不存在的骨骼
    exist_mask = np.array([cmds.objExists(j) for j in joint_names])
    active_cols = np.where(weights.max(axis=0) > 0)[0]
    active_cols = active_cols[exist_mask[active_cols]]

    if len(active_cols) == 0:
        logger.warning(f"inject_skin_weights: 无有效骨骼，跳过 {mesh_path}")
        return

    bind_joints = [joint_names[c] for c in active_cols]
    compact_w = weights[:, active_cols]

    # 归一化
    row_sums = compact_w.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    compact_w = compact_w / row_sums

    target_name = mesh_path.split("|")[-1]
    skin_name = target_name + "_skinCluster"

    new_skin = cmds.skinCluster(
        mesh_path, bind_joints, toSelectedBones=True,
        bindMethod=0, skinMethod=0, normalizeWeights=1,
        name=skin_name)[0]

    sel_skin = om2.MSelectionList()
    sel_skin.add(new_skin)
    fn_skin = oma2.MFnSkinCluster(sel_skin.getDependNode(0))

    shapes = cmds.listRelatives(mesh_path, shapes=True,
                                fullPath=True, noIntermediate=True)
    sel_shape = om2.MSelectionList()
    sel_shape.add(shapes[0])
    dag_shape = sel_shape.getDagPath(0)

    num_verts = om2.MFnMesh(dag_shape).numVertices
    comp = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
    om2.MFnSingleIndexedComponent(comp).setCompleteData(num_verts)

    inf_idx = om2.MIntArray(list(range(len(bind_joints))))
    fn_skin.setWeights(dag_shape, comp, inf_idx,
                       om2.MDoubleArray(compact_w.flatten().tolist()))

    logger.info(f"inject_skin_weights: {mesh_path} ← "
                f"{len(bind_joints)} joints, {num_verts} verts")


def inject_bs_deltas(mesh_path, bs_deltas, bs_node_name=None):
    """
    将 BS delta 字典写入 Maya BlendShape 节点。

    Parameters
    ----------
    mesh_path : str — Maya DAG 路径
    bs_deltas : dict {target_name: ndarray (N, 3)}
    bs_node_name : str, optional — BS 节点名，默认自动命名
    """
    import maya.api.OpenMaya as om2
    import maya.cmds as cmds

    if not bs_deltas:
        return

    target_name = mesh_path.split("|")[-1]
    if not bs_node_name:
        bs_node_name = f"{target_name}_blendShape"

    new_bs = cmds.blendShape(mesh_path, name=bs_node_name,
                             frontOfChain=True)[0]
    sel_bs = om2.MSelectionList()
    sel_bs.add(new_bs)
    fn_bs = om2.MFnDependencyNode(sel_bs.getDependNode(0))
    it_plug = fn_bs.findPlug("inputTarget", False)
    geom_indices = it_plug.getExistingArrayAttributeIndices()
    geom_idx = geom_indices[0] if geom_indices else 0
    itg_plug = it_plug.elementByLogicalIndex(geom_idx).child(0)

    for t_idx, (name, delta) in enumerate(bs_deltas.items()):
        delta = np.asarray(delta, dtype=np.float64)
        cmds.aliasAttr(name, f"{new_bs}.weight[{t_idx}]")

        tgt_plug = itg_plug.elementByLogicalIndex(t_idx)
        iti_plug = tgt_plug.child(0)
        item_idx = 6000  # 标准 target weight=1.0

        norms = np.linalg.norm(delta, axis=1)
        sparse_ids = np.where(norms > 1e-5)[0]
        if len(sparse_ids) == 0:
            continue

        sparse_pts = om2.MPointArray()
        for si in sparse_ids:
            d = delta[si]
            sparse_pts.append(om2.MPoint(d[0], d[1], d[2]))

        iti_d = iti_plug.elementByLogicalIndex(item_idx)
        ipt_plug = ict_plug = None
        for ci in range(iti_d.numChildren()):
            child = iti_d.child(ci)
            attr_name = om2.MFnAttribute(child.attribute()).name
            if attr_name == "inputPointsTarget":
                ipt_plug = child
            elif attr_name == "inputComponentsTarget":
                ict_plug = child

        if ipt_plug and ict_plug:
            ipt_plug.setMObject(om2.MFnPointArrayData().create(sparse_pts))
            fn_comp = om2.MFnSingleIndexedComponent()
            comp_obj = fn_comp.create(om2.MFn.kMeshVertComponent)
            fn_comp.addElements(sparse_ids.tolist())
            fn_comp_data = om2.MFnComponentListData()
            comp_list_obj = fn_comp_data.create()
            fn_comp_data.add(comp_obj)
            ict_plug.setMObject(comp_list_obj)

    logger.info(f"inject_bs_deltas: {mesh_path} ← {len(bs_deltas)} targets")


# ═══════════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════════

def _compute_vertex_normals(verts, faces):
    """从三角面计算顶点法线（后备方案）。"""
    v0 = verts[faces[:, 0]]
    v1 = verts[faces[:, 1]]
    v2 = verts[faces[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)

    normals = np.zeros_like(verts)
    for i in range(3):
        np.add.at(normals, faces[:, i], fn)

    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return normals / norms
