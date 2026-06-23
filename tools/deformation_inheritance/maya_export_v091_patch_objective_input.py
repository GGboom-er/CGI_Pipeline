# -*- coding: utf-8 -*-
"""导出 v091 patch-level constrained optimization 输入。

本脚本只采样数据，不写权重。目标是给离线求解器提供真实 Maya DG
姿态下的 source 位移、target LBS basis、局部 patch 拓扑和当前 base 权重。
"""

from __future__ import annotations

import json
from pathlib import Path
import traceback

import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import maya.cmds as cmds
import numpy as np


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
INPUT_SCENE = PROJECT_DIR / "test_v090_A_patchSurface_candidates_eval.ma"
SCENE_DATA = INFO_DIR / "v087_all_influence_input.npz"
V089B_WEIGHTS = INFO_DIR / "v089b_fast_continuous_field_candidates.npz"
V090_SURFACE = INFO_DIR / "v090_patch_surface_candidates_objective_arrays.npz"
V090_CANDIDATES = INFO_DIR / "v090_patch_surface_candidates.npz"
OUT_NPZ = INFO_DIR / "v091_patch_objective_input.npz"
OUT_JSON = INFO_DIR / "v091_patch_objective_input_summary.json"

SOURCE = "M_Head_base"
BASE_MESH = "A_V089B_001_baseHybrid"
BASE_KEY = "v089b_base_hybrid_m0010"

POSES = [
    ("jaw_rx_5", {"M_Jaw_A_ctrl.rotateX": 5.0}),
    ("jaw_rx_15", {"M_Jaw_A_ctrl.rotateX": 15.0}),
    ("jaw_rx_25", {"M_Jaw_A_ctrl.rotateX": 25.0}),
    ("jaw_rx_30", {"M_Jaw_A_ctrl.rotateX": 30.0}),
    ("jawA_ty_n05", {"M_JawA_A_ctrl.translateY": -0.5}),
    ("jawUpA_ty_05", {"M_JawUpA_A_ctrl.translateY": 0.5}),
    ("mouth_ty_05", {"M_Mouth_A_ctrl.translateY": 0.5}),
    ("mouth_l_tx_n05", {"L_Mouth_A_ctrl.translateX": -0.5}),
    ("mouth_r_tx_05", {"R_Mouth_A_ctrl.translateX": 0.5}),
    ("r_cheekA_ty_1", {"R_CheekA_A_ctrl.translateY": 1.0}),
    ("l_cheekA_ty_1", {"L_CheekA_A_ctrl.translateY": 1.0}),
    ("r_cheekB_ty_1", {"R_CheekB_A_ctrl.translateY": 1.0}),
    ("l_cheekB_ty_1", {"L_CheekB_A_ctrl.translateY": 1.0}),
    ("r_upcheekMid_ty_1", {"R_UpCheekMid_A_ctrl.translateY": 1.0}),
    ("l_upcheekMid_ty_1", {"L_UpCheekMid_A_ctrl.translateY": 1.0}),
    ("r_upLidMid_ty_05", {"R_UpLidMid_A_ctrl.translateY": 0.5}),
    ("r_loLidMid_ty_n05", {"R_LoLidMid_A_ctrl.translateY": -0.5}),
    ("l_upLidMid_ty_05", {"L_UpLidMid_A_ctrl.translateY": 0.5}),
    ("l_loLidMid_ty_n05", {"L_LoLidMid_A_ctrl.translateY": -0.5}),
    ("m_upLip_ty_05", {"M_UpLip_A_ctrl.translateY": 0.5}),
    ("m_loLip_ty_n05", {"M_LoLip_A_ctrl.translateY": -0.5}),
    ("r_upLipMain_ty_05", {"R_UpLipMain1_A_ctrl.translateY": 0.5}),
    ("l_upLipMain_ty_05", {"L_UpLipMain1_A_ctrl.translateY": 0.5}),
    ("r_loLipMain_ty_n05", {"R_LoLipMain1_A_ctrl.translateY": -0.5}),
    ("l_loLipMain_ty_n05", {"L_LoLipMain1_A_ctrl.translateY": -0.5}),
    ("noseTip_tz_05", {"M_NoseTip_A_ctrl.translateZ": 0.5}),
    ("chin_ty_n05", {"M_Chin_A_ctrl.translateY": -0.5}),
]

