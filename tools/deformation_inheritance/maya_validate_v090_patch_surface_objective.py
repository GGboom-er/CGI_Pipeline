"""v090 patch-level surface objective 验收。

在真实 Maya DG 姿态下，不再只看每点位移误差，同时计算：
- vertex motion error
- edge length strain error
- triangle area strain error
- triangle normal error

本脚本只评估并创建诊断 set，不改权重。
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import traceback

import maya.api.OpenMaya as om2
import maya.cmds as cmds
import numpy as np


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
SCENE = PROJECT_DIR / "test_v090_A_patchSurface_candidates.ma"
OUTPUT_SCENE = PROJECT_DIR / "test_v090_A_patchSurface_candidates_eval.ma"
INPUT_NPZ = INFO_DIR / "v087_all_influence_input.npz"
REPORT_JSON = INFO_DIR / "v090_patch_surface_candidates_objective_report.json"
REPORT_CSV = INFO_DIR / "v090_patch_surface_candidates_objective_summary.csv"
ARRAYS_NPZ = INFO_DIR / "v090_patch_surface_candidates_objective_arrays.npz"

SOURCE = "M_Head_base"
BASE_KEY = "v089b_base_hybrid_m0010"
CANDIDATES = [
    (BASE_KEY, "A_V089B_001_baseHybrid"),
    ("v089b_field_prior_topk_DIAGNOSTIC", "A_V089B_002_fieldPrior_DIAGNOSTIC"),
    ("v089b_actual_safe_rt", "A_V089B_003_actualSafeRT"),
    ("v089b_visibility_guard_rt", "A_V089B_004_visibilityGuardRT"),
    ("v089b_final_alpha035_rt", "A_V089B_005_finalAlpha035RT"),
    ("v089b_final_strict_rt", "A_V089B_006_finalStrictRT"),
    ("v090_strict_patch_direct", "A_V090_001_strictDirect"),
    ("v090_strict_patch_blend035", "A_V090_002_strictBlend035"),
    ("v090_balanced_patch_direct", "A_V090_003_balancedDirect"),
    ("v090_balanced_patch_blend035", "A_V090_004_balancedBlend035"),
    ("v090_broad_patch_direct", "A_V090_005_broadDirect"),
    ("v090_broad_patch_blend035", "A_V090_006_broadBlend035"),
    ("v090_broad_patch_blend060", "A_V090_007_broadBlend060"),
    ("v090_broad_patch_blend060_smooth", "A_V090_008_broadBlend060Smooth"),
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

EPS = 1e-8


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


def _topology(mesh: str) -> tuple[list[np.ndarray], np.ndarray, np.ndarray, np.ndarray]:
    fn = om2.MFnMesh(_dag(_shape(mesh)))
    counts, flat = fn.getVertices()
    counts = np.asarray(counts, dtype=np.int64)
    flat = np.asarray(flat, dtype=np.int64)
    offsets = np.zeros(counts.shape[0] + 1, dtype=np.int64)
    offsets[1:] = np.cumsum(counts)
    faces = [flat[offsets[i] : offsets[i + 1]].astype(np.int32) for i in range(counts.shape[0])]
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
        if n == 3:
            tris.append((int(face[0]), int(face[1]), int(face[2])))
            tri_faces.append(face_id)
        elif n > 3:
            root = int(face[0])
            for i in range(1, n - 1):
                tris.append((root, int(face[i]), int(face[i + 1])))
                tri_faces.append(face_id)
    return faces, np.asarray(tris, dtype=np.int32), np.asarray(tri_faces, dtype=np.int32), np.asarray(sorted(edges), dtype=np.int32)


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


def _face_range_vertices(faces: list[np.ndarray], start: int, end: int, unsupported: np.ndarray) -> np.ndarray:
    ids = set()
    for face_id in range(start, min(end + 1, len(faces))):
        ids.update(int(v) for v in faces[face_id].tolist())
    arr = np.asarray(sorted(ids), dtype=np.int32)
    return arr[~unsupported[arr]] if arr.size else arr


def _tri_area(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    return 0.5 * np.linalg.norm(np.cross(points[tris[:, 1]] - points[tris[:, 0]], points[tris[:, 2]] - points[tris[:, 0]]), axis=1)


def _tri_normal(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    n = np.cross(points[tris[:, 1]] - points[tris[:, 0]], points[tris[:, 2]] - points[tris[:, 0]])
    length = np.linalg.norm(n, axis=1, keepdims=True)
    length[length < EPS] = 1.0
    return n / length


def _surface_metrics(
    actual: np.ndarray,
    neutral: np.ndarray,
    expected_pose: np.ndarray,
    expected_neutral: np.ndarray,
    edges: np.ndarray,
    tris: np.ndarray,
    unsupported: np.ndarray,
) -> dict[str, np.ndarray]:
    vertex_error = np.linalg.norm((actual - neutral) - (expected_pose - expected_neutral), axis=1)
    vertex_error[unsupported] = np.nan

    cand_edge_neutral = np.linalg.norm(neutral[edges[:, 0]] - neutral[edges[:, 1]], axis=1)
    cand_edge_pose = np.linalg.norm(actual[edges[:, 0]] - actual[edges[:, 1]], axis=1)
    exp_edge_neutral = np.linalg.norm(expected_neutral[edges[:, 0]] - expected_neutral[edges[:, 1]], axis=1)
    exp_edge_pose = np.linalg.norm(expected_pose[edges[:, 0]] - expected_pose[edges[:, 1]], axis=1)
    edge_error = np.abs(np.log((cand_edge_pose + EPS) / (cand_edge_neutral + EPS)) - np.log((exp_edge_pose + EPS) / (exp_edge_neutral + EPS)))
    edge_bad_vertex = unsupported[edges[:, 0]] | unsupported[edges[:, 1]]
    edge_error[edge_bad_vertex] = np.nan

    cand_area_neutral = _tri_area(neutral, tris)
    cand_area_pose = _tri_area(actual, tris)
    exp_area_neutral = _tri_area(expected_neutral, tris)
    exp_area_pose = _tri_area(expected_pose, tris)
    area_error = np.abs(np.log((cand_area_pose + EPS) / (cand_area_neutral + EPS)) - np.log((exp_area_pose + EPS) / (exp_area_neutral + EPS)))
    tri_bad = unsupported[tris[:, 0]] | unsupported[tris[:, 1]] | unsupported[tris[:, 2]]
    area_error[tri_bad] = np.nan

    cand_normal = _tri_normal(actual, tris)
    exp_normal = _tri_normal(expected_pose, tris)
    normal_error = 1.0 - np.clip(np.einsum("ij,ij->i", cand_normal, exp_normal), -1.0, 1.0)
    normal_error[tri_bad] = np.nan

    tri_score = (
        np.nan_to_num(np.max(vertex_error[tris], axis=1), nan=0.0)
        + 0.30 * np.nan_to_num(area_error, nan=0.0)
        + 0.15 * np.nan_to_num(normal_error, nan=0.0)
    )
    tri_score[tri_bad] = np.nan

    return {
        "vertex_error": vertex_error,
        "edge_error": edge_error,
        "area_error": area_error,
        "normal_error": normal_error,
        "tri_score": tri_score,
    }


def _group_stats(metrics: dict[str, np.ndarray], groups: dict[str, np.ndarray], edges: np.ndarray, tris: np.ndarray) -> dict:
    out = {}
    edge_groups = {}
    tri_groups = {}
    for name, vertices in groups.items():
        if vertices.size:
            mask_v = np.zeros((np.nanmax(tris) + 1,), dtype=bool)
            mask_v[vertices] = True
            edge_groups[name] = np.where(mask_v[edges[:, 0]] | mask_v[edges[:, 1]])[0]
            tri_groups[name] = np.where(mask_v[tris[:, 0]] | mask_v[tris[:, 1]] | mask_v[tris[:, 2]])[0]
        else:
            edge_groups[name] = np.asarray([], dtype=np.int32)
            tri_groups[name] = np.asarray([], dtype=np.int32)
    for name, vertices in groups.items():
        edge_ids = edge_groups[name]
        tri_ids = tri_groups[name]
        vertex_p95 = _stat(metrics["vertex_error"][vertices]).get("p95", 0.0) if vertices.size else 0.0
        edge_p95 = _stat(metrics["edge_error"][edge_ids]).get("p95", 0.0) if edge_ids.size else 0.0
        area_p95 = _stat(metrics["area_error"][tri_ids]).get("p95", 0.0) if tri_ids.size else 0.0
        normal_p95 = _stat(metrics["normal_error"][tri_ids]).get("p95", 0.0) if tri_ids.size else 0.0
        score = float(vertex_p95 or 0.0) + 0.50 * float(edge_p95 or 0.0) + 0.35 * float(area_p95 or 0.0) + 0.10 * float(normal_p95 or 0.0)
        out[name] = {
            "vertex": _stat(metrics["vertex_error"][vertices]) if vertices.size else {"count": 0},
            "edge": _stat(metrics["edge_error"][edge_ids]) if edge_ids.size else {"count": 0},
            "area": _stat(metrics["area_error"][tri_ids]) if tri_ids.size else {"count": 0},
            "normal": _stat(metrics["normal_error"][tri_ids]) if tri_ids.size else {"count": 0},
            "surface_score": score,
        }
    return out


def _make_set(name: str, components: list[str]) -> dict:
    if cmds.objExists(name):
        cmds.delete(name)
    if not components:
        cmds.sets(empty=True, name=name)
        return {"set": name, "count": 0}
    created = cmds.sets(components, name=name)
    return {"set": created, "count": len(components)}


def _execute() -> dict:
    cmds.file(str(SCENE), open=True, force=True)
    for _key, mesh in CANDIDATES:
        if not cmds.objExists(mesh):
            raise RuntimeError("候选 mesh 不存在: %s" % mesh)
    if not cmds.objExists(SOURCE):
        raise RuntimeError("source mesh 不存在: %s" % SOURCE)

    data = {key: value for key, value in np.load(str(INPUT_NPZ), allow_pickle=True).items()}
    unsupported = np.asarray(data["unsupported"], dtype=bool)
    faces, tris, tri_faces, edges = _topology(CANDIDATES[0][1])
    groups = {
        "supported": np.where(~unsupported)[0],
        "high_confidence": np.where(np.asarray(data["high_confidence"], dtype=bool))[0],
        "low_confidence": np.where(np.asarray(data["low_confidence"], dtype=bool))[0],
        "strict_block": np.where((np.asarray(data["topology_discontinuity"], dtype=bool) | np.asarray(data["semantic_ambiguous"], dtype=bool) | np.asarray(data["face_semantic_discontinuity"], dtype=bool)) & (~unsupported))[0],
        "mouth_red_left_9095_9150": _face_range_vertices(faces, 9095, 9150, unsupported),
        "mouth_red_mid_9263_9318": _face_range_vertices(faces, 9263, 9318, unsupported),
    }

    attrs = _all_pose_attrs()
    old_values = _capture_attrs(attrs)
    old_bs = _disable_source_blendshapes()
    active_reset_attrs = _reset_attrs(attrs)

    report = {
        "status": "SUCCESS",
        "scene": str(SCENE),
        "output_scene": str(OUTPUT_SCENE),
        "source": SOURCE,
        "source_blendshape_disabled": [plug for plug, _value in old_bs],
        "poses_requested": [{"name": name, "attrs": pose} for name, pose in POSES],
        "active_reset_attrs": active_reset_attrs,
        "candidates": [key for key, _mesh in CANDIDATES],
        "groups": {name: int(ids.size) for name, ids in groups.items()},
        "metric_weights": {"surface_score": "vertex_p95 + 0.50*edge_p95 + 0.35*area_p95 + 0.10*normal_p95"},
        "poses": {},
        "candidate_summary": {},
        "sets": {},
    }
    arrays = {}
    try:
        _reset_attrs(attrs)
        source_neutral = _points(SOURCE)
        expected_neutral = _expected_points(data, source_neutral)
        neutral_by_candidate = {key: _points(mesh) for key, mesh in CANDIDATES}

        max_score_by_candidate = {key: np.zeros((tris.shape[0],), dtype=np.float64) for key, _mesh in CANDIDATES}
        regression_faces = {key: set() for key, _mesh in CANDIDATES if key != BASE_KEY}

        for pose_name, pose in POSES:
            _reset_attrs(attrs)
            applied = _apply_pose(pose)
            if not applied:
                report["poses"][pose_name] = {"status": "SKIPPED", "reason": "没有可写属性", "requested": pose}
                continue
            source_pose = _points(SOURCE)
            expected_pose = _expected_points(data, source_pose)
            pose_payload = {"status": "SUCCESS", "applied": applied, "candidates": {}, "vs_base": {}}
            metrics_by_candidate = {}
            group_by_candidate = {}
            for key, mesh in CANDIDATES:
                actual = _points(mesh)
                metrics = _surface_metrics(actual, neutral_by_candidate[key], expected_pose, expected_neutral, edges, tris, unsupported)
                group_stats = _group_stats(metrics, groups, edges, tris)
                metrics_by_candidate[key] = metrics
                group_by_candidate[key] = group_stats
                pose_payload["candidates"][key] = group_stats
                good = np.isfinite(metrics["tri_score"])
                max_score_by_candidate[key][good] = np.maximum(max_score_by_candidate[key][good], metrics["tri_score"][good])
                arrays["%s__%s__vertex_error" % (pose_name, key)] = metrics["vertex_error"].astype(np.float32)
                arrays["%s__%s__tri_score" % (pose_name, key)] = metrics["tri_score"].astype(np.float32)

            base_score = metrics_by_candidate[BASE_KEY]["tri_score"]
            for key, _mesh in CANDIDATES:
                if key == BASE_KEY:
                    continue
                score = metrics_by_candidate[key]["tri_score"]
                diff = score - base_score
                regressed = np.where(np.isfinite(diff) & (diff > 0.01))[0].astype(np.int32)
                improved = np.where(np.isfinite(diff) & (diff < -0.01))[0].astype(np.int32)
                regression_faces[key].update(int(tri_faces[i]) for i in regressed.tolist())
                pose_payload["vs_base"][key] = {
                    "supported_score_delta": float(group_by_candidate[key]["supported"]["surface_score"] - group_by_candidate[BASE_KEY]["supported"]["surface_score"]),
                    "low_score_delta": float(group_by_candidate[key]["low_confidence"]["surface_score"] - group_by_candidate[BASE_KEY]["low_confidence"]["surface_score"]),
                    "strict_score_delta": float(group_by_candidate[key]["strict_block"]["surface_score"] - group_by_candidate[BASE_KEY]["strict_block"]["surface_score"]),
                    "improved_tri_count_gt_0p01": int(improved.size),
                    "regressed_tri_count_gt_0p01": int(regressed.size),
                    "max_tri_regression": float(np.nanmax(diff)) if np.any(np.isfinite(diff)) else 0.0,
                }
            report["poses"][pose_name] = pose_payload

        rows = []
        for key, mesh in CANDIDATES:
            pose_count = 0
            supported_scores = []
            low_scores = []
            strict_scores = []
            red_left_scores = []
            red_mid_scores = []
            regressed_tri_total = 0
            improved_tri_total = 0
            for pose_payload in report["poses"].values():
                if pose_payload.get("status") != "SUCCESS":
                    continue
                pose_count += 1
                candidate_stats = pose_payload["candidates"][key]
                supported_scores.append(candidate_stats["supported"]["surface_score"])
                low_scores.append(candidate_stats["low_confidence"]["surface_score"])
                strict_scores.append(candidate_stats["strict_block"]["surface_score"])
                red_left_scores.append(candidate_stats["mouth_red_left_9095_9150"]["surface_score"])
                red_mid_scores.append(candidate_stats["mouth_red_mid_9263_9318"]["surface_score"])
                if key != BASE_KEY:
                    delta = pose_payload["vs_base"].get(key, {})
                    regressed_tri_total += int(delta.get("regressed_tri_count_gt_0p01", 0))
                    improved_tri_total += int(delta.get("improved_tri_count_gt_0p01", 0))
            summary = {
                "pose_count": int(pose_count),
                "supported_surface_score_max": float(max(supported_scores)) if supported_scores else None,
                "supported_surface_score_mean": float(np.mean(supported_scores)) if supported_scores else None,
                "low_surface_score_max": float(max(low_scores)) if low_scores else None,
                "strict_surface_score_max": float(max(strict_scores)) if strict_scores else None,
                "red_left_surface_score_max": float(max(red_left_scores)) if red_left_scores else None,
                "red_mid_surface_score_max": float(max(red_mid_scores)) if red_mid_scores else None,
                "total_regressed_tri_gt_0p01_vs_base": int(regressed_tri_total),
                "total_improved_tri_gt_0p01_vs_base": int(improved_tri_total),
                "unique_regressed_faces_vs_base": int(len(regression_faces.get(key, set()))),
            }
            report["candidate_summary"][key] = summary
            rows.append({"candidate": key, **summary})

            top_tri = np.argsort(max_score_by_candidate[key])[::-1]
            top_tri = top_tri[np.isfinite(max_score_by_candidate[key][top_tri])][:300]
            top_faces = sorted(set(int(tri_faces[i]) for i in top_tri.tolist()))
            report["sets"][key + "_top_surface_faces"] = _make_set(
                "CDFDIAG_V090_SURFACE_%s_TOPFACE_SET" % key.upper().replace("V089B_", "").replace("_", ""),
                ["%s.f[%d]" % (mesh, face_id) for face_id in top_faces],
            )
            if key != BASE_KEY:
                report["sets"][key + "_regression_faces"] = _make_set(
                    "CDFDIAG_V090_SURFACE_%s_REGRESSIONFACE_SET" % key.upper().replace("V089B_", "").replace("_", ""),
                    ["%s.f[%d]" % (mesh, face_id) for face_id in sorted(regression_faces[key])],
                )

        np.savez_compressed(str(ARRAYS_NPZ), **arrays)
        report["arrays_path"] = str(ARRAYS_NPZ)
        REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        with REPORT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "candidate",
                    "pose_count",
                    "supported_surface_score_max",
                    "supported_surface_score_mean",
                    "low_surface_score_max",
                    "strict_surface_score_max",
                    "red_left_surface_score_max",
                    "red_mid_surface_score_max",
                    "total_regressed_tri_gt_0p01_vs_base",
                    "total_improved_tri_gt_0p01_vs_base",
                    "unique_regressed_faces_vs_base",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        cmds.file(rename=str(OUTPUT_SCENE))
        cmds.file(save=True, type="mayaAscii", force=True)
    finally:
        _restore_attrs(old_values)
        _restore_plugs(old_bs)

    return report


try:
    result = _execute()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
