"""把 v087 全 influence 候选写成 Maya 对比 mesh。

只保存新对比场景，不覆盖 test.ma。候选绑定到同一套 rig，
用户可以自行拉任意 Jaw/Cheek/Lid/Head 控制器检查。
"""

from __future__ import annotations

import json
from pathlib import Path
import traceback

import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import maya.cmds as cmds
import numpy as np


TARGET = "A"
TEST_SCENE = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\test.ma")
PROJECT_DIR = TEST_SCENE.parent
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
CANDIDATE_NPZ = INFO_DIR / "v087_all_influence_candidates.npz"
REPORT_JSON = INFO_DIR / "v087_maya_apply_report.json"
OUTPUT_SCENE = PROJECT_DIR / "test_v087_A_allInfluence_compare.ma"

VARIANTS = [
    ("A_V087_001_baseHybrid", "weights__v087_base_hybrid_m0010", -240.0, (0.12, 0.28, 1.0)),
    ("A_V087_002_rawRT_DIAGNOSTIC", "weights__v087_rt_raw_topk", -120.0, (0.55, 0.2, 0.9)),
    ("A_V087_003_guardStrictRT", "weights__v087_rt_guard_strict", 0.0, (0.1, 0.65, 0.22)),
    ("A_V087_004_guardBalancedRT", "weights__v087_rt_guard_balanced", 120.0, (1.0, 0.08, 0.85)),
    ("A_V087_005_guardBalancedRTS", "weights__v087_rts_guard_balanced", 240.0, (0.95, 0.45, 0.12)),
]


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


def _leaf(name: str) -> str:
    return str(name).split("|")[-1].split(":")[-1]


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


def _unlock_transform(transform: str) -> None:
    for attr in (
        "translateX",
        "translateY",
        "translateZ",
        "rotateX",
        "rotateY",
        "rotateZ",
        "scaleX",
        "scaleY",
        "scaleZ",
        "visibility",
    ):
        plug = transform + "." + attr
        if cmds.objExists(plug):
            try:
                cmds.setAttr(plug, lock=False)
            except Exception:
                pass


def _delete_existing() -> None:
    for name, _key, _offset, _color in VARIANTS:
        found = cmds.ls(name, type="transform", long=True) or []
        if found:
            cmds.delete(found)
        skin = name + "_skinCluster"
        if cmds.objExists(skin):
            cmds.delete(skin)
        group = name + "_GRP"
        if cmds.objExists(group):
            cmds.delete(group)


def _create_material(name: str, color: tuple[float, float, float]) -> str:
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
    vertex_count, _source_joint_count = weights.shape
    influence_count = len(skin_joints)
    influence_indices = om2.MIntArray(list(range(influence_count)))
    shape_dag = _dag(shape)
    cmds.setAttr(skin + ".normalizeWeights", 0)
    cmds.setAttr(skin + ".maintainMaxInfluences", 0)
    chunk = 256
    for start in range(0, vertex_count, chunk):
        end = min(vertex_count, start + chunk)
        flat = [0.0] * ((end - start) * influence_count)
        for local, vertex_index in enumerate(range(start, end)):
            row = weights[vertex_index]
            offset = local * influence_count
            nz = np.where(row > 1e-10)[0]
            for source_joint_idx in nz.tolist():
                flat[offset + source_to_skin[int(source_joint_idx)]] = float(row[int(source_joint_idx)])
        comp_fn = om2.MFnSingleIndexedComponent()
        comp = comp_fn.create(om2.MFn.kMeshVertComponent)
        comp_fn.addElements(list(range(start, end)))
        fn.setWeights(shape_dag, comp, influence_indices, om2.MDoubleArray(flat), False)
    cmds.setAttr(skin + ".normalizeWeights", 1)
    cmds.skinCluster(skin, edit=True, forceNormalizeWeights=True)


def _read_weights(skin: str, shape: str, source_joints: list[str]) -> np.ndarray:
    fn, skin_joints = _skin_info(skin)
    source_to_skin = _map_source_to_skin(source_joints, skin_joints)
    vertex_count = int(cmds.polyEvaluate(shape, vertex=True))
    comp_fn = om2.MFnSingleIndexedComponent()
    comp = comp_fn.create(om2.MFn.kMeshVertComponent)
    comp_fn.addElements(list(range(vertex_count)))
    flat, influence_count = fn.getWeights(_dag(shape), comp)
    got = np.asarray(flat, dtype=np.float64).reshape(vertex_count, int(influence_count))
    return got[:, np.asarray(source_to_skin, dtype=np.int64)]


def _execute() -> dict:
    cmds.file(str(TEST_SCENE), open=True, force=True)
    data = np.load(str(CANDIDATE_NPZ), allow_pickle=True)
    source_joints = [str(x) for x in data["influence_names"].tolist()]
    target = _long(TARGET)
    INFO_DIR.mkdir(parents=True, exist_ok=True)

    cmds.undoInfo(openChunk=True, chunkName="v087 all influence apply")
    created = []
    try:
        _delete_existing()
        for name, key, offset_x, color in VARIANTS:
            dup = cmds.duplicate(target, inputConnections=False, name=name)[0]
            dup = cmds.ls(dup, long=True)[0]
            _unlock_transform(dup)
            try:
                cmds.delete(dup, constructionHistory=True)
            except Exception:
                pass
            shape = _shape(dup)
            sg = _create_material(name, color)
            try:
                cmds.sets(shape, edit=True, forceElement=sg)
            except Exception:
                pass
            skin = cmds.skinCluster(
                source_joints,
                dup,
                toSelectedBones=True,
                maximumInfluences=len(source_joints),
                obeyMaxInfluences=False,
                normalizeWeights=1,
                name=name + "_skinCluster",
            )[0]
            cmds.setAttr(skin + ".maintainMaxInfluences", 0)
            weights = np.asarray(data[key], dtype=np.float64)
            _set_weights(skin, shape, weights, source_joints)
            _unlock_transform(dup)
            cmds.setAttr(dup + ".translateX", float(offset_x))
            got = _read_weights(skin, shape, source_joints)
            diff = np.abs(got - weights)
            created.append(
                {
                    "name": name,
                    "key": key,
                    "transform": dup,
                    "shape": shape,
                    "skin": skin,
                    "row_l1_max": float(np.max(np.sum(diff, axis=1))),
                    "abs_max": float(np.max(diff)),
                    "offset_x": float(offset_x),
                }
            )
        cmds.select([item["transform"] for item in created], replace=True)
        cmds.file(rename=str(OUTPUT_SCENE))
        cmds.file(save=True, type="mayaAscii", force=True)
    finally:
        cmds.undoInfo(closeChunk=True)

    report = {
        "status": "SUCCESS",
        "scene": cmds.file(q=True, sceneName=True),
        "output_scene": str(OUTPUT_SCENE),
        "candidate_npz": str(CANDIDATE_NPZ),
        "created": created,
        "note": "场景保持 neutral，用户可手动拉任意控制器验证所有 influence 权重。",
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


try:
    result = _execute()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
