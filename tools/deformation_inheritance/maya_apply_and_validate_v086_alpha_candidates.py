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
INFLUENCE_JSON_PATH = INFO_DIR / "v082_weight_candidates_influences.json"
HYBRID_WEIGHTS_PATH = INFO_DIR / "v082_weight_candidates_hybrid_motion_gate.npz"
V084_WEIGHTS_PATH = INFO_DIR / "v084_weight_candidates_constrained_inverse.npz"
V085_WEIGHTS_PATH = INFO_DIR / "v085_weight_candidates_multipose_gate.npz"
V086_WEIGHTS_PATH = INFO_DIR / "v086_weight_candidates_alpha_pose_gate.npz"
VALIDATION_PATH = INFO_DIR / "param_sweep" / "v082p00_distance_k64_correspondence_validation.npz"
REPORT_PATH = INFO_DIR / "v086_multipose_actual_dg_report.json"
ERROR_ARRAYS_PATH = INFO_DIR / "v086_multipose_actual_dg_error_arrays.npz"

POSES = [
    ("jaw_rx_5", {"M_Jaw_A_ctrl.rotateX": 5.0}),
    ("jaw_rx_10", {"M_Jaw_A_ctrl.rotateX": 10.0}),
    ("jaw_rx_15", {"M_Jaw_A_ctrl.rotateX": 15.0}),
    ("jaw_rx_20", {"M_Jaw_A_ctrl.rotateX": 20.0}),
    ("jaw_rx_25", {"M_Jaw_A_ctrl.rotateX": 25.0}),
    ("jaw_rx_30", {"M_Jaw_A_ctrl.rotateX": 30.0}),
    ("r_cheekA_ty_1", {"R_CheekA_A_ctrl.translateY": 1.0}),
    ("l_cheekA_ty_1", {"L_CheekA_A_ctrl.translateY": 1.0}),
    ("r_upLidMid_ty_05", {"R_UpLidMid_A_ctrl.translateY": 0.5}),
    ("r_loLidMid_ty_n05", {"R_LoLidMid_A_ctrl.translateY": -0.5}),
    ("l_upLidMid_ty_05", {"L_UpLidMid_A_ctrl.translateY": 0.5}),
    ("l_loLidMid_ty_n05", {"L_LoLidMid_A_ctrl.translateY": -0.5}),
]

CANDIDATES = [
    ("hybrid_m0010_cc3", "hybrid", "A_V086TEST_000_baseHybrid", -48.0, (0.05, 0.55, 0.2)),
    ("v084_inverse_motion_accepted", "v084", "A_V086TEST_001_v084Motion", -24.0, (1.0, 0.36, 0.04)),
    ("v085_inverse_motion_multiposeGated", "v085", "A_V086TEST_002_v085HardGate", 0.0, (0.15, 0.95, 0.9)),
    ("v086_alphaGate_safe005", "v086", "A_V086TEST_003_alphaSafe005", 24.0, (0.3, 0.45, 1.0)),
    ("v086_alphaGate_strict003", "v086", "A_V086TEST_004_alphaStrict003", 48.0, (0.9, 0.25, 0.95)),
    ("v086_alphaGate_balanced", "v086", "A_V086TEST_005_alphaBalanced", 72.0, (1.0, 0.65, 0.05)),
]


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


def _get_points(mesh):
    fn = om2.MFnMesh(_dag_path(_shape(mesh)))
    pts = fn.getPoints(om2.MSpace.kWorld)
    return np.asarray([[p.x, p.y, p.z] for p in pts], dtype=np.float64)


def _set_attr_safe(attr, value):
    if not cmds.objExists(attr):
        return False
    try:
        if cmds.getAttr(attr, settable=True):
            cmds.setAttr(attr, float(value))
            return True
    except Exception:
        return False
    return False


def _all_pose_attrs():
    attrs = set()
    for _, pose in POSES:
        attrs.update(pose.keys())
    return sorted(attrs)


def _capture_attrs(attrs):
    values = {}
    for attr in attrs:
        if cmds.objExists(attr):
            try:
                values[attr] = float(cmds.getAttr(attr))
            except Exception:
                pass
    return values


def _restore_attrs(values):
    for attr, value in values.items():
        _set_attr_safe(attr, value)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)


def _reset_attrs(attrs):
    active = []
    for attr in attrs:
        if _set_attr_safe(attr, 0.0):
            active.append(attr)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)
    return active


