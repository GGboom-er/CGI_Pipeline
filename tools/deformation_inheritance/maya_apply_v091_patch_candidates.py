# -*- coding: utf-8 -*-
"""把 v091 patch constrained objective 候选写成 Maya 对照 mesh。"""

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
CANDIDATE_NPZ = INFO_DIR / "v091_patch_constrained_objective_candidates.npz"
REPORT_JSON = INFO_DIR / "v091_maya_apply_report.json"
OUTPUT_SCENE = PROJECT_DIR / "test_v091_A_patchConstrained_candidates.ma"

BASE_MESH = "A_V089B_001_baseHybrid"
VARIANTS = [
    ("A_V091_001_surfaceBalanced", "weights__v091_surface_balanced", 1380.0, (0.12, 0.72, 0.95)),
    ("A_V091_002_surfaceVisible", "weights__v091_surface_visible", 1500.0, (0.95, 0.35, 0.10)),
    ("A_V091_003_shapeGuard", "weights__v091_surface_shape_guard", 1620.0, (0.40, 0.90, 0.38)),
]

POSE_ATTRS = [
    "M_Jaw_A_ctrl.rotateX", "M_JawA_A_ctrl.translateY", "M_JawUpA_A_ctrl.translateY",
    "M_Mouth_A_ctrl.translateY", "L_Mouth_A_ctrl.translateX", "R_Mouth_A_ctrl.translateX",
    "R_CheekA_A_ctrl.translateY", "L_CheekA_A_ctrl.translateY",
    "R_CheekB_A_ctrl.translateY", "L_CheekB_A_ctrl.translateY",
    "R_UpCheekMid_A_ctrl.translateY", "L_UpCheekMid_A_ctrl.translateY",
    "R_UpLidMid_A_ctrl.translateY", "R_LoLidMid_A_ctrl.translateY",
    "L_UpLidMid_A_ctrl.translateY", "L_LoLidMid_A_ctrl.translateY",
    "M_UpLip_A_ctrl.translateY", "M_LoLip_A_ctrl.translateY",
    "R_UpLipMain1_A_ctrl.translateY", "L_UpLipMain1_A_ctrl.translateY",
    "R_LoLipMain1_A_ctrl.translateY", "L_LoLipMain1_A_ctrl.translateY",
    "M_NoseTip_A_ctrl.translateZ", "M_Chin_A_ctrl.translateY",
]


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


def _unlock(transform: str) -> None:
    for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ", "visibility"):
        plug = transform + "." + attr
        if cmds.objExists(plug):
            try:
                incoming = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
                for src in incoming:
                    try:
                        cmds.disconnectAttr(src, plug)
                    except Exception:
                        pass
                cmds.setAttr(plug, lock=False)
            except Exception:
                pass


def _reset_pose() -> None:
    for attr in POSE_ATTRS:
        if cmds.objExists(attr):
            try:
                if cmds.getAttr(attr, settable=True):
                    cmds.setAttr(attr, 0.0)
            except Exception:
                pass
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)


def _material(name: str, color: tuple[float, float, float]) -> str:
    mat = name + "_mat"
    if not cmds.objExists(mat):
        mat = cmds.shadingNode("lambert", asShader=True, name=mat)
        cmds.setAttr(mat + ".color", color[0], color[1], color[2], type="double3")
    sg = mat + "SG"
    if not cmds.objExists(sg):
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg)
        cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader", force=True)
    return sg


def _set_weights(skin: str, shape: str, weights: np.ndarray, source_joints: list[str]) -> None:
    fn, skin_joints = _skin_info(skin)
    source_to_skin = _map_source_to_skin(source_joints, skin_joints)
    vertex_count, _cols = weights.shape
    influence_count = len(skin_joints)
    influence_indices = om2.MIntArray(list(range(influence_count)))
    shape_dag = _dag(shape)
    cmds.setAttr(skin + ".normalizeWeights", 0)
    cmds.setAttr(skin + ".maintainMaxInfluences", 0)
    for start in range(0, vertex_count, 256):
        end = min(vertex_count, start + 256)
        flat = [0.0] * ((end - start) * influence_count)
        for local, vertex_index in enumerate(range(start, end)):
            row = weights[vertex_index]
            offset = local * influence_count
            for source_col in np.where(row > 1e-10)[0].astype(np.int64).tolist():
                flat[offset + source_to_skin[source_col]] = float(row[source_col])
        comp_fn = om2.MFnSingleIndexedComponent()
        comp = comp_fn.create(om2.MFn.kMeshVertComponent)
        comp_fn.addElements(list(range(start, end)))
        fn.setWeights(shape_dag, comp, influence_indices, om2.MDoubleArray(flat), False)
    cmds.setAttr(skin + ".normalizeWeights", 1)
    cmds.skinCluster(skin, edit=True, forceNormalizeWeights=True)