MAX_PATCH_VERTICES = 260
CORE_TRI_COUNT = 520


def _leaf(name: str) -> str:
    return str(name).split("|")[-1].split(":")[-1]


def _long(name: str) -> str:
    found = cmds.ls(name, long=True) or []
    if not found:
        raise RuntimeError("找不到节点: %s" % name)
    return found[0]


def _shape(transform: str) -> str:
    shapes = cmds.listRelatives(transform, shapes=True, noIntermediate=True, fullPath=True) or []
    meshes = [shape for shape in shapes if cmds.nodeType(shape) == "mesh"]
    if len(meshes) != 1:
        raise RuntimeError("无法唯一解析 mesh shape: %s -> %s" % (transform, meshes))
    return meshes[0]


def _dag(node: str) -> om2.MDagPath:
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDagPath(0)


def _node(node: str) -> om2.MObject:
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDependNode(0)


def _points(mesh: str) -> np.ndarray:
    fn = om2.MFnMesh(_dag(_shape(mesh)))
    pts = fn.getPoints(om2.MSpace.kWorld)
    return np.asarray([[p.x, p.y, p.z] for p in pts], dtype=np.float64)


def _topology(mesh: str) -> tuple[list[np.ndarray], np.ndarray, np.ndarray, np.ndarray]:
    fn = om2.MFnMesh(_dag(_shape(mesh)))
    counts, flat = fn.getVertices()
    counts = np.asarray(counts, dtype=np.int64)
    flat = np.asarray(flat, dtype=np.int64)
    offsets = np.zeros((len(counts) + 1,), dtype=np.int64)
    offsets[1:] = np.cumsum(counts)
    faces = [flat[offsets[i] : offsets[i + 1]].astype(np.int32) for i in range(len(counts))]
    tris = []
    tri_faces = []
    edges = set()
    for face_id, face in enumerate(faces):
        n = len(face)
        for i in range(n):
            a = int(face[i])
            b = int(face[(i + 1) % n])
            if a != b:
                edges.add((min(a, b), max(a, b)))
        if n >= 3:
            root = int(face[0])
            for i in range(1, n - 1):
                tris.append((root, int(face[i]), int(face[i + 1])))
                tri_faces.append(face_id)
    return faces, np.asarray(tris, dtype=np.int32), np.asarray(tri_faces, dtype=np.int32), np.asarray(sorted(edges), dtype=np.int32)


def _set_attr(attr: str, value: float) -> bool:
    if not cmds.objExists(attr):
        return False
    try:
        if cmds.getAttr(attr, settable=True):
            cmds.setAttr(attr, float(value))
            return True
    except Exception:
        return False
    return False


def _all_pose_attrs() -> list[str]:
    attrs = set()
    for _name, pose in POSES:
        attrs.update(pose.keys())
    return sorted(attrs)


def _capture_attrs(attrs: list[str]) -> dict[str, float]:
    out = {}
    for attr in attrs:
        if cmds.objExists(attr):
            try:
                out[attr] = float(cmds.getAttr(attr))
            except Exception:
                pass
    return out


def _restore_attrs(values: dict[str, float]) -> None:
    for attr, value in values.items():
        _set_attr(attr, value)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)


def _reset_attrs(attrs: list[str]) -> None:
    for attr in attrs:
        _set_attr(attr, 0.0)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)


def _apply_pose(pose: dict[str, float]) -> dict[str, float]:
    applied = {}
    for attr, value in pose.items():
        if _set_attr(attr, value):
            applied[attr] = float(value)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)
    return applied


def _disable_source_blendshapes() -> list[tuple[str, float]]:
    history = cmds.listHistory(_shape(SOURCE), pruneDagObjects=True) or []
    nodes = cmds.ls(history, type="blendShape") or []
    old = []
    for node in nodes:
        plug = node + ".envelope"
        if cmds.objExists(plug):
            old.append((plug, float(cmds.getAttr(plug))))
            cmds.setAttr(plug, 0.0)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)
    return old


def _restore_plugs(values: list[tuple[str, float]]) -> None:
    for plug, value in values:
        if cmds.objExists(plug):
            cmds.setAttr(plug, float(value))
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)


