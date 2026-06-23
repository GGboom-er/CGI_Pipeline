"""v089b 快速连续场 + 真实 DG 门控的最终候选测试。

本脚本只做离线求解，不写 Maya。

目标：
1. 从干净 `test.ma` 导出的 v082/v087 数据重新构建连续 source 权重场。
2. 复用已验证可计算的 v087 全 influence 反求 raw 候选。
3. 把 v088 射线/可见性风险和 v087 真实 Maya DG 回归结果接入门控。
4. 输出完整 NPZ/JSON/CSV，后续再写 Maya 对照体并跑真实 DG 验收。

注意：
- 这里的 continuous field 是 correspondence / weight prior，不再直接为每个 joint/channel
  重算 expected motion；v089 初版在该处超时，不适合作为生产链路。
- v087 actual-DG-in-loop 是强门控：生产化时可以先生成 raw 诊断体、跑真实 ROM，
  再按同一逻辑回收安全行。
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from validate_mhead_to_a_v082_correspondence import (  # noqa: E402
    FAMILIES,
    build_edges,
    classify_influence,
    closest_point_tri_batch,
    faces_from_flat,
    safe_normalize,
    triangle_normals,
    triangulate,
    vertex_normals,
)
from solve_v087_all_influence_inverse import (  # noqa: E402
    _stats,
    _topk_prune,
    evaluate_weights,
    per_vertex_error,
)


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
SCENE_DATA = INFO_DIR / "v082_clean_scene_data.npz"
V087_INPUT = INFO_DIR / "v087_all_influence_input.npz"
V087_CANDIDATES = INFO_DIR / "v087_all_influence_candidates.npz"
V087_ACTUAL = INFO_DIR / "v087_actual_dg_multipose_error_arrays.npz"
V088_VISIBILITY = INFO_DIR / "v088_visibility_correspondence_probe.npz"

OUT_NPZ = INFO_DIR / "v089b_fast_continuous_field_candidates.npz"
OUT_JSON = INFO_DIR / "v089b_fast_continuous_field_summary.json"
OUT_CSV = INFO_DIR / "v089b_fast_continuous_field_summary.csv"

FIELD_K = 32
FIELD_SIGMA = 0.14
NORMAL_POWER = 1.6
FAMILY_SIGMA = 0.72
FIELD_TOPK = 12
SOLVE_TOPK = 8
EPS = 1e-12

BASE_KEY = "v087_base_hybrid_m0010"
RAW_KEY = "v087_rt_raw_topk"


def _now() -> float:
    return time.perf_counter()


def _step(name: str, start: float, payload: dict | None = None) -> dict:
    out = {"name": name, "status": "SUCCESS", "seconds": round(_now() - start, 3)}
    if payload:
        out.update(payload)
    return out


def _normalize_rows(weights: np.ndarray) -> np.ndarray:
    out = np.maximum(np.asarray(weights, dtype=np.float64), 0.0).copy()
    sums = out.sum(axis=1, keepdims=True)
    good = sums[:, 0] > EPS
    out[good] /= sums[good]
    return out


def _family_matrix(influence_names: np.ndarray) -> np.ndarray:
    out = np.zeros((len(influence_names), len(FAMILIES)), dtype=np.float64)
    for i, name in enumerate(influence_names.tolist()):
        family = classify_influence(str(name))
        out[i, FAMILIES.index(family)] = 1.0
    return out


def _tri_centroids(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    return (points[tris[:, 0]] + points[tris[:, 1]] + points[tris[:, 2]]) / 3.0


def _load_npz(path: Path) -> dict:
    loaded = np.load(str(path), allow_pickle=True)
    return {key: loaded[key] for key in loaded.files}


def _build_fast_field(scene: dict, data: dict) -> dict:
    source_points = np.asarray(scene["source_points"], dtype=np.float64)
    target_points = np.asarray(scene["target_points"], dtype=np.float64)
    source_weights = np.asarray(data["source_weights"], dtype=np.float64)
    base_weights = np.asarray(data["base_weights"], dtype=np.float64)
    supported = ~np.asarray(data["unsupported"], dtype=bool)

    source_faces = faces_from_flat(scene["source_face_counts"], scene["source_face_offsets"], scene["source_face_vertices"])
    target_faces = faces_from_flat(scene["target_face_counts"], scene["target_face_offsets"], scene["target_face_vertices"])
    source_tris = triangulate(source_faces)
    v087_source_tris = np.asarray(data["source_tris"], dtype=np.int32)
    if source_tris.shape == v087_source_tris.shape and np.all(source_tris == v087_source_tris):
        source_tris = v087_source_tris
    target_tris = triangulate(target_faces)

    source_normals = triangle_normals(source_points, source_tris)
    target_normals = vertex_normals(target_points, target_tris)
    tree = cKDTree(_tri_centroids(source_points, source_tris))

    family_mat = _family_matrix(np.asarray(data["influence_names"]))
    source_family = _normalize_rows(source_weights @ family_mat)
    base_family = _normalize_rows(base_weights @ family_mat)

    ids = np.where(supported)[0].astype(np.int32)
    _dist, candidate_tris = tree.query(target_points[ids], k=min(FIELD_K, len(source_tris)))
    candidate_tris = np.asarray(candidate_tris, dtype=np.int64)
    if candidate_tris.ndim == 1:
        candidate_tris = candidate_tris[:, None]

    n, k = candidate_tris.shape
    candidate_coeff = np.zeros((n, k), dtype=np.float64)
    candidate_bary = np.zeros((n, k, 3), dtype=np.float64)
    candidate_dist = np.zeros((n, k), dtype=np.float64)
    candidate_normal_dot = np.zeros((n, k), dtype=np.float64)
    candidate_family_l1 = np.zeros((n, k), dtype=np.float64)

    for row, target_id in enumerate(ids.tolist()):
        cand = candidate_tris[row]
        tris = source_tris[cand]
        closest, bary, dist2 = closest_point_tri_batch(
            target_points[target_id],
            source_points[tris[:, 0]],
            source_points[tris[:, 1]],
            source_points[tris[:, 2]],
        )
        del closest
        dist = np.sqrt(np.maximum(dist2, 0.0))
        fam = (
            bary[:, 0:1] * source_family[tris[:, 0]]
            + bary[:, 1:2] * source_family[tris[:, 1]]
            + bary[:, 2:3] * source_family[tris[:, 2]]
        )
        fam = _normalize_rows(fam)
        normal_dot = np.clip(source_normals[cand] @ target_normals[target_id], -1.0, 1.0)
        family_l1 = np.abs(fam - base_family[target_id][None, :]).sum(axis=1)

        dist_score = np.exp(-((dist / FIELD_SIGMA) ** 2))
        normal_score = np.power(np.clip((normal_dot + 1.0) * 0.5, 0.0, 1.0), NORMAL_POWER)
        family_score = np.exp(-(family_l1 / FAMILY_SIGMA))
        coeff = dist_score * (0.10 + 0.90 * normal_score) * (0.20 + 0.80 * family_score)
        if np.sum(coeff) <= EPS:
            coeff = dist_score + EPS
        coeff /= max(float(np.sum(coeff)), EPS)

        candidate_coeff[row] = coeff
        candidate_bary[row] = bary
        candidate_dist[row] = dist
        candidate_normal_dot[row] = normal_dot
        candidate_family_l1[row] = family_l1

    tri = source_tris[candidate_tris]
    field_supported = np.zeros((n, source_weights.shape[1]), dtype=np.float64)
    for joint_id in range(source_weights.shape[1]):
        sw = (
            candidate_bary[:, :, 0] * source_weights[tri[:, :, 0], joint_id]
            + candidate_bary[:, :, 1] * source_weights[tri[:, :, 1], joint_id]
            + candidate_bary[:, :, 2] * source_weights[tri[:, :, 2], joint_id]
        )
        field_supported[:, joint_id] = np.einsum("nk,nk->n", candidate_coeff, sw)

    field_weights = np.asarray(base_weights, dtype=np.float64).copy()
    field_weights[ids] = field_supported
    field_weights = _topk_prune(field_weights, FIELD_TOPK, supported)

    coeff_entropy = -np.sum(candidate_coeff * np.log(np.maximum(candidate_coeff, EPS)), axis=1)
    coeff_entropy /= max(np.log(max(k, 2)), EPS)

    field_nearest_dist = np.asarray(data["best_dist"], dtype=np.float64).copy()
    field_coeff_max = np.zeros(target_points.shape[0], dtype=np.float64)
    field_coeff_entropy = np.ones(target_points.shape[0], dtype=np.float64)
    field_mean_normal_dot = np.zeros(target_points.shape[0], dtype=np.float64)
    field_mean_family_l1 = np.ones(target_points.shape[0], dtype=np.float64) * 2.0
    field_nearest_dist[ids] = np.min(candidate_dist, axis=1)
    field_coeff_max[ids] = np.max(candidate_coeff, axis=1)
    field_coeff_entropy[ids] = coeff_entropy
    field_mean_normal_dot[ids] = np.einsum("nk,nk->n", candidate_coeff, candidate_normal_dot)
    field_mean_family_l1[ids] = np.einsum("nk,nk->n", candidate_coeff, candidate_family_l1)

    return {
        "ids": ids,
        "supported": supported,
        "field_weights": field_weights,
        "family_mat": family_mat,
        "target_edges": build_edges(target_faces),
        "field_nearest_dist": field_nearest_dist,
        "field_coeff_max": field_coeff_max,
        "field_coeff_entropy": field_coeff_entropy,
        "field_mean_normal_dot": field_mean_normal_dot,
        "field_mean_family_l1": field_mean_family_l1,
        "field_candidate_count": int(k),
    }


def _finalize_field_diag(field: dict, candidate_coeff: np.ndarray | None = None) -> None:
    # Kept as a hook for future profiling; diagnostics are filled in _build_fast_field below via local arrays.
    del field, candidate_coeff


def _edge_jump(weights: np.ndarray, edges: np.ndarray) -> np.ndarray:
    if edges.size == 0:
        return np.zeros((0,), dtype=np.float64)
    return np.abs(weights[edges[:, 0]] - weights[edges[:, 1]]).sum(axis=1)


def _actual_gate(actual: dict, target_count: int) -> dict:
    base_rows = []
    raw_rows = []
    for key in actual:
        if key.endswith("__%s__supported" % BASE_KEY):
            pose = key.split("__", 1)[0]
            raw_key = "%s__%s__supported" % (pose, RAW_KEY)
            if raw_key not in actual:
                continue
            base_rows.append(np.asarray(actual[key], dtype=np.float64))
            raw_rows.append(np.asarray(actual[raw_key], dtype=np.float64))
    if not base_rows:
        raise RuntimeError("v087 actual DG error arrays 缺少 base/raw 支撑域数据")

    base_stack = np.vstack(base_rows)
    raw_stack = np.vstack(raw_rows)
    base_valid = np.isfinite(base_stack)
    raw_valid = np.isfinite(raw_stack)
    base_count = np.sum(base_valid, axis=0)
    raw_count = np.sum(raw_valid, axis=0)
    base_max = np.full((base_stack.shape[1],), np.nan, dtype=np.float64)
    raw_max = np.full((raw_stack.shape[1],), np.nan, dtype=np.float64)
    base_mean = np.full((base_stack.shape[1],), np.nan, dtype=np.float64)
    raw_mean = np.full((raw_stack.shape[1],), np.nan, dtype=np.float64)
    good_base = base_count > 0
    good_raw = raw_count > 0
    base_max[good_base] = np.max(np.where(base_valid[:, good_base], base_stack[:, good_base], -np.inf), axis=0)
    raw_max[good_raw] = np.max(np.where(raw_valid[:, good_raw], raw_stack[:, good_raw], -np.inf), axis=0)
    base_mean[good_base] = np.sum(np.where(base_valid[:, good_base], base_stack[:, good_base], 0.0), axis=0) / base_count[good_base]
    raw_mean[good_raw] = np.sum(np.where(raw_valid[:, good_raw], raw_stack[:, good_raw], 0.0), axis=0) / raw_count[good_raw]

    # nan 表示 unsupported 或未采样，不允许作为 actual-safe。
    finite = np.isfinite(base_max) & np.isfinite(raw_max)
    if base_max.shape[0] != target_count:
        raise RuntimeError("actual error 顶点数不匹配: %s != %s" % (base_max.shape[0], target_count))

    no_regress_005 = finite & (raw_max <= base_max + 0.005)
    no_regress_002 = finite & (raw_max <= base_max + 0.002)
    improves_001 = finite & ((raw_mean < base_mean - 0.001) | (raw_max < base_max - 0.001))
    improves_any = finite & ((raw_mean < base_mean - 1e-5) | (raw_max < base_max - 1e-5))

    return {
        "finite": finite,
        "base_max": base_max,
        "raw_max": raw_max,
        "base_mean": base_mean,
        "raw_mean": raw_mean,
        "no_regress_005": no_regress_005,
        "no_regress_002": no_regress_002,
        "improves_001": improves_001,
        "improves_any": improves_any,
    }


def _make_variant(
    name: str,
    raw: np.ndarray,
    base: np.ndarray,
    data: dict,
    field: dict,
    visibility: dict,
    actual_gate: dict,
    config: dict,
    base_error: np.ndarray,
    raw_error: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict]:
    supported = field["supported"]
    high = np.asarray(data["high_confidence"], dtype=bool)
    low = np.asarray(data["low_confidence"], dtype=bool)
    topology_bad = np.asarray(data["topology_discontinuity"], dtype=bool)
    semantic_bad = np.asarray(data["semantic_ambiguous"], dtype=bool) | np.asarray(data["face_semantic_discontinuity"], dtype=bool)
    visibility_risk = np.asarray(visibility["visibility_risk"], dtype=bool)
    normal_front = np.asarray(visibility["target_normal_front_layer_hit"], dtype=bool)
    segment_blocked = np.asarray(visibility["target_segment_blocked"], dtype=bool)
    regression_probe = np.asarray(visibility["candidate_regression_any"], dtype=bool)

    field_weights = field["field_weights"]
    family_mat = field["family_mat"]
    raw_family = _normalize_rows(raw @ family_mat)
    field_family = _normalize_rows(field_weights @ family_mat)
    base_family = _normalize_rows(base @ family_mat)

    row_l1 = np.abs(raw - base).sum(axis=1)
    field_weight_l1 = np.abs(raw - field_weights).sum(axis=1)
    raw_field_family_l1 = np.abs(raw_family - field_family).sum(axis=1)
    raw_base_family_l1 = np.abs(raw_family - base_family).sum(axis=1)

    allow = supported.copy()
    if config.get("require_high_confidence"):
        allow &= high
    if config.get("block_low_confidence"):
        allow &= ~low
    if config.get("block_topology"):
        allow &= ~topology_bad
    if config.get("block_semantic"):
        allow &= ~semantic_bad
    if config.get("block_visibility"):
        allow &= ~visibility_risk
    if config.get("block_normal_front"):
        allow &= ~normal_front
    if config.get("block_segment"):
        allow &= ~segment_blocked
    if config.get("block_v088_regression_probe"):
        allow &= ~regression_probe

    actual_mode = str(config.get("actual_mode", "none"))
    if actual_mode == "no_regress_005":
        allow &= actual_gate["no_regress_005"]
    elif actual_mode == "no_regress_002":
        allow &= actual_gate["no_regress_002"]
    elif actual_mode == "improve_001":
        allow &= actual_gate["no_regress_005"] & actual_gate["improves_001"]
    elif actual_mode == "improve_001_no_regress_002":
        allow &= actual_gate["no_regress_002"] & actual_gate["improves_001"]
    elif actual_mode == "improve_any":
        allow &= actual_gate["no_regress_005"] & actual_gate["improves_any"]
    elif actual_mode != "none":
        raise ValueError("未知 actual_mode: %s" % actual_mode)

    allow &= row_l1 <= float(config["row_l1_max"])
    allow &= field_weight_l1 <= float(config["field_weight_l1_max"])
    allow &= raw_field_family_l1 <= float(config["family_l1_max"])
    allow &= raw_base_family_l1 <= float(config["base_family_l1_max"])
    allow &= raw_error <= base_error + float(config["max_canonical_regression"])
    if config.get("require_canonical_improvement"):
        allow &= raw_error + float(config["min_canonical_improvement"]) < base_error

    out = base.copy()
    alpha = float(config.get("alpha", 1.0))
    out[allow] = (1.0 - alpha) * base[allow] + alpha * raw[allow]
    out = _topk_prune(out, SOLVE_TOPK, supported)

    before_jump = _edge_jump(base, field["target_edges"])
    after_jump = _edge_jump(out, field["target_edges"])
    bad_edges = field["target_edges"][after_jump > before_jump + float(config["max_new_edge_jump"])]
    edge_rejected = np.zeros(out.shape[0], dtype=bool)
    if bad_edges.size:
        for a, b in bad_edges.tolist():
            if allow[int(a)]:
                edge_rejected[int(a)] = True
            if allow[int(b)]:
                edge_rejected[int(b)] = True
        if np.any(edge_rejected):
            allow[edge_rejected] = False
            out = base.copy()
            out[allow] = (1.0 - alpha) * base[allow] + alpha * raw[allow]
            out = _topk_prune(out, SOLVE_TOPK, supported)

    diag = {
        "variant": name,
        "config": config,
        "accepted_count": int(np.sum(allow)),
        "edge_rejected_count": int(np.sum(edge_rejected)),
        "row_l1_accepted": _stats(row_l1[allow]),
        "field_weight_l1_accepted": _stats(field_weight_l1[allow]),
        "family_l1_accepted": _stats(raw_field_family_l1[allow]),
        "canonical_base_error_accepted": _stats(base_error[allow]),
        "canonical_raw_error_accepted": _stats(raw_error[allow]),
        "actual_base_max_accepted": _stats(actual_gate["base_max"][allow]),
        "actual_raw_max_accepted": _stats(actual_gate["raw_max"][allow]),
        "blocked_counts": {
            "unsupported": int(np.sum(~supported)),
            "low_confidence": int(np.sum(supported & low)),
            "topology": int(np.sum(supported & topology_bad)),
            "semantic": int(np.sum(supported & semantic_bad)),
            "visibility": int(np.sum(supported & visibility_risk)),
            "normal_front": int(np.sum(supported & normal_front)),
            "segment": int(np.sum(supported & segment_blocked)),
            "v088_regression_probe": int(np.sum(supported & regression_probe)),
            "actual_regress_005": int(np.sum(supported & ~actual_gate["no_regress_005"])),
            "actual_regress_002": int(np.sum(supported & ~actual_gate["no_regress_002"])),
            "actual_not_improve_001": int(np.sum(supported & ~actual_gate["improves_001"])),
            "row_l1_gt": int(np.sum(supported & (row_l1 > float(config["row_l1_max"])))),
            "field_weight_l1_gt": int(np.sum(supported & (field_weight_l1 > float(config["field_weight_l1_max"])))),
            "family_l1_gt": int(np.sum(supported & (raw_field_family_l1 > float(config["family_l1_max"])))),
            "base_family_l1_gt": int(np.sum(supported & (raw_base_family_l1 > float(config["base_family_l1_max"])))),
            "canonical_regression": int(np.sum(supported & (raw_error > base_error + float(config["max_canonical_regression"])))),
            "edge_rejected": int(np.sum(edge_rejected)),
        },
    }
    return out, allow, diag


def _summarize(variants: dict[str, np.ndarray], base: np.ndarray, data: dict) -> tuple[list[dict], dict]:
    reports = {}
    rows = []
    for name, weights in variants.items():
        report = evaluate_weights(weights, data)
        row = {
            "variant": name,
            "all_p95": report["global"]["all_channel_error"].get("p95"),
            "all_max": report["global"]["all_channel_error"].get("max"),
            "rotate_p95": report["global"]["by_group"]["rotate"].get("p95"),
            "translate_p95": report["global"]["by_group"]["translate"].get("p95"),
            "scale_p95": report["global"]["by_group"]["scale"].get("p95"),
            "changed_vs_base": int(np.sum(np.abs(weights - base).sum(axis=1) > 1e-8)),
        }
        rows.append(row)
        reports[name] = report
    rows.sort(key=lambda item: (float(item["all_p95"] or 1e9), float(item["all_max"] or 1e9)))
    return rows, reports


def main() -> dict:
    steps: list[dict] = []
    t_all = _now()

    t = _now()
    scene = _load_npz(SCENE_DATA)
    data = _load_npz(V087_INPUT)
    candidates = _load_npz(V087_CANDIDATES)
    visibility = _load_npz(V088_VISIBILITY)
    actual = _load_npz(V087_ACTUAL)
    target_count = int(np.asarray(data["target_points"]).shape[0])
    steps.append(
        _step(
            "load_inputs",
            t,
            {
                "scene_data": str(SCENE_DATA),
                "v087_input": str(V087_INPUT),
                "v087_candidates": str(V087_CANDIDATES),
                "v087_actual": str(V087_ACTUAL),
                "v088_visibility": str(V088_VISIBILITY),
                "target_vertices": target_count,
            },
        )
    )

    t = _now()
    field = _build_fast_field(scene, data)
    # Fill field diagnostics after build without carrying the large candidate arrays in the NPZ.
    supported = field["supported"]
    steps.append(
        _step(
            "build_fast_continuous_field",
            t,
            {
                "field_k": FIELD_K,
                "supported": int(np.sum(supported)),
                "unsupported": int(np.sum(~supported)),
                "field_candidate_count": int(field["field_candidate_count"]),
            },
        )
    )

    t = _now()
    base = _topk_prune(np.asarray(candidates["weights__%s" % BASE_KEY], dtype=np.float64), SOLVE_TOPK, supported)
    raw = _topk_prune(np.asarray(candidates["weights__%s" % RAW_KEY], dtype=np.float64), SOLVE_TOPK, supported)
    field_prior = _topk_prune(np.asarray(field["field_weights"], dtype=np.float64), SOLVE_TOPK, supported)
    base_error = per_vertex_error(base, data, ("rotate", "translate", "scale"))
    raw_error = per_vertex_error(raw, data, ("rotate", "translate", "scale"))
    actual_gate = _actual_gate(actual, target_count)
    steps.append(
        _step(
            "prepare_solver_inputs",
            t,
            {
                "base_key": BASE_KEY,
                "raw_key": RAW_KEY,
                "canonical_base_p95": _stats(base_error[supported]).get("p95"),
                "canonical_raw_p95": _stats(raw_error[supported]).get("p95"),
                "actual_no_regress_005": int(np.sum(actual_gate["no_regress_005"] & supported)),
                "actual_improves_001": int(np.sum(actual_gate["improves_001"] & supported)),
            },
        )
    )

    t = _now()
    configs = {
        "v089b_actual_safe_rt": {
            "actual_mode": "improve_any",
            "require_high_confidence": False,
            "block_low_confidence": False,
            "block_topology": False,
            "block_semantic": True,
            "block_visibility": False,
            "block_normal_front": True,
            "block_segment": False,
            "block_v088_regression_probe": False,
            "row_l1_max": 1.25,
            "field_weight_l1_max": 1.60,
            "family_l1_max": 0.85,
            "base_family_l1_max": 1.20,
            "max_canonical_regression": 0.0005,
            "require_canonical_improvement": False,
            "min_canonical_improvement": 0.0,
            "max_new_edge_jump": 0.55,
            "alpha": 1.0,
        },
        "v089b_visibility_guard_rt": {
            "actual_mode": "improve_any",
            "require_high_confidence": False,
            "block_low_confidence": False,
            "block_topology": True,
            "block_semantic": True,
            "block_visibility": True,
            "block_normal_front": True,
            "block_segment": True,
            "block_v088_regression_probe": True,
            "row_l1_max": 0.90,
            "field_weight_l1_max": 1.20,
            "family_l1_max": 0.60,
            "base_family_l1_max": 0.90,
            "max_canonical_regression": 0.00025,
            "require_canonical_improvement": False,
            "min_canonical_improvement": 0.0,
            "max_new_edge_jump": 0.40,
            "alpha": 1.0,
        },
        "v089b_final_strict_rt": {
            "actual_mode": "improve_001_no_regress_002",
            "require_high_confidence": True,
            "block_low_confidence": False,
            "block_topology": True,
            "block_semantic": True,
            "block_visibility": True,
            "block_normal_front": True,
            "block_segment": True,
            "block_v088_regression_probe": True,
            "row_l1_max": 0.65,
            "field_weight_l1_max": 0.95,
            "family_l1_max": 0.42,
            "base_family_l1_max": 0.65,
            "max_canonical_regression": 0.0001,
            "require_canonical_improvement": True,
            "min_canonical_improvement": 0.00005,
            "max_new_edge_jump": 0.30,
            "alpha": 1.0,
        },
        "v089b_final_alpha035_rt": {
            "actual_mode": "improve_any",
            "require_high_confidence": False,
            "block_low_confidence": False,
            "block_topology": True,
            "block_semantic": True,
            "block_visibility": True,
            "block_normal_front": True,
            "block_segment": False,
            "block_v088_regression_probe": True,
            "row_l1_max": 1.10,
            "field_weight_l1_max": 1.35,
            "family_l1_max": 0.68,
            "base_family_l1_max": 0.95,
            "max_canonical_regression": 0.00025,
            "require_canonical_improvement": False,
            "min_canonical_improvement": 0.0,
            "max_new_edge_jump": 0.42,
            "alpha": 0.35,
        },
    }

    variants = {
        "v089b_base_hybrid_m0010": base,
        "v089b_field_prior_topk_DIAGNOSTIC": field_prior,
    }
    accept_masks = {}
    guard_diag = {}
    for name, config in configs.items():
        weights, accept, diag = _make_variant(
            name=name,
            raw=raw,
            base=base,
            data=data,
            field=field,
            visibility=visibility,
            actual_gate=actual_gate,
            config=config,
            base_error=base_error,
            raw_error=raw_error,
        )
        variants[name] = weights
        accept_masks[name] = accept
        guard_diag[name] = diag
    steps.append(_step("build_guarded_variants", t, {"variant_count": len(variants)}))

    t = _now()
    ranking, reports = _summarize(variants, base, data)
    steps.append(_step("offline_all_influence_evaluation", t, {"ranking_count": len(ranking)}))

    t = _now()
    influence_names = [str(x) for x in np.asarray(data["influence_names"]).tolist()]
    save_payload = {
        "influence_names": np.asarray(influence_names, dtype="<U512"),
        "supported": supported,
        "visibility_risk": np.asarray(visibility["visibility_risk"], dtype=bool),
        "normal_front_layer": np.asarray(visibility["target_normal_front_layer_hit"], dtype=bool),
        "actual_raw_no_regress_005": actual_gate["no_regress_005"],
        "actual_raw_improves_001": actual_gate["improves_001"],
        "canonical_base_error": base_error.astype(np.float32),
        "canonical_raw_error": raw_error.astype(np.float32),
        **{f"weights__{name}": weights.astype(np.float32) for name, weights in variants.items()},
        **{f"accept__{name}": mask for name, mask in accept_masks.items()},
    }
    np.savez_compressed(str(OUT_NPZ), **save_payload)

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["variant", "all_p95", "all_max", "rotate_p95", "translate_p95", "scale_p95", "changed_vs_base"],
        )
        writer.writeheader()
        for row in ranking:
            writer.writerow(row)

    report = {
        "status": "SUCCESS",
        "inputs": {
            "scene_data": str(SCENE_DATA),
            "v087_input": str(V087_INPUT),
            "v087_candidates": str(V087_CANDIDATES),
            "v087_actual": str(V087_ACTUAL),
            "v088_visibility": str(V088_VISIBILITY),
        },
        "outputs": {"npz": str(OUT_NPZ), "json": str(OUT_JSON), "csv": str(OUT_CSV)},
        "steps": steps + [_step("write_outputs", t, {"npz": str(OUT_NPZ), "csv": str(OUT_CSV)})],
        "total_seconds": round(_now() - t_all, 3),
        "field_params": {
            "field_k": FIELD_K,
            "field_sigma": FIELD_SIGMA,
            "normal_power": NORMAL_POWER,
            "family_sigma": FAMILY_SIGMA,
            "field_topk": FIELD_TOPK,
            "solve_topk": SOLVE_TOPK,
        },
        "counts": {
            "target_vertices": target_count,
            "supported": int(np.sum(supported)),
            "unsupported": int(np.sum(~supported)),
            "visibility_risk_supported": int(np.sum(np.asarray(visibility["visibility_risk"], dtype=bool) & supported)),
            "normal_front_supported": int(np.sum(np.asarray(visibility["target_normal_front_layer_hit"], dtype=bool) & supported)),
            "actual_raw_no_regress_005_supported": int(np.sum(actual_gate["no_regress_005"] & supported)),
            "actual_raw_improves_001_supported": int(np.sum(actual_gate["improves_001"] & supported)),
        },
        "ranking": ranking,
        "guard_diag": guard_diag,
        "top_bad": {name: reports[name]["top_bad"][:15] for name in variants},
        "note": (
            "v089b 是快速最终候选测试：continuous field 只作先验，"
            "v087 raw 只在 actual-DG/v088/语义/拓扑/edge gate 同时通过时接管。"
            "是否可用仍以随后 Maya actual DG 多姿态验收为准。"
        ),
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    main()
