from __future__ import annotations

import json
from pathlib import Path

import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import maya.cmds as cmds
import numpy as np


INFO_DIR = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\.info\a_weight_transfer_v082_reboot"
)
SOURCE_DATA = INFO_DIR / "v082_clean_scene_data.npz"
VALIDATION_PATH = INFO_DIR / "param_sweep" / "v082p00_distance_k64_correspondence_validation.npz"
BASE_WEIGHTS_PATH = INFO_DIR / "v082_weight_candidates_hybrid_motion_gate.npz"
V083_MASK_PATH = INFO_DIR / "v083_weight_candidates_inpaint.npz"
INFLUENCE_JSON_PATH = INFO_DIR / "v082_weight_candidates_influences.json"
OUT_PATH = INFO_DIR / "v084_inverse_input.npz"
SUMMARY_PATH = INFO_DIR / "v084_inverse_input_summary.json"

SOURCE = "M_Head_base"
TARGET = "A"
TEMP_MESH = "A_V084_TMP_inverseInput"
TEMP_SKIN = "A_V084_TMP_inverseInput_skinCluster"


def _leaf(path):
    return str(path).split("|")[-1].split(":")[-1]


def _shape(mesh):
    shapes = cmds.listRelatives(mesh, shapes=True, noIntermediate=True, fullPath=True) or []
    if not shapes:
        shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True) or []
    if not shapes:
        raise RuntimeError("No mesh shape found under %s" % mesh)
    return shapes[0]


def _dag_path(node):
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDagPath(0)


def _node(node):
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDependNode(0)


def _get_points(mesh):
    fn = om2.MFnMesh(_dag_path(_shape(mesh)))
    pts = fn.getPoints(om2.MSpace.kWorld)
    return np.asarray([[p.x, p.y, p.z] for p in pts], dtype=np.float64)


def _set_jaw(value):
    if cmds.objExists("M_Jaw_A_ctrl.rotateX"):
        cmds.setAttr("M_Jaw_A_ctrl.rotateX", float(value))
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)


def _resolve_influence_names(influence_names):
    resolved = []
    missing = []
    for raw in influence_names:
        raw = str(raw)
        if cmds.objExists(raw):
            resolved.append(raw)
            continue
        hits = cmds.ls(_leaf(raw), long=True) or []
        if hits:
            resolved.append(hits[0])
        else:
            missing.append(raw)
    if missing:
        raise RuntimeError("Missing influences: %s" % missing[:8])
    return resolved


def _component(count):
    comp_fn = om2.MFnSingleIndexedComponent()
    comp = comp_fn.create(om2.MFn.kMeshVertComponent)
    comp_fn.addElements(om2.MIntArray(list(range(count))))
    return comp


def _apply_weights(mesh, skin_name, influence_names, weights):
    resolved = _resolve_influence_names(influence_names)
    if cmds.objExists(skin_name):
        cmds.delete(skin_name)
    skin = cmds.skinCluster(
        resolved,
        mesh,
        toSelectedBones=True,
        bindMethod=0,
        skinMethod=0,
        normalizeWeights=1,
        maximumInfluences=12,
        obeyMaxInfluences=False,
        name=skin_name,
    )[0]
    cmds.setAttr(skin + ".normalizeWeights", 1)

    fn_skin = oma2.MFnSkinCluster(_node(skin))
    mesh_dag = _dag_path(_shape(mesh))
    name_to_logical = {}
    for path in fn_skin.influenceObjects():
        logical = int(fn_skin.indexForInfluenceObject(path))
        for key in (path.fullPathName(), path.partialPathName(), _leaf(path.partialPathName())):
            name_to_logical[key] = logical

    logical_indices = []
    cols = []
    for col, raw in enumerate(influence_names):
        logical = name_to_logical.get(str(raw))
        if logical is None:
            logical = name_to_logical.get(_leaf(raw))
        if logical is not None:
            logical_indices.append(int(logical))
            cols.append(col)
    if not cols:
        raise RuntimeError("No influence columns matched %s" % skin)

    sub = np.asarray(weights[:, cols], dtype=np.float64)
    row_sum = sub.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0.0] = 1.0
    sub = sub / row_sum
    comp = _component(int(cmds.polyEvaluate(mesh, vertex=True)))
    flat = om2.MDoubleArray(sub.ravel(order="C").tolist())
    fn_skin.setWeights(mesh_dag, comp, om2.MIntArray(logical_indices), flat, True)
    return skin


def _duplicate_temp():
    if cmds.objExists(TEMP_MESH):
        cmds.delete(TEMP_MESH)
    dup = cmds.duplicate(TARGET, returnRootsOnly=True, name=TEMP_MESH)[0]
    try:
        cmds.delete(dup, constructionHistory=True)
    except Exception:
        pass
    for attr, value in (("translateX", 0), ("translateY", 0), ("translateZ", 0), ("rotateX", 0), ("rotateY", 0), ("rotateZ", 0), ("scaleX", 1), ("scaleY", 1), ("scaleZ", 1)):
        try:
            cmds.setAttr(dup + "." + attr, value)
        except Exception:
            pass
    return dup


def _expected_source_positions(val, source_points, ids):
    source_tris = val["source_tris"].astype(np.int32)
    best_tri = val["best_tri"].astype(np.int32)
    best_bary = val["best_bary"].astype(np.float64)
    tris = source_tris[best_tri[ids]]
    bary = best_bary[ids]
    return (
        bary[:, 0:1] * source_points[tris[:, 0]]
        + bary[:, 1:2] * source_points[tris[:, 1]]
        + bary[:, 2:3] * source_points[tris[:, 2]]
    )