def _skin_cluster(mesh: str) -> str:
    shape = _shape(mesh)
    skins = cmds.ls(cmds.listHistory(shape, pruneDagObjects=True) or [], type="skinCluster") or []
    if not skins:
        raise RuntimeError("缺少 skinCluster: %s" % mesh)
    return skins[0]


def _skin_info(skin: str):
    fn = oma2.MFnSkinCluster(_node(skin))
    joints = [dag.fullPathName() for dag in fn.influenceObjects()]
    return fn, joints


def _map_source_to_skin(source_joints: list[str], skin_joints: list[str]) -> list[int]:
    full = {name: idx for idx, name in enumerate(skin_joints)}
    leaf = {}
    for idx, name in enumerate(skin_joints):
        leaf.setdefault(_leaf(name), []).append(idx)
    out = []
    for name in source_joints:
        if name in full:
            out.append(full[name])
            continue
        bucket = leaf.get(_leaf(name), [])
        if len(bucket) == 1:
            out.append(bucket[0])
            continue
        raise RuntimeError("joint 映射失败: %s -> %s" % (name, bucket))
    return out


def _read_weights(skin: str, mesh: str, source_joints: list[str]) -> np.ndarray:
    fn, skin_joints = _skin_info(skin)
    source_to_skin = _map_source_to_skin(source_joints, skin_joints)
    count = int(cmds.polyEvaluate(mesh, vertex=True))
    comp_fn = om2.MFnSingleIndexedComponent()
    comp = comp_fn.create(om2.MFn.kMeshVertComponent)
    comp_fn.addElements(list(range(count)))
    flat, influence_count = fn.getWeights(_dag(_shape(mesh)), comp)
    got = np.asarray(flat, dtype=np.float64).reshape(count, int(influence_count))
    return got[:, np.asarray(source_to_skin, dtype=np.int64)]


def _matrix_attr(attr: str) -> om2.MMatrix:
    values = cmds.getAttr(attr)
    if isinstance(values, (list, tuple)) and len(values) == 1 and isinstance(values[0], (list, tuple)):
        values = values[0]
    return om2.MMatrix(values)


def _skin_logical_by_influence(skin: str) -> dict[str, int]:
    fn = oma2.MFnSkinCluster(_node(skin))
    mapping = {}
    for path in fn.influenceObjects():
        logical = int(fn.indexForInfluenceObject(path))
        for key in (path.fullPathName(), path.partialPathName(), _leaf(path.partialPathName())):
            mapping[key] = logical
    return mapping


def _basis_positions(skin: str, influence_names: list[str], target_points: np.ndarray, roi_ids: np.ndarray) -> np.ndarray:
    logical_by_name = _skin_logical_by_influence(skin)
    roi_points = target_points[roi_ids]
    basis = np.zeros((len(roi_ids), len(influence_names), 3), dtype=np.float32)
    missing = []
    for col, raw in enumerate(influence_names):
        logical = logical_by_name.get(str(raw))
        if logical is None:
            logical = logical_by_name.get(_leaf(raw))
        if logical is None:
            missing.append(str(raw))
            continue
        delta = _matrix_attr("%s.bindPreMatrix[%d]" % (skin, logical)) * _matrix_attr("%s.matrix[%d]" % (skin, logical))
        for row, point in enumerate(roi_points):
            p = om2.MPoint(float(point[0]), float(point[1]), float(point[2]), 1.0) * delta
            basis[row, col] = (p.x, p.y, p.z)
    if missing:
        raise RuntimeError("base skin 缺少影响骨骼: %s" % missing[:8])
    return basis


def _expected_points(data: dict, source_points: np.ndarray) -> np.ndarray:
    source_tris = np.asarray(data["source_tris"], dtype=np.int64)
    best_tri = np.asarray(data["best_tri"], dtype=np.int64)
    best_bary = np.asarray(data["best_bary"], dtype=np.float64)
    valid = best_tri >= 0
    safe_tri = best_tri.copy()
    safe_tri[~valid] = 0
    tris = source_tris[safe_tri]
    out = (
        best_bary[:, 0:1] * source_points[tris[:, 0]]
        + best_bary[:, 1:2] * source_points[tris[:, 1]]
        + best_bary[:, 2:3] * source_points[tris[:, 2]]
    )
    out[~valid] = 0.0
    return out