def _read_weights(skin: str, shape: str, source_joints: list[str]) -> np.ndarray:
    fn, skin_joints = _skin_info(skin)
    source_to_skin = _map_source_to_skin(source_joints, skin_joints)
    count = int(cmds.polyEvaluate(shape, vertex=True))
    comp_fn = om2.MFnSingleIndexedComponent()
    comp = comp_fn.create(om2.MFn.kMeshVertComponent)
    comp_fn.addElements(list(range(count)))
    flat, influence_count = fn.getWeights(_dag(shape), comp)
    got = np.asarray(flat, dtype=np.float64).reshape(count, int(influence_count))
    return got[:, np.asarray(source_to_skin, dtype=np.int64)]


def _make_set(mesh: str, name: str, mask: np.ndarray) -> dict:
    if cmds.objExists(name):
        cmds.delete(name)
    ids = np.where(np.asarray(mask, dtype=bool))[0].astype(int).tolist()
    if not ids:
        cmds.sets(empty=True, name=name)
        return {"set": name, "count": 0}
    return {"set": cmds.sets(["%s.vtx[%d]" % (mesh, i) for i in ids], name=name), "count": len(ids)}


def _execute() -> dict:
    cmds.file(str(INPUT_SCENE), open=True, force=True)
    data = np.load(str(CANDIDATE_NPZ), allow_pickle=True)
    source_joints = [str(x) for x in data["influence_names"].tolist()]
    base_mesh = _long(BASE_MESH)

    for name, _key, _offset, _color in VARIANTS:
        if cmds.objExists(name):
            cmds.delete(name)
        group = name + "_displayOffset_GRP"
        if cmds.objExists(group):
            cmds.delete(group)
        skin = name + "_skinCluster"
        if cmds.objExists(skin):
            cmds.delete(skin)

    report = {"status": "SUCCESS", "output_scene": str(OUTPUT_SCENE), "variants": {}}
    cmds.undoInfo(openChunk=True, chunkName="v091 patch constrained candidates")
    try:
        _reset_pose()
        for name, key, offset_x, color in VARIANTS:
            dup = cmds.duplicate(base_mesh, inputConnections=False, name=name)[0]
            dup = cmds.ls(dup, long=True)[0]
            _unlock(dup)
            try:
                cmds.delete(dup, constructionHistory=True)
            except Exception:
                pass
            cmds.setAttr(dup + ".translateX", 0.0)
            shape = _shape(dup)
            sg = _material(name, color)
            cmds.sets(shape, edit=True, forceElement=sg)
            skin = cmds.skinCluster(
                source_joints,
                dup,
                toSelectedBones=True,
                bindMethod=0,
                skinMethod=0,
                normalizeWeights=1,
                maximumInfluences=12,
                obeyMaxInfluences=False,
                name=name + "_skinCluster",
            )[0]
            weights = np.asarray(data[key], dtype=np.float64)
            _set_weights(skin, shape, weights, source_joints)
            got = _read_weights(skin, shape, source_joints)
            diff = np.abs(got - weights)
            # 绑定后某些 transform 通道会被锁/连接。展示偏移只用于横向对照，
            # 放到父组上，避免污染 skin bind 和目标 transform 本身。
            group = cmds.group(empty=True, name=name + "_displayOffset_GRP")
            cmds.parent(dup, group)
            cmds.setAttr(group + ".translateX", float(offset_x))
            accept_key = "accept__" + key.replace("weights__", "")
            if accept_key in data:
                report["variants"][name] = _make_set(name, "CDFDIAG_V091_%s_ACCEPT_SET" % name.upper(), data[accept_key])
            report["variants"].setdefault(name, {})
            report["variants"][name].update({
                "weight_key": key,
                "skin": skin,
                "row_l1_max": float(diff.sum(axis=1).max()),
                "abs_max": float(diff.max()),
                "changed_vertices": int(np.count_nonzero(data.get(accept_key, np.zeros((weights.shape[0],), dtype=bool)))) if accept_key in data else 0,
            })
        _reset_pose()
        cmds.file(rename=str(OUTPUT_SCENE))
        cmds.file(save=True, type="mayaAscii")
    finally:
        cmds.undoInfo(closeChunk=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


try:
    result = _execute()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