def _matrix_attr(attr):
    values = cmds.getAttr(attr)
    if isinstance(values, (list, tuple)) and len(values) == 1 and isinstance(values[0], (list, tuple)):
        values = values[0]
    return om2.MMatrix(values)


def _skin_logical_by_influence(skin):
    fn_skin = oma2.MFnSkinCluster(_node(skin))
    mapping = {}
    for path in fn_skin.influenceObjects():
        logical = int(fn_skin.indexForInfluenceObject(path))
        for key in (path.fullPathName(), path.partialPathName(), _leaf(path.partialPathName())):
            mapping[key] = logical
    return mapping


def _basis_positions(skin, influence_names, target_points, roi_ids):
    logical_by_name = _skin_logical_by_influence(skin)
    roi_points = target_points[roi_ids]
    basis = np.zeros((len(roi_ids), len(influence_names), 3), dtype=np.float64)
    missing = []
    for col, raw in enumerate(influence_names):
        logical = logical_by_name.get(str(raw))
        if logical is None:
            logical = logical_by_name.get(_leaf(raw))
        if logical is None:
            missing.append(str(raw))
            continue
        bind_pre = _matrix_attr("%s.bindPreMatrix[%d]" % (skin, logical))
        matrix = _matrix_attr("%s.matrix[%d]" % (skin, logical))
        delta = bind_pre * matrix
        for row, point in enumerate(roi_points):
            p = om2.MPoint(float(point[0]), float(point[1]), float(point[2]), 1.0) * delta
            basis[row, col] = (p.x, p.y, p.z)
    if missing:
        raise RuntimeError("Missing logical influences on temp skin: %s" % missing[:8])
    return basis


def _stat(values):
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return {"count": 0}
    return {
        "count": int(values.size),
        "mean": float(np.mean(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(np.max(values)),
    }


def main():
    data = np.load(str(SOURCE_DATA), allow_pickle=True)
    val = np.load(str(VALIDATION_PATH), allow_pickle=True)
    base_weights = np.load(str(BASE_WEIGHTS_PATH), allow_pickle=True)["hybrid_m0010_cc3"].astype(np.float64)
    masks = np.load(str(V083_MASK_PATH), allow_pickle=True)
    influence_names = json.loads(INFLUENCE_JSON_PATH.read_text(encoding="utf-8"))["influence_names"]

    domain = masks["v083_inpaint_a025_domain_mask"].astype(bool)
    core = masks["v083_inpaint_a025_core_mask"].astype(bool)
    roi_ids = np.where(domain)[0].astype(np.int32)
    core_ids = np.where(core)[0].astype(np.int32)

    _set_jaw(0.0)
    source_neutral = _get_points(SOURCE)
    target_neutral = _get_points(TARGET)
    temp = _duplicate_temp()
    temp_skin = _apply_weights(temp, TEMP_SKIN, influence_names, base_weights)

    _set_jaw(25.0)
    source_jaw = _get_points(SOURCE)
    temp_actual_jaw = _get_points(temp)
    expected_neutral = _expected_source_positions(val, source_neutral, roi_ids)
    expected_jaw = _expected_source_positions(val, source_jaw, roi_ids)
    desired_jaw = target_neutral[roi_ids] + (expected_jaw - expected_neutral)
    basis = _basis_positions(temp_skin, influence_names, target_neutral, roi_ids)
    predicted = np.einsum("ri,rij->rj", base_weights[roi_ids], basis)
    formula_error = np.linalg.norm(predicted - temp_actual_jaw[roi_ids], axis=1)
    base_motion_error = np.linalg.norm(
        (temp_actual_jaw[roi_ids] - target_neutral[roi_ids]) - (expected_jaw - expected_neutral),
        axis=1,
    )

    payload = {
        "roi_ids": roi_ids,
        "core_ids": core_ids,
        "target_neutral_roi": target_neutral[roi_ids].astype(np.float64),
        "desired_jaw_roi": desired_jaw.astype(np.float64),
        "source_expected_neutral_roi": expected_neutral.astype(np.float64),
        "source_expected_jaw_roi": expected_jaw.astype(np.float64),
        "basis_jaw25_roi": basis.astype(np.float32),
        "base_weights": base_weights.astype(np.float32),
        "base_weights_roi": base_weights[roi_ids].astype(np.float32),
        "base_actual_jaw_roi": temp_actual_jaw[roi_ids].astype(np.float64),
        "base_motion_error_roi": base_motion_error.astype(np.float64),
        "formula_error_roi": formula_error.astype(np.float64),
        "influence_names": np.asarray(influence_names, dtype=object),
    }
    np.savez_compressed(str(OUT_PATH), **payload)
    summary = {
        "status": "SUCCESS",
        "scene": cmds.file(q=True, sn=True),
        "output_path": str(OUT_PATH),
        "roi_vertices": int(len(roi_ids)),
        "core_vertices": int(len(core_ids)),
        "influence_count": int(len(influence_names)),
        "formula_error": _stat(formula_error),
        "base_motion_error": _stat(base_motion_error),
        "note": "basis uses MPoint * bindPreMatrix * matrix from a temporary ungrouped skinCluster bound at neutral.",
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        cmds.delete(temp)
    except Exception:
        pass
    return summary


result = main()