def _apply_pose(pose):
    applied = {}
    for attr, value in pose.items():
        if _set_attr_safe(attr, value):
            applied[attr] = value
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)
    return applied


def _component(count):
    comp_fn = om2.MFnSingleIndexedComponent()
    comp = comp_fn.create(om2.MFn.kMeshVertComponent)
    comp_fn.addElements(om2.MIntArray(list(range(count))))
    return comp


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


def _apply_weights(mesh, skin_name, influence_names, weights):
    resolved_influences = _resolve_influence_names(influence_names)
    if cmds.objExists(skin_name):
        cmds.delete(skin_name)
    skin = cmds.skinCluster(
        resolved_influences,
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
    sel = om2.MSelectionList()
    sel.add(skin)
    fn_skin = oma2.MFnSkinCluster(sel.getDependNode(0))
    mesh_dag = _dag_path(_shape(mesh))

    name_to_logical = {}
    for path in fn_skin.influenceObjects():
        logical = fn_skin.indexForInfluenceObject(path)
        full = path.fullPathName()
        partial = path.partialPathName()
        leaf = _leaf(partial)
        name_to_logical[full] = logical
        name_to_logical[partial] = logical
        name_to_logical[leaf] = logical

    logical_indices = []
    cols = []
    for col, raw in enumerate(influence_names):
        raw = str(raw)
        logical = name_to_logical.get(raw)
        if logical is None:
            logical = name_to_logical.get(_leaf(raw))
        if logical is None:
            continue
        logical_indices.append(int(logical))
        cols.append(col)
    if not cols:
        raise RuntimeError("No influence columns matched skinCluster %s" % skin)

    sub = np.asarray(weights[:, cols], dtype=np.float64)
    row_sum = sub.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0.0] = 1.0
    sub = sub / row_sum
    fn_skin.setWeights(
        mesh_dag,
        _component(int(cmds.polyEvaluate(mesh, vertex=True))),
        om2.MIntArray(logical_indices),
        om2.MDoubleArray(sub.ravel(order="C").tolist()),
        True,
    )
    return skin


def _make_material(name, color):
    if cmds.objExists(name):
        shader = name
    else:
        shader = cmds.shadingNode("lambert", asShader=True, name=name)
        cmds.setAttr(shader + ".color", color[0], color[1], color[2], type="double3")
    sg = shader + "SG"
    if not cmds.objExists(sg):
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg)
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    return sg


def _duplicate_target(name):
    dup = cmds.duplicate("A", returnRootsOnly=True, name=name)[0]
    try:
        cmds.delete(dup, constructionHistory=True)
    except Exception:
        pass
    for attr in ("translateX", "translateY", "translateZ"):
        cmds.setAttr(dup + "." + attr, 0.0)
    return dup


def _delete_existing():
    for _, _, mesh, _, _ in CANDIDATES:
        for name in (mesh, mesh + "_GRP", mesh + "_skinCluster"):
            if cmds.objExists(name):
                cmds.delete(name)


def _expected_points(val, source_points, ids):
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


def _face_range_vertices(data, start_face, end_face):
    counts = data["target_face_counts"].astype(int)
    offsets = data["target_face_offsets"].astype(int)
    flat = data["target_face_vertices"].astype(int)
    vertices = set()
    for face_id in range(start_face, end_face + 1):
        vertices.update(flat[offsets[face_id] : offsets[face_id] + counts[face_id]].tolist())
    return np.asarray(sorted(vertices), dtype=np.int32)


def _groups(source_data, val):
    unsupported = val["unsupported"].astype(bool)
    low = val["low_confidence"].astype(bool)
    high = val["high_confidence"].astype(bool)
    strict = (
        val["normal_mismatch"].astype(bool)
        | val["semantic_ambiguous"].astype(bool)
        | val["topology_discontinuity"].astype(bool)
        | val["face_semantic_discontinuity"].astype(bool)
    ) & (~unsupported)
    groups = {
        "supported": np.where(~unsupported)[0],
        "high_confidence": np.where(high)[0],
        "low_confidence": np.where(low)[0],
        "strict_block": np.where(strict)[0],
        "mouth_red_left_9095_9150": _face_range_vertices(source_data, 9095, 9150),
        "mouth_red_mid_9263_9318": _face_range_vertices(source_data, 9263, 9318),
    }
    for name, ids in list(groups.items()):
        groups[name] = ids[(~unsupported[ids])] if len(ids) else ids
    return groups


