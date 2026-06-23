"""v089b 候选的真实 Maya DG 多控制器验收。

目的不是只测 Jaw，而是覆盖嘴、眼睑、脸颊、鼻、下巴等真实控制器。
source 侧会关闭 blendShape envelope，只验 Skin 权重复制本身。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import traceback

import maya.api.OpenMaya as om2
import maya.cmds as cmds
import numpy as np


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
INPUT_NPZ = INFO_DIR / "v087_all_influence_input.npz"
REPORT_JSON = INFO_DIR / "v089b_actual_dg_multipose_report.json"
REPORT_CSV = INFO_DIR / "v089b_actual_dg_multipose_summary.csv"
ERROR_ARRAYS = INFO_DIR / "v089b_actual_dg_multipose_error_arrays.npz"

SOURCE = "M_Head_base"
BASE_KEY = "v089b_base_hybrid_m0010"
CANDIDATES = [
    (BASE_KEY, "A_V089B_001_baseHybrid"),
    ("v089b_field_prior_topk_DIAGNOSTIC", "A_V089B_002_fieldPrior_DIAGNOSTIC"),
    ("v089b_actual_safe_rt", "A_V089B_003_actualSafeRT"),
    ("v089b_visibility_guard_rt", "A_V089B_004_visibilityGuardRT"),
    ("v089b_final_alpha035_rt", "A_V089B_005_finalAlpha035RT"),
    ("v089b_final_strict_rt", "A_V089B_006_finalStrictRT"),
]

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


def _shape(mesh: str) -> str:
    shapes = cmds.listRelatives(mesh, shapes=True, noIntermediate=True, fullPath=True) or []
    if not shapes:
        shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True) or []
    meshes = [shape for shape in shapes if cmds.nodeType(shape) == "mesh"]
    if not meshes:
        raise RuntimeError("找不到 mesh shape: %s" % mesh)
    return meshes[0]


def _dag(node: str) -> om2.MDagPath:
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDagPath(0)


def _points(mesh: str) -> np.ndarray:
    fn = om2.MFnMesh(_dag(_shape(mesh)))
    pts = fn.getPoints(om2.MSpace.kWorld)
    return np.asarray([[p.x, p.y, p.z] for p in pts], dtype=np.float64)


def _target_face_range_vertices(mesh: str, start_face: int, end_face: int) -> np.ndarray:
    fn = om2.MFnMesh(_dag(_shape(mesh)))
    counts, flat = fn.getVertices()
    counts = np.asarray(counts, dtype=np.int64)
    flat = np.asarray(flat, dtype=np.int64)
    offsets = np.zeros(counts.shape[0] + 1, dtype=np.int64)
    offsets[1:] = np.cumsum(counts)
    vertices = set()
    for face_id in range(start_face, min(end_face + 1, counts.shape[0])):
        vertices.update(flat[offsets[face_id] : offsets[face_id + 1]].tolist())
    return np.asarray(sorted(vertices), dtype=np.int32)


def _stat(values: np.ndarray) -> dict:
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"count": 0}
    return {
        "count": int(arr.size),
        "mean": float(np.mean(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(np.max(arr)),
    }


def _set_attr_safe(attr: str, value: float) -> bool:
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
        _set_attr_safe(attr, value)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)


def _reset_attrs(attrs: list[str]) -> list[str]:
    active = []
    for attr in attrs:
        if _set_attr_safe(attr, 0.0):
            active.append(attr)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)
    return active


def _apply_pose(pose: dict[str, float]) -> dict[str, float]:
    applied = {}
    for attr, value in pose.items():
        if _set_attr_safe(attr, value):
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


def _expected_points(data: dict, source_points: np.ndarray, ids: np.ndarray) -> np.ndarray:
    source_tris = np.asarray(data["source_tris"], dtype=np.int64)
    best_tri = np.asarray(data["best_tri"], dtype=np.int64)
    best_bary = np.asarray(data["best_bary"], dtype=np.float64)
    tris = source_tris[best_tri[ids]]
    bary = best_bary[ids]
    return (
        bary[:, 0:1] * source_points[tris[:, 0]]
        + bary[:, 1:2] * source_points[tris[:, 1]]
        + bary[:, 2:3] * source_points[tris[:, 2]]
    )


def _groups(data: dict) -> dict[str, np.ndarray]:
    unsupported = np.asarray(data["unsupported"], dtype=bool)
    high = np.asarray(data["high_confidence"], dtype=bool)
    low = np.asarray(data["low_confidence"], dtype=bool)
    topology = np.asarray(data["topology_discontinuity"], dtype=bool)
    semantic = np.asarray(data["semantic_ambiguous"], dtype=bool)
    face_semantic = np.asarray(data["face_semantic_discontinuity"], dtype=bool)
    strict = (topology | semantic | face_semantic) & (~unsupported)
    out = {
        "supported": np.where(~unsupported)[0],
        "high_confidence": np.where(high)[0],
        "low_confidence": np.where(low)[0],
        "strict_block": np.where(strict)[0],
        "topology_discontinuity": np.where(topology & (~unsupported))[0],
        "semantic_ambiguous": np.where(semantic & (~unsupported))[0],
        "mouth_red_left_9095_9150": _target_face_range_vertices("A", 9095, 9150),
        "mouth_red_mid_9263_9318": _target_face_range_vertices("A", 9263, 9318),
    }
    for name, ids in list(out.items()):
        out[name] = ids[(~unsupported[ids])] if ids.size else ids
    return out


def _pose_errors(
    mesh: str,
    groups: dict[str, np.ndarray],
    data: dict,
    source_neutral: np.ndarray,
    source_pose: np.ndarray,
    target_neutral: np.ndarray,
) -> tuple[dict, dict]:
    actual = _points(mesh)
    metrics = {}
    arrays = {}
    for name, ids in groups.items():
        if ids.size == 0:
            metrics[name] = {"count": 0}
            arrays[name] = (ids, np.zeros((0,), dtype=np.float64))
            continue
        expected_neutral = _expected_points(data, source_neutral, ids)
        expected_pose = _expected_points(data, source_pose, ids)
        error = np.linalg.norm((actual[ids] - target_neutral[ids]) - (expected_pose - expected_neutral), axis=1)
        metrics[name] = _stat(error)
        arrays[name] = (ids, error)
    return metrics, arrays


def _make_set(mesh: str, name: str, ids: list[int] | set[int] | np.ndarray, limit: int | None = None) -> dict:
    if cmds.objExists(name):
        cmds.delete(name)
    clean = sorted(set(int(v) for v in list(ids)))
    total = len(clean)
    if limit is not None:
        clean = clean[:limit]
    if not clean:
        cmds.sets(empty=True, name=name)
        return {"set": name, "count": total, "created_count": 0}
    comps = ["%s.vtx[%d]" % (mesh, v) for v in clean]
    created = cmds.sets(comps, name=name)
    return {"set": created, "count": total, "created_count": len(clean)}


def _candidate_key_to_set_prefix(key: str) -> str:
    return "CDFDIAG_V089B_ACTUAL_" + key.upper().replace("V089B_", "").replace("_", "")


def _execute() -> dict:
    for _key, mesh in CANDIDATES:
        if not cmds.objExists(mesh):
            raise RuntimeError("候选 mesh 不存在: %s" % mesh)
    if not cmds.objExists(SOURCE):
        raise RuntimeError("source mesh 不存在: %s" % SOURCE)

    data = {key: value for key, value in np.load(str(INPUT_NPZ), allow_pickle=True).items()}
    groups = _groups(data)
    attrs = _all_pose_attrs()
    old_values = _capture_attrs(attrs)
    old_bs = _disable_source_blendshapes()
    active_reset_attrs = _reset_attrs(attrs)

    report = {
        "status": "SUCCESS",
        "scene": cmds.file(q=True, sceneName=True),
        "source": SOURCE,
        "source_blendshape_disabled": [plug for plug, _value in old_bs],
        "poses_requested": [{"name": name, "attrs": pose} for name, pose in POSES],
        "active_reset_attrs": active_reset_attrs,
        "candidates": [key for key, _mesh in CANDIDATES],
        "groups": {name: int(ids.size) for name, ids in groups.items()},
        "poses": {},
        "candidate_summary": {},
        "sets": {},
        "note": "v089b 真实 Maya DG 多控制器验收；source 侧关闭 blendShape，只评估 Skin 权重复制。",
    }
    error_arrays = {}
    regression_vertices = {key: set() for key, _mesh in CANDIDATES if key != BASE_KEY}
    top_error_bucket = {key: np.zeros((int(cmds.polyEvaluate(mesh, vertex=True)),), dtype=np.float64) for key, mesh in CANDIDATES}

    try:
        _reset_attrs(attrs)
        source_neutral = _points(SOURCE)
        target_neutral = {key: _points(mesh) for key, mesh in CANDIDATES}

        for pose_name, pose in POSES:
            _reset_attrs(attrs)
            applied = _apply_pose(pose)
            if not applied:
                report["poses"][pose_name] = {"status": "SKIPPED", "reason": "没有可写属性", "requested": pose}
                continue
            source_pose = _points(SOURCE)
            pose_payload = {"status": "SUCCESS", "applied": applied, "candidates": {}, "vs_base": {}}
            pose_arrays = {}
            base_ids = None
            base_errors = None

            for key, mesh in CANDIDATES:
                metrics, arrays = _pose_errors(mesh, groups, data, source_neutral, source_pose, target_neutral[key])
                pose_payload["candidates"][key] = metrics
                ids, errors = arrays["supported"]
                pose_arrays[key] = (ids, errors)
                full = np.full((int(cmds.polyEvaluate(mesh, vertex=True)),), np.nan, dtype=np.float64)
                full[ids] = errors
                error_arrays["%s__%s__supported" % (pose_name, key)] = full
                current_max = top_error_bucket[key]
                good = np.isfinite(full)
                current_max[good] = np.maximum(current_max[good], full[good])
                if key == BASE_KEY:
                    base_ids, base_errors = ids, errors

            if base_ids is not None and base_errors is not None:
                base_index = {int(v): i for i, v in enumerate(base_ids.tolist())}
                for key, mesh in CANDIDATES:
                    if key == BASE_KEY:
                        continue
                    ids, errors = pose_arrays[key]
                    common = [i for i, vid in enumerate(ids.tolist()) if int(vid) in base_index]
                    if not common:
                        continue
                    common = np.asarray(common, dtype=np.int32)
                    base_common = np.asarray([base_errors[base_index[int(ids[i])]] for i in common], dtype=np.float64)
                    cand_common = errors[common]
                    diff = cand_common - base_common
                    regressed = ids[common[diff > 0.005]].astype(np.int32)
                    improved = ids[common[diff < -0.005]].astype(np.int32)
                    regression_vertices[key].update(int(v) for v in regressed.tolist())
                    pose_payload["vs_base"][key] = {
                        "common_count": int(common.size),
                        "supported_p95_delta": float(np.percentile(cand_common, 95) - np.percentile(base_common, 95)),
                        "supported_mean_delta": float(np.mean(cand_common) - np.mean(base_common)),
                        "improved_count_gt_0p005": int(improved.size),
                        "regressed_count_gt_0p005": int(regressed.size),
                        "max_regression": float(np.max(diff)) if diff.size else 0.0,
                    }
            report["poses"][pose_name] = pose_payload

        rows = []
        for key, mesh in CANDIDATES:
            supported_p95 = []
            high_p95 = []
            low_p95 = []
            strict_p95 = []
            pose_count = 0
            total_regressions = 0
            total_improvements = 0
            for pose_payload in report["poses"].values():
                if pose_payload.get("status") != "SUCCESS":
                    continue
                pose_count += 1
                metrics = pose_payload["candidates"][key]
                supported_p95.append(metrics["supported"].get("p95", 0.0))
                high_p95.append(metrics["high_confidence"].get("p95", 0.0))
                low_p95.append(metrics["low_confidence"].get("p95", 0.0))
                strict_p95.append(metrics["strict_block"].get("p95", 0.0))
                if key != BASE_KEY:
                    delta = pose_payload["vs_base"].get(key, {})
                    total_regressions += int(delta.get("regressed_count_gt_0p005", 0))
                    total_improvements += int(delta.get("improved_count_gt_0p005", 0))
            summary = {
                "pose_count": int(pose_count),
                "supported_p95_max": float(max(supported_p95)) if supported_p95 else None,
                "supported_p95_mean": float(np.mean(supported_p95)) if supported_p95 else None,
                "high_p95_max": float(max(high_p95)) if high_p95 else None,
                "low_p95_max": float(max(low_p95)) if low_p95 else None,
                "strict_p95_max": float(max(strict_p95)) if strict_p95 else None,
                "total_regressed_count_gt_0p005_vs_base": int(total_regressions),
                "total_improved_count_gt_0p005_vs_base": int(total_improvements),
                "unique_regressed_vertices_vs_base": int(len(regression_vertices.get(key, set()))),
            }
            report["candidate_summary"][key] = summary
            rows.append({"candidate": key, **summary})

            top_error = top_error_bucket[key]
            top_ids = np.argsort(top_error)[::-1]
            top_ids = top_ids[top_error[top_ids] > 0.005][:300]
            report["sets"][key + "_top_error"] = _make_set(mesh, _candidate_key_to_set_prefix(key) + "_TOPERROR_SET", top_ids)
            if key != BASE_KEY:
                report["sets"][key + "_regression"] = _make_set(mesh, _candidate_key_to_set_prefix(key) + "_REGRESSION_SET", regression_vertices[key])

        np.savez_compressed(str(ERROR_ARRAYS), **error_arrays)
        report["error_arrays_path"] = str(ERROR_ARRAYS)
        REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        with REPORT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "candidate",
                    "pose_count",
                    "supported_p95_max",
                    "supported_p95_mean",
                    "high_p95_max",
                    "low_p95_max",
                    "strict_p95_max",
                    "total_regressed_count_gt_0p005_vs_base",
                    "total_improved_count_gt_0p005_vs_base",
                    "unique_regressed_vertices_vs_base",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        cmds.file(save=True, type="mayaAscii", force=True)
    finally:
        _restore_attrs(old_values)
        _restore_plugs(old_bs)

    return report


try:
    result = _execute()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
