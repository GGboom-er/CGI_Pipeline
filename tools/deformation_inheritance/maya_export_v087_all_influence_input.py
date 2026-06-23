"""导出 v087 全 influence 反求权重输入。

口径：干净 test.ma，source=M_Head_base，target=A。
本脚本只保存求解所需数据，不保存 Maya 场景。
"""

from __future__ import annotations

import json
from pathlib import Path
import traceback

import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import maya.cmds as cmds
import numpy as np


SOURCE = "M_Head_base"
TARGET = "A"
TEST_SCENE = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\test.ma")
PROJECT_DIR = TEST_SCENE.parent
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
CORR_NPZ = INFO_DIR / "v082_correspondence_validation.npz"
BASE_WEIGHTS_NPZ = INFO_DIR / "v082_weight_candidates_hybrid_motion_gate.npz"
BASE_KEY = "hybrid_m0010_cc3"
OUT_NPZ = INFO_DIR / "v087_all_influence_input.npz"
OUT_JSON = INFO_DIR / "v087_all_influence_input_summary.json"
TEMP_TARGET_SKIN = "A_V087_temp_matrix_skinCluster"


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


def _find_skin(transform: str) -> str | None:
    shape = _shape(transform)
    skins = cmds.ls(cmds.listHistory(shape, pruneDagObjects=True) or [], type="skinCluster") or []
    return skins[0] if skins else None


def _points_world(shape: str) -> np.ndarray:
    fn = om2.MFnMesh(_dag(shape))
    return np.asarray([[p.x, p.y, p.z] for p in fn.getPoints(om2.MSpace.kWorld)], dtype=np.float64)


def _as_matrix(value) -> np.ndarray:
    if isinstance(value, (list, tuple)) and len(value) == 16:
        vals = value
    elif isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)) and len(value[0]) == 16:
        vals = value[0]
    else:
        raise RuntimeError("无法解析矩阵: %s" % (value,))
    return np.asarray([float(x) for x in vals], dtype=np.float64).reshape(4, 4)


def _skin_info(skin: str):
    fn = oma2.MFnSkinCluster(_node(skin))
    joints = [dag.fullPathName() for dag in fn.influenceObjects()]
    return fn, joints


def _leaf(name: str) -> str:
    return str(name).split("|")[-1].split(":")[-1]


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


def _all_vertices(vertex_count: int) -> om2.MObject:
    comp_fn = om2.MFnSingleIndexedComponent()
    comp = comp_fn.create(om2.MFn.kMeshVertComponent)
    comp_fn.addElements(om2.MIntArray(list(range(vertex_count))))
    return comp


def _read_weights(skin: str, shape: str, source_joints: list[str]) -> np.ndarray:
    fn, skin_joints = _skin_info(skin)
    source_to_skin = _map_source_to_skin(source_joints, skin_joints)
    vertex_count = int(cmds.polyEvaluate(shape, vertex=True))
    flat, influence_count = fn.getWeights(_dag(shape), _all_vertices(vertex_count))
    got = np.asarray(flat, dtype=np.float64).reshape(vertex_count, int(influence_count))
    return got[:, np.asarray(source_to_skin, dtype=np.int64)]


def _skin_matrices(skin: str, source_joints: list[str]) -> tuple[np.ndarray, np.ndarray]:
    fn, skin_joints = _skin_info(skin)
    source_to_skin = _map_source_to_skin(source_joints, skin_joints)
    matrices = []
    pivots = []
    for skin_idx in source_to_skin:
        joint = skin_joints[int(skin_idx)]
        logical_index = int(fn.indexForInfluenceObject(_dag(joint)))
        bind_pre = _as_matrix(cmds.getAttr("%s.bindPreMatrix[%d]" % (skin, logical_index)))
        world = _as_matrix(cmds.getAttr("%s.worldMatrix[0]" % joint))
        matrices.append(bind_pre @ world)
        pivots.append(world[3, :3].copy())
    return np.asarray(matrices, dtype=np.float64), np.asarray(pivots, dtype=np.float64)


def _set_envelope(skin: str | None, value: float) -> float | None:
    if not skin or not cmds.objExists(skin + ".envelope"):
        return None
    old = float(cmds.getAttr(skin + ".envelope"))
    cmds.setAttr(skin + ".envelope", float(value))
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)
    return old


def _disable_blendshapes(transform: str) -> list[tuple[str, float]]:
    shape = _shape(transform)
    blendshapes = cmds.ls(cmds.listHistory(shape, pruneDagObjects=True) or [], type="blendShape") or []
    old = []
    for node in blendshapes:
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