def _stat(values):
    if len(values) == 0:
        return {"count": 0}
    return {
        "count": int(len(values)),
        "mean": float(np.mean(values)),
        "p50": float(np.percentile(values, 50)),
        "p75": float(np.percentile(values, 75)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "max": float(np.max(values)),
    }


def _pose_errors(mesh, groups, val, source_neutral, source_pose, target_neutral):
    actual = _get_points(mesh)
    result = {}
    arrays = {}
    for name, ids in groups.items():
        if len(ids) == 0:
            result[name] = {"count": 0}
            arrays[name] = (ids, np.zeros((0,), dtype=np.float64))
            continue
        expected_neutral = _expected_points(val, source_neutral, ids)
        expected_pose = _expected_points(val, source_pose, ids)
        motion_error = np.linalg.norm(
            (actual[ids] - target_neutral[ids]) - (expected_pose - expected_neutral),
            axis=1,
        )
        result[name] = _stat(motion_error)
        arrays[name] = (ids, motion_error)
    return result, arrays


def _make_set(mesh, name, ids):
    if cmds.objExists(name):
        cmds.delete(name)
    if len(ids) == 0:
        return {"count": 0, "set": name}
    comps = ["%s.vtx[%d]" % (mesh, int(i)) for i in sorted(set([int(v) for v in ids]))]
    created = cmds.sets(comps, name=name)
    return {"count": len(comps), "set": created}


def main():
    if not cmds.objExists("A"):
        raise RuntimeError("Target mesh A not found")
    if not cmds.objExists("M_Head_base"):
        raise RuntimeError("Source mesh M_Head_base not found")

    weights = {
        "hybrid": np.load(str(HYBRID_WEIGHTS_PATH), allow_pickle=True),
        "v084": np.load(str(V084_WEIGHTS_PATH), allow_pickle=True),
        "v085": np.load(str(V085_WEIGHTS_PATH), allow_pickle=True),
        "v086": np.load(str(V086_WEIGHTS_PATH), allow_pickle=True),
    }
    influence_names = json.loads(INFLUENCE_JSON_PATH.read_text(encoding="utf-8"))["influence_names"]
    source_data = np.load(str(SOURCE_DATA), allow_pickle=True)
    val = np.load(str(VALIDATION_PATH), allow_pickle=True)
    groups = _groups(source_data, val)

    attrs = _all_pose_attrs()
    old_values = _capture_attrs(attrs)
    active_reset_attrs = _reset_attrs(attrs)

    _delete_existing()
    cmds.undoInfo(openChunk=True, chunkName="CDFDIAG_V086_alpha_candidate_validate")
    try:
        for key, source_key, mesh, offset_x, color in CANDIDATES:
            dup = _duplicate_target(mesh)
            skin = _apply_weights(dup, mesh + "_skinCluster", influence_names, weights[source_key][key])
            sg = _make_material(mesh + "_MAT", color)
            cmds.sets(dup, edit=True, forceElement=sg)
            grp = cmds.group(dup, name=mesh + "_GRP")
            cmds.setAttr(grp + ".translateX", float(offset_x))
            if not cmds.objExists(skin):
                raise RuntimeError("Skin write failed for %s" % mesh)

        report = {
            "status": "SUCCESS",
            "scene": cmds.file(q=True, sn=True),
            "source": "M_Head_base",
            "target": "A",
            "validation_path": str(VALIDATION_PATH),
            "v086_weights_path": str(V086_WEIGHTS_PATH),
            "poses_requested": [{"name": name, "attrs": pose} for name, pose in POSES],
            "active_reset_attrs": active_reset_attrs,
            "candidates": [key for key, _, _, _, _ in CANDIDATES],
            "poses": {},
            "candidate_summary": {},
            "regression_sets": {},
            "note": "Actual Maya DG multipose validation for v086 per-row alpha gated candidates.",
        }
        error_arrays = {}
        regression_vertices = {key: set() for key, _, _, _, _ in CANDIDATES if key != "hybrid_m0010_cc3"}

        _reset_attrs(attrs)
        source_neutral = _get_points("M_Head_base")
        target_neutral = {key: _get_points(mesh) for key, _, mesh, _, _ in CANDIDATES}

        for pose_name, pose in POSES:
            _reset_attrs(attrs)
            applied = _apply_pose(pose)
            if not applied:
                report["poses"][pose_name] = {"status": "SKIPPED", "reason": "No requested attrs were settable"}
                continue
            source_pose = _get_points("M_Head_base")
            pose_payload = {"status": "SUCCESS", "applied": applied, "candidates": {}, "vs_base": {}}
            pose_arrays = {}
            base_ids = None
            base_errors = None

            for key, _, mesh, _, _ in CANDIDATES:
                metrics, arrays = _pose_errors(mesh, groups, val, source_neutral, source_pose, target_neutral[key])
                pose_payload["candidates"][key] = metrics
                ids, errors = arrays["supported"]
                full = np.full((int(cmds.polyEvaluate(mesh, vertex=True)),), np.nan, dtype=np.float64)
                full[ids] = errors
                error_arrays["%s__%s__supported_motion_error" % (pose_name, key)] = full
                pose_arrays[key] = (ids, errors)
                if key == "hybrid_m0010_cc3":
                    base_ids, base_errors = ids, errors

            if base_ids is not None and base_errors is not None:
                base_index = {int(v): i for i, v in enumerate(base_ids.tolist())}
                for key, _, _, _, _ in CANDIDATES:
                    if key == "hybrid_m0010_cc3":
                        continue
                    ids, errors = pose_arrays[key]
                    common = [i for i, vid in enumerate(ids.tolist()) if int(vid) in base_index]
                    if not common:
                        continue
                    common = np.asarray(common, dtype=np.int32)
                    base_common = np.asarray([base_errors[base_index[int(ids[i])]] for i in common], dtype=np.float64)
                    cand_common = errors[common]
                    diff = cand_common - base_common
                    regressed_vids = ids[common[diff > 0.005]].astype(np.int32)
                    improved_vids = ids[common[diff < -0.005]].astype(np.int32)
                    regression_vertices[key].update([int(v) for v in regressed_vids.tolist()])
                    pose_payload["vs_base"][key] = {
                        "common_count": int(len(common)),
                        "p95_delta": float(np.percentile(cand_common, 95) - np.percentile(base_common, 95)),
                        "mean_delta": float(np.mean(cand_common) - np.mean(base_common)),
                        "improved_count_gt_0p005": int(len(improved_vids)),
                        "regressed_count_gt_0p005": int(len(regressed_vids)),
                        "max_regression": float(np.max(diff)) if len(diff) else 0.0,
                    }

            report["poses"][pose_name] = pose_payload

        for key, _, mesh, _, _ in CANDIDATES:
            if key == "hybrid_m0010_cc3":
                continue
            supported_p95 = []
            strict_p95 = []
            regression_count = 0
            for pose_payload in report["poses"].values():
                if pose_payload.get("status") != "SUCCESS":
                    continue
                metrics = pose_payload["candidates"][key]
                supported_p95.append(metrics["supported"]["p95"])
                strict_p95.append(metrics["strict_block"]["p95"])
                regression_count += pose_payload["vs_base"].get(key, {}).get("regressed_count_gt_0p005", 0)
            report["candidate_summary"][key] = {
                "pose_count": int(len(supported_p95)),
                "supported_p95_max": float(max(supported_p95)) if supported_p95 else None,
                "supported_p95_mean": float(np.mean(supported_p95)) if supported_p95 else None,
                "strict_p95_max": float(max(strict_p95)) if strict_p95 else None,
                "total_regressed_count_gt_0p005_vs_base": int(regression_count),
            }

        selected_sets = []
        for key, _, mesh, _, _ in CANDIDATES:
            if key == "hybrid_m0010_cc3":
                continue
            set_name = "CDFDIAG_V086_MULTI_%s_REGRESSION_SET" % key.replace("v086_alphaGate_", "A").replace("v084_inverse_", "V084_").replace("v085_inverse_", "V085_").upper()
            report["regression_sets"][key] = _make_set(mesh, set_name, sorted(regression_vertices[key]))
            if report["regression_sets"][key]["count"]:
                selected_sets.append(set_name)

        np.savez_compressed(str(ERROR_ARRAYS_PATH), **error_arrays)
        report["error_arrays_path"] = str(ERROR_ARRAYS_PATH)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if selected_sets and cmds.objExists(selected_sets[-1]):
            cmds.select(selected_sets[-1], replace=True)
    finally:
        _restore_attrs(old_values)
        cmds.undoInfo(closeChunk=True)

    return report


result = main()