def _select_patch_vertices(tris: np.ndarray, edges: np.ndarray, supported: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    surface = np.load(str(V090_SURFACE), allow_pickle=True)
    pose_names = sorted({key.split("__")[0] for key in surface.files if key.endswith("__%s__tri_score" % BASE_KEY)})
    base_tri = np.stack([surface["%s__%s__tri_score" % (pose, BASE_KEY)] for pose in pose_names]).astype(np.float64)
    max_score = np.nanmax(base_tri, axis=0)
    tri_supported = np.all(supported[tris], axis=1)
    score = np.where(tri_supported & np.isfinite(max_score), max_score, -1.0)
    order = np.argsort(score)[::-1]
    selected_tris = []
    selected_vertices = set()
    for tri_id in order.tolist():
        if score[tri_id] <= 0:
            break
        selected_tris.append(int(tri_id))
        selected_vertices.update(int(v) for v in tris[tri_id].tolist())
        if len(selected_tris) >= CORE_TRI_COUNT or len(selected_vertices) >= MAX_PATCH_VERTICES:
            break

    core_vertex_mask = np.zeros((supported.shape[0],), dtype=bool)
    core_vertex_mask[np.asarray(sorted(selected_vertices), dtype=np.int32)] = True
    patch_vertex_mask = core_vertex_mask.copy()
    for a, b in edges.tolist():
        if core_vertex_mask[int(a)] or core_vertex_mask[int(b)]:
            patch_vertex_mask[int(a)] = True
            patch_vertex_mask[int(b)] = True
    patch_vertex_mask &= supported
    roi_ids = np.where(patch_vertex_mask)[0].astype(np.int32)
    if len(roi_ids) > MAX_PATCH_VERTICES:
        local_score = np.zeros((supported.shape[0],), dtype=np.float64)
        for tri_id in selected_tris:
            for vid in tris[int(tri_id)].tolist():
                local_score[int(vid)] = max(local_score[int(vid)], float(score[int(tri_id)]))
        for a, b in edges.tolist():
            local_score[int(a)] = max(local_score[int(a)], local_score[int(b)] * 0.92)
            local_score[int(b)] = max(local_score[int(b)], local_score[int(a)] * 0.92)
        roi_ids = roi_ids[np.argsort(local_score[roi_ids])[::-1][:MAX_PATCH_VERTICES]]
        roi_ids.sort()
        patch_vertex_mask[:] = False
        patch_vertex_mask[roi_ids] = True
    core_ids = np.where(core_vertex_mask & patch_vertex_mask)[0].astype(np.int32)
    selected_tri_mask = np.all(patch_vertex_mask[tris], axis=1)
    return roi_ids, core_ids, np.where(selected_tri_mask)[0].astype(np.int32)


def _execute() -> dict:
    if str(cmds.file(q=True, sceneName=True)).replace("\\", "/") != str(INPUT_SCENE).replace("\\", "/"):
        cmds.file(str(INPUT_SCENE), open=True, force=True)
    INFO_DIR.mkdir(parents=True, exist_ok=True)

    scene = np.load(str(SCENE_DATA), allow_pickle=True)
    weight_data = np.load(str(V089B_WEIGHTS), allow_pickle=True)
    topo = np.load(str(V090_CANDIDATES), allow_pickle=True)

    influence_names = [str(x) for x in weight_data["influence_names"].tolist()]
    supported = np.asarray(weight_data["supported"], dtype=bool)
    base_mesh = _long(BASE_MESH)
    base_skin = _skin_cluster(base_mesh)
    faces, tris, tri_faces, edges = _topology(base_mesh)
    roi_ids, core_ids, tri_ids = _select_patch_vertices(tris, edges, supported)
    edge_mask = patch_vertex_mask = np.zeros((supported.shape[0],), dtype=bool)
    patch_vertex_mask[roi_ids] = True
    local_edge_mask = patch_vertex_mask[edges[:, 0]] & patch_vertex_mask[edges[:, 1]]
    local_tri_mask = patch_vertex_mask[tris[:, 0]] & patch_vertex_mask[tris[:, 1]] & patch_vertex_mask[tris[:, 2]]

    attrs = _all_pose_attrs()
    old_attrs = _capture_attrs(attrs)
    old_bs = _disable_source_blendshapes()
    try:
        _reset_attrs(attrs)
        source_neutral = _points(SOURCE)
        target_neutral = _points(base_mesh)
        expected_neutral = _expected_points(scene, source_neutral)[roi_ids]
        base_weights = _read_weights(base_skin, base_mesh, influence_names)

        basis_by_pose = []
        expected_by_pose = []
        applied_poses = []
        for pose_name, pose in POSES:
            _reset_attrs(attrs)
            applied = _apply_pose(pose)
            if not applied:
                continue
            source_pose = _points(SOURCE)
            expected_by_pose.append(_expected_points(scene, source_pose)[roi_ids].astype(np.float32))
            basis_by_pose.append(_basis_positions(base_skin, influence_names, target_neutral, roi_ids))
            applied_poses.append(pose_name)
        _reset_attrs(attrs)
    finally:
        _restore_plugs(old_bs)
        _restore_attrs(old_attrs)

    if not basis_by_pose:
        raise RuntimeError("没有成功采样任何姿态")

    # 局部拓扑转本地索引
    global_to_local = {int(vid): i for i, vid in enumerate(roi_ids.tolist())}
    local_edges = []
    for edge in edges[local_edge_mask].tolist():
        local_edges.append((global_to_local[int(edge[0])], global_to_local[int(edge[1])]))
    local_tris = []
    local_tri_faces = []
    for tri_id in np.where(local_tri_mask)[0].astype(np.int32).tolist():
        tri = tris[int(tri_id)]
        local_tris.append((global_to_local[int(tri[0])], global_to_local[int(tri[1])], global_to_local[int(tri[2])]))
        local_tri_faces.append(int(tri_faces[int(tri_id)]))

    expected_weights = (
        scene["best_bary"][roi_ids, 0:1] * scene["source_weights"][scene["source_tris"][scene["best_tri"][roi_ids], 0]]
        + scene["best_bary"][roi_ids, 1:2] * scene["source_weights"][scene["source_tris"][scene["best_tri"][roi_ids], 1]]
        + scene["best_bary"][roi_ids, 2:3] * scene["source_weights"][scene["source_tris"][scene["best_tri"][roi_ids], 2]]
    )

    np.savez_compressed(
        str(OUT_NPZ),
        influence_names=np.asarray(influence_names).astype(str),
        pose_names=np.asarray(applied_poses).astype(str),
        roi_ids=roi_ids.astype(np.int32),
        core_ids=core_ids.astype(np.int32),
        local_edges=np.asarray(local_edges, dtype=np.int32),
        local_tris=np.asarray(local_tris, dtype=np.int32),
        local_tri_faces=np.asarray(local_tri_faces, dtype=np.int32),
        target_neutral_roi=target_neutral[roi_ids].astype(np.float32),
        expected_neutral_roi=expected_neutral.astype(np.float32),
        expected_pose_roi=np.stack(expected_by_pose).astype(np.float32),
        basis_pose_roi=np.stack(basis_by_pose).astype(np.float32),
        base_weights=base_weights.astype(np.float32),
        base_weights_roi=base_weights[roi_ids].astype(np.float32),
        expected_weights_roi=expected_weights.astype(np.float32),
        supported=supported.astype(bool),
        selected_tri_ids=np.asarray(tri_ids, dtype=np.int32),
        selected_face_ids=np.asarray(sorted(set(local_tri_faces)), dtype=np.int32),
    )

    summary = {
        "status": "SUCCESS",
        "scene": cmds.file(q=True, sceneName=True),
        "output_npz": str(OUT_NPZ),
        "pose_count": len(applied_poses),
        "roi_vertices": int(len(roi_ids)),
        "core_vertices": int(len(core_ids)),
        "local_edges": int(len(local_edges)),
        "local_tris": int(len(local_tris)),
        "selected_faces": int(len(set(local_tri_faces))),
        "base_skin": base_skin,
        "note": "v091 输入采自真实 Maya DG；求解器必须使用 vertex/edge/area/normal 目标。",
    }
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


try:
    result = _execute()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