def _create_temp_target_skin(target: str, source_joints: list[str]) -> str:
    if cmds.objExists(TEMP_TARGET_SKIN):
        cmds.delete(TEMP_TARGET_SKIN)
    skin = cmds.skinCluster(
        source_joints,
        target,
        toSelectedBones=True,
        maximumInfluences=len(source_joints),
        obeyMaxInfluences=False,
        normalizeWeights=1,
        name=TEMP_TARGET_SKIN,
    )[0]
    cmds.setAttr(skin + ".maintainMaxInfluences", 0)
    cmds.setAttr(skin + ".envelope", 0.0)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)
    return skin


def _execute() -> dict:
    if str(cmds.file(q=True, sceneName=True)).replace("\\", "/") != str(TEST_SCENE).replace("\\", "/"):
        cmds.file(str(TEST_SCENE), open=True, force=True)

    INFO_DIR.mkdir(parents=True, exist_ok=True)
    source = _long(SOURCE)
    target = _long(TARGET)
    source_shape = _shape(source)
    target_shape = _shape(target)
    source_skin = _find_skin(source)
    if not source_skin:
        raise RuntimeError("source 缺少 skinCluster: %s" % source)

    fn, source_joints = _skin_info(source_skin)
    old_bs = _disable_blendshapes(source)
    old_source_env = _set_envelope(source_skin, 0.0)
    try:
        source_points = _points_world(source_shape)
        target_points = _points_world(target_shape)
    finally:
        if old_source_env is not None:
            _set_envelope(source_skin, old_source_env)
        _restore_plugs(old_bs)

    source_weights = _read_weights(source_skin, source_shape, source_joints)
    source_matrices, source_pivots = _skin_matrices(source_skin, source_joints)

    temp_skin = _create_temp_target_skin(target, source_joints)
    target_matrices, target_pivots = _skin_matrices(temp_skin, source_joints)
    if cmds.objExists(temp_skin):
        cmds.delete(temp_skin)

    corr = np.load(str(CORR_NPZ), allow_pickle=True)
    base_data = np.load(str(BASE_WEIGHTS_NPZ), allow_pickle=True)
    base_weights = np.asarray(base_data[BASE_KEY], dtype=np.float64)

    np.savez_compressed(
        str(OUT_NPZ),
        scene=str(TEST_SCENE),
        source=np.asarray(source, dtype=object),
        target=np.asarray(target, dtype=object),
        source_shape=np.asarray(source_shape, dtype=object),
        target_shape=np.asarray(target_shape, dtype=object),
        source_skin=np.asarray(source_skin, dtype=object),
        influence_names=np.asarray(source_joints, dtype=object),
        source_points=source_points,
        target_points=target_points,
        source_weights=source_weights,
        base_weights=base_weights,
        source_matrices=source_matrices,
        target_matrices=target_matrices,
        source_pivots=source_pivots,
        target_pivots=target_pivots,
        source_tris=np.asarray(corr["source_tris"], dtype=np.int64),
        best_tri=np.asarray(corr["best_tri"], dtype=np.int64),
        best_bary=np.asarray(corr["best_bary"], dtype=np.float64),
        best_dist=np.asarray(corr["best_dist"], dtype=np.float64),
        confidence=np.asarray(corr["confidence"], dtype=np.float64),
        unsupported=np.asarray(corr["unsupported"], dtype=bool),
        low_confidence=np.asarray(corr["low_confidence"], dtype=bool),
        high_confidence=np.asarray(corr["high_confidence"], dtype=bool),
        normal_dot=np.asarray(corr["normal_dot"], dtype=np.float64),
        semantic_ambiguous=np.asarray(corr["semantic_ambiguous"], dtype=bool),
        topology_discontinuity=np.asarray(corr["topology_discontinuity"], dtype=bool),
        face_semantic_discontinuity=np.asarray(corr["face_semantic_discontinuity"], dtype=bool),
    )

    summary = {
        "status": "SUCCESS",
        "scene": cmds.file(q=True, sceneName=True),
        "output_npz": str(OUT_NPZ),
        "source_vertex_count": int(source_points.shape[0]),
        "target_vertex_count": int(target_points.shape[0]),
        "influence_count": int(len(source_joints)),
        "supported_count": int(np.sum(~np.asarray(corr["unsupported"], dtype=bool))),
        "high_confidence_count": int(np.sum(np.asarray(corr["high_confidence"], dtype=bool))),
        "base_key": BASE_KEY,
        "base_row_sum_error": float(np.max(np.abs(base_weights.sum(axis=1) - 1.0))),
    }
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


try:
    result = _execute()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
