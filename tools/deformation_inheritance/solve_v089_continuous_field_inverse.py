"""v089 continuous deformation field inverse solve.

目标：
1. 不再只用单个 closest source triangle。
2. 用多 source triangle 形成连续权重/形变场。
3. 把 v088 ray/visibility 风险作为 gate，而不是直接删映射。
4. 生成候选权重，并输出每一步可审计 JSON/CSV/NPZ。

本脚本只离线求解，不写 Maya。
"""

from __future__ import annotations

import csv
import json
import math
import sys
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
    _apply_matrix,
    _channel_delta,
    _channels,
    _map_source_values,
    _stats,
    _topk_prune,
    evaluate_weights,
    per_vertex_error,
)


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
SCENE_DATA = INFO_DIR / "v082_clean_scene_data.npz"
V087_INPUT = INFO_DIR / "v087_all_influence_input.npz"
V088_VISIBILITY = INFO_DIR / "v088_visibility_correspondence_probe.npz"
OUT_NPZ = INFO_DIR / "v089_continuous_field_candidates.npz"
OUT_JSON = INFO_DIR / "v089_continuous_field_summary.json"
OUT_CSV = INFO_DIR / "v089_continuous_field_summary.csv"

FIELD_K = 64
FIELD_SIGMA = 0.12
NORMAL_POWER = 1.8
FAMILY_SIGMA = 0.70
FIELD_TOPK = 12
SOLVE_TOPK = 8
ACTIVE_WEIGHT_EPS = 1e-3
EPS = 1e-12


def _family_matrix(influence_names: np.ndarray) -> np.ndarray:
    out = np.zeros((len(influence_names), len(FAMILIES)), dtype=np.float64)
    for i, name in enumerate(influence_names.tolist()):
        family = classify_influence(str(name))
        out[i, FAMILIES.index(family)] = 1.0
    return out


def _tri_centroids(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    return (points[tris[:, 0]] + points[tris[:, 1]] + points[tris[:, 2]]) / 3.0


def _normalize_rows(weights: np.ndarray) -> np.ndarray:
    out = np.maximum(np.asarray(weights, dtype=np.float64), 0.0).copy()
    sums = out.sum(axis=1, keepdims=True)
    good = sums[:, 0] > EPS
    out[good] /= sums[good]
    return out


def _load() -> tuple[dict, dict, dict]:
    scene = {key: value for key, value in np.load(str(SCENE_DATA), allow_pickle=True).items()}
    data = {key: value for key, value in np.load(str(V087_INPUT), allow_pickle=True).items()}
    visibility = {key: value for key, value in np.load(str(V088_VISIBILITY), allow_pickle=True).items()}
    return scene, data, visibility


def _build_continuous_field(scene: dict, data: dict, visibility: dict) -> dict:
    source_points = np.asarray(scene["source_points"], dtype=np.float64)
    target_points = np.asarray(scene["target_points"], dtype=np.float64)
    source_weights = np.asarray(data["source_weights"], dtype=np.float64)
    base_weights = np.asarray(data["base_weights"], dtype=np.float64)
    unsupported = np.asarray(data["unsupported"], dtype=bool)
    supported = ~unsupported
    source_faces = faces_from_flat(scene["source_face_counts"], scene["source_face_offsets"], scene["source_face_vertices"])
    target_faces = faces_from_flat(scene["target_face_counts"], scene["target_face_offsets"], scene["target_face_vertices"])
    source_tris = triangulate(source_faces)
    target_tris = triangulate(target_faces)
    v087_source_tris = np.asarray(data["source_tris"], dtype=np.int32)
    if source_tris.shape == v087_source_tris.shape and np.all(source_tris == v087_source_tris):
        source_tris = v087_source_tris

    source_tri_normals = triangle_normals(source_points, source_tris)
    target_normals = vertex_normals(target_points, target_tris)
    tree = cKDTree(_tri_centroids(source_points, source_tris))

    influence_names = np.asarray(data["influence_names"])
    family_mat = _family_matrix(influence_names)
    source_family_weights = source_weights @ family_mat
    source_family_weights = _normalize_rows(source_family_weights)
    base_family_weights = _normalize_rows(base_weights @ family_mat)

    ids = np.where(supported)[0].astype(np.int32)
    _, candidate_tris = tree.query(target_points[ids], k=min(FIELD_K, len(source_tris)))
    candidate_tris = np.atleast_2d(candidate_tris)
    if candidate_tris.shape[0] != ids.size:
        candidate_tris = candidate_tris.T

    n, k = candidate_tris.shape
    candidate_points = np.zeros((n, k, 3), dtype=np.float64)
    candidate_bary = np.zeros((n, k, 3), dtype=np.float64)
    candidate_dist = np.zeros((n, k), dtype=np.float64)
    candidate_coeff = np.zeros((n, k), dtype=np.float64)
    candidate_family = np.zeros((n, k, len(FAMILIES)), dtype=np.float64)
    candidate_normal_dot = np.zeros((n, k), dtype=np.float64)

    for row, vertex_id in enumerate(ids.tolist()):
        cand = candidate_tris[row]
        tris = source_tris[cand]
        a = source_points[tris[:, 0]]
        b = source_points[tris[:, 1]]
        c = source_points[tris[:, 2]]
        points, bary, dist2 = closest_point_tri_batch(target_points[vertex_id], a, b, c)
        dist = np.sqrt(np.maximum(dist2, 0.0))
        fam = (
            bary[:, 0:1] * source_family_weights[tris[:, 0]]
            + bary[:, 1:2] * source_family_weights[tris[:, 1]]
            + bary[:, 2:3] * source_family_weights[tris[:, 2]]
        )
        fam = _normalize_rows(fam)
        normal_dot = np.clip(source_tri_normals[cand] @ target_normals[vertex_id], -1.0, 1.0)
        family_l1 = np.abs(fam - base_family_weights[vertex_id][None, :]).sum(axis=1)

        dist_score = np.exp(-((dist / FIELD_SIGMA) ** 2))
        normal_score = np.power(np.clip((normal_dot + 1.0) * 0.5, 0.0, 1.0), NORMAL_POWER)
        family_score = np.exp(-(family_l1 / FAMILY_SIGMA))
        coeff = dist_score * (0.10 + 0.90 * normal_score) * (0.20 + 0.80 * family_score)
        if np.all(coeff <= EPS):
            coeff = dist_score + EPS
        coeff = coeff / max(float(coeff.sum()), EPS)

        candidate_points[row] = points
        candidate_bary[row] = bary
        candidate_dist[row] = dist
        candidate_coeff[row] = coeff
        candidate_family[row] = fam
        candidate_normal_dot[row] = normal_dot

        if row and row % 1500 == 0:
            print("[v089] continuous field candidates %d/%d" % (row, n))

    # Continuous source weight field.
    source_tris_for_candidates = source_tris[candidate_tris]
    field_weights_supported = np.zeros((n, source_weights.shape[1]), dtype=np.float64)
    for joint_id in range(source_weights.shape[1]):
        sw = (
            candidate_bary[:, :, 0] * source_weights[source_tris_for_candidates[:, :, 0], joint_id]
            + candidate_bary[:, :, 1] * source_weights[source_tris_for_candidates[:, :, 1], joint_id]
            + candidate_bary[:, :, 2] * source_weights[source_tris_for_candidates[:, :, 2], joint_id]
        )
        field_weights_supported[:, joint_id] = np.einsum("nk,nk->n", candidate_coeff, sw)
    field_weights = np.asarray(base_weights, dtype=np.float64).copy()
    field_weights[ids] = field_weights_supported
    field_weights = _topk_prune(field_weights, FIELD_TOPK, supported)

    visibility_risk = np.asarray(visibility["visibility_risk"], dtype=bool)
    normal_front_layer = np.asarray(visibility["target_normal_front_layer_hit"], dtype=bool)
    target_segment_blocked = np.asarray(visibility["target_segment_blocked"], dtype=bool)

    return {
        "ids": ids,
        "candidate_tris": candidate_tris,
        "source_tris_for_candidates": source_tris_for_candidates,
        "candidate_points": candidate_points,
        "candidate_bary": candidate_bary,
        "candidate_coeff": candidate_coeff,
        "candidate_dist": candidate_dist,
        "candidate_family": candidate_family,
        "candidate_normal_dot": candidate_normal_dot,
        "field_weights": field_weights,
        "supported": supported,
        "visibility_risk": visibility_risk,
        "normal_front_layer": normal_front_layer,
        "target_segment_blocked": target_segment_blocked,
        "source_family_weights": source_family_weights,
        "base_family_weights": base_family_weights,
        "family_mat": family_mat,
        "target_faces": target_faces,
        "target_edges": build_edges(target_faces),
    }


def _field_expected_motion(data: dict, field: dict, joint_id: int, channel: str) -> np.ndarray:
    ids = field["ids"]
    candidate_points = field["candidate_points"]
    source_tris_for_candidates = field["source_tris_for_candidates"]
    candidate_bary = field["candidate_bary"]
    coeff = field["candidate_coeff"]
    source_weights = np.asarray(data["source_weights"], dtype=np.float64)
    source_matrices = np.asarray(data["source_matrices"], dtype=np.float64)
    source_pivots = np.asarray(data["source_pivots"], dtype=np.float64)

    n, k, _ = candidate_points.shape
    flat_points = candidate_points.reshape(n * k, 3)
    source_neutral = _apply_matrix(flat_points, source_matrices[joint_id])
    delta = _channel_delta(source_pivots[joint_id], channel)
    source_delta = (_apply_matrix(flat_points, source_matrices[joint_id] @ delta) - source_neutral).reshape(n, k, 3)
    sw = (
        candidate_bary[:, :, 0] * source_weights[source_tris_for_candidates[:, :, 0], joint_id]
        + candidate_bary[:, :, 1] * source_weights[source_tris_for_candidates[:, :, 1], joint_id]
        + candidate_bary[:, :, 2] * source_weights[source_tris_for_candidates[:, :, 2], joint_id]
    )
    mapped = np.einsum("nk,nk,nkc->nc", coeff, sw, source_delta)
    out = np.zeros((np.asarray(data["target_points"]).shape[0], 3), dtype=np.float64)
    out[ids] = mapped
    return out


def _solve_inverse(data: dict, field: dict, channel_groups: tuple[str, ...]) -> np.ndarray:
    channels = _channels(channel_groups)
    target_points = np.asarray(data["target_points"], dtype=np.float64)
    target_matrices = np.asarray(data["target_matrices"], dtype=np.float64)
    source_weights = np.asarray(data["source_weights"], dtype=np.float64)
    target_pivots = np.asarray(data["target_pivots"], dtype=np.float64)
    base = np.asarray(data["base_weights"], dtype=np.float64)
    supported = field["supported"]

    numerator = np.zeros((target_points.shape[0], source_weights.shape[1]), dtype=np.float64)
    denominator = np.zeros_like(numerator)
    ids = field["ids"]
    for joint_id in range(source_weights.shape[1]):
        target_neutral = _apply_matrix(target_points, target_matrices[joint_id])
        for channel in channels:
            expected = _field_expected_motion(data, field, joint_id, channel)
            delta = _channel_delta(target_pivots[joint_id], channel)
            target_delta = _apply_matrix(target_points, target_matrices[joint_id] @ delta) - target_neutral
            numerator[ids, joint_id] += np.einsum("ij,ij->i", target_delta[ids], expected[ids])
            denominator[ids, joint_id] += np.einsum("ij,ij->i", target_delta[ids], target_delta[ids])
        if joint_id and joint_id % 24 == 0:
            print("[v089] inverse field joint %d/%d" % (joint_id, source_weights.shape[1]))

    raw = np.zeros_like(numerator)
    valid = denominator > EPS
    raw[valid] = numerator[valid] / denominator[valid]
    raw = np.clip(raw, 0.0, 1.0)
    raw[~supported] = base[~supported]
    return _normalize_rows(raw)


def _field_per_vertex_error(weights: np.ndarray, data: dict, field: dict, channel_groups: tuple[str, ...]) -> np.ndarray:
    channels = _channels(channel_groups)
    target_points = np.asarray(data["target_points"], dtype=np.float64)
    target_matrices = np.asarray(data["target_matrices"], dtype=np.float64)
    target_pivots = np.asarray(data["target_pivots"], dtype=np.float64)
    source_weights = np.asarray(data["source_weights"], dtype=np.float64)
    supported = field["supported"]
    ids = field["ids"]
    err_sum = np.zeros((target_points.shape[0],), dtype=np.float64)
    count = np.zeros_like(err_sum)
    field_weight = field["field_weights"]
    for joint_id in range(source_weights.shape[1]):
        active = supported & ((field_weight[:, joint_id] > ACTIVE_WEIGHT_EPS) | (weights[:, joint_id] > ACTIVE_WEIGHT_EPS))
        if not np.any(active):
            continue
        target_neutral = _apply_matrix(target_points, target_matrices[joint_id])
        for channel in channels:
            expected = _field_expected_motion(data, field, joint_id, channel)
            delta = _channel_delta(target_pivots[joint_id], channel)
            target_delta = _apply_matrix(target_points, target_matrices[joint_id] @ delta) - target_neutral
            target_motion = weights[:, joint_id][:, None] * target_delta
            err = np.linalg.norm(target_motion - expected, axis=1)
            active_ids = ids[np.asarray(active[ids], dtype=bool)]
            err_sum[active_ids] += err[active_ids]
            count[active_ids] += 1.0
    out = np.zeros_like(err_sum)
    good = count > 0
    out[good] = err_sum[good] / count[good]
    return out


def _edge_jump(weights: np.ndarray, edges: np.ndarray) -> np.ndarray:
    if edges.size == 0:
        return np.zeros((0,), dtype=np.float64)
    return np.abs(weights[edges[:, 0]] - weights[edges[:, 1]]).sum(axis=1)


def _make_guarded(raw: np.ndarray, base: np.ndarray, data: dict, field: dict, config: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    supported = field["supported"]
    high = np.asarray(data["high_confidence"], dtype=bool)
    low = np.asarray(data["low_confidence"], dtype=bool)
    topology_bad = np.asarray(data["topology_discontinuity"], dtype=bool)
    semantic_bad = np.asarray(data["semantic_ambiguous"], dtype=bool) | np.asarray(data["face_semantic_discontinuity"], dtype=bool)
    visibility_risk = field["visibility_risk"]
    normal_front = field["normal_front_layer"]
    segment_blocked = field["target_segment_blocked"]
    field_weights = field["field_weights"]
    edges = field["target_edges"]

    row_l1 = np.abs(raw - base).sum(axis=1)
    semantic_l1 = np.abs(raw @ field["family_mat"] - field_weights @ field["family_mat"]).sum(axis=1)
    field_base_error = _field_per_vertex_error(base, data, field, ("rotate", "translate", "scale"))
    field_raw_error = _field_per_vertex_error(raw, data, field, ("rotate", "translate", "scale"))
    canonical_base_error = per_vertex_error(base, data, ("rotate", "translate", "scale"))
    canonical_raw_error = per_vertex_error(raw, data, ("rotate", "translate", "scale"))

    allow = supported.copy()
    if config.get("require_high_confidence"):
        allow &= high
    if config.get("block_low_confidence"):
        allow &= ~low
    if config.get("block_topology_discontinuity"):
        allow &= ~topology_bad
    if config.get("block_semantic_ambiguous"):
        allow &= ~semantic_bad
    if config.get("block_visibility_risk"):
        allow &= ~visibility_risk
    if config.get("block_normal_front_layer"):
        allow &= ~normal_front
    if config.get("block_segment_blocked"):
        allow &= ~segment_blocked
    allow &= row_l1 <= float(config["row_l1_max"])
    allow &= semantic_l1 <= float(config["semantic_l1_max"])
    allow &= field_raw_error + float(config["min_field_improvement"]) < field_base_error
    allow &= canonical_raw_error <= canonical_base_error + float(config["max_canonical_regression"])

    out = base.copy()
    out[allow] = raw[allow]

    # One pass neighbor guard: reject accepted rows that would introduce a large edge jump.
    edge_jump_after = _edge_jump(out, edges)
    edge_jump_before = _edge_jump(base, edges)
    bad_edges = edges[edge_jump_after > (edge_jump_before + float(config["max_new_edge_jump"]))]
    edge_rejected = np.zeros(out.shape[0], dtype=bool)
    if bad_edges.size:
        for a, b in bad_edges.tolist():
            if allow[int(a)]:
                edge_rejected[int(a)] = True
            if allow[int(b)]:
                edge_rejected[int(b)] = True
        allow[edge_rejected] = False
        out = base.copy()
        out[allow] = raw[allow]

    out = _topk_prune(out, SOLVE_TOPK, supported)
    diag = {
        "config": config,
        "accepted_count": int(np.sum(allow)),
        "edge_rejected_count": int(np.sum(edge_rejected)),
        "supported_count": int(np.sum(supported)),
        "row_l1_accepted": _stats(row_l1[allow]),
        "semantic_l1_accepted": _stats(semantic_l1[allow]),
        "field_base_error_supported": _stats(field_base_error[supported]),
        "field_raw_error_supported": _stats(field_raw_error[supported]),
        "canonical_base_error_supported": _stats(canonical_base_error[supported]),
        "canonical_raw_error_supported": _stats(canonical_raw_error[supported]),
        "blocked_counts": {
            "unsupported": int(np.sum(~supported)),
            "low_confidence": int(np.sum(supported & low)),
            "topology_discontinuity": int(np.sum(supported & topology_bad)),
            "semantic_ambiguous": int(np.sum(supported & semantic_bad)),
            "visibility_risk": int(np.sum(supported & visibility_risk)),
            "normal_front_layer": int(np.sum(supported & normal_front)),
            "target_segment_blocked": int(np.sum(supported & segment_blocked)),
            "row_l1_gt_threshold": int(np.sum(supported & (row_l1 > float(config["row_l1_max"])))),
            "semantic_l1_gt_threshold": int(np.sum(supported & (semantic_l1 > float(config["semantic_l1_max"])))),
            "no_field_improvement": int(np.sum(supported & (field_raw_error + float(config["min_field_improvement"]) >= field_base_error))),
            "canonical_regression": int(np.sum(supported & (canonical_raw_error > canonical_base_error + float(config["max_canonical_regression"])))),
        },
    }
    return out, allow, diag


def _summarize_variants(variants: dict[str, np.ndarray], base: np.ndarray, data: dict, field: dict) -> tuple[list[dict], dict]:
    reports = {}
    rows = []
    supported = field["supported"]
    for name, weights in variants.items():
        canonical = evaluate_weights(weights, data)
        field_err = _field_per_vertex_error(weights, data, field, ("rotate", "translate", "scale"))
        row = {
            "variant": name,
            "canonical_all_p95": canonical["global"]["all_channel_error"].get("p95"),
            "canonical_all_max": canonical["global"]["all_channel_error"].get("max"),
            "canonical_rotate_p95": canonical["global"]["by_group"]["rotate"].get("p95"),
            "canonical_translate_p95": canonical["global"]["by_group"]["translate"].get("p95"),
            "canonical_scale_p95": canonical["global"]["by_group"]["scale"].get("p95"),
            "field_error_p95": _stats(field_err[supported]).get("p95"),
            "field_error_max": _stats(field_err[supported]).get("max"),
            "changed_vs_base": int(np.sum(np.abs(weights - base).sum(axis=1) > 1e-8)),
        }
        rows.append(row)
        reports[name] = {
            "canonical": canonical,
            "field_error_supported": _stats(field_err[supported]),
        }
    rows.sort(key=lambda row: (float(row["canonical_all_p95"] or 1e9), float(row["field_error_p95"] or 1e9)))
    return rows, reports


def main() -> dict:
    scene, data, visibility = _load()
    field = _build_continuous_field(scene, data, visibility)
    supported = field["supported"]
    base = _topk_prune(np.asarray(data["base_weights"], dtype=np.float64), SOLVE_TOPK, supported)
    field_prior = _topk_prune(field["field_weights"], SOLVE_TOPK, supported)
    raw_rt = _topk_prune(_solve_inverse(data, field, ("rotate", "translate")), SOLVE_TOPK, supported)
    raw_rts = _topk_prune(_solve_inverse(data, field, ("rotate", "translate", "scale")), SOLVE_TOPK, supported)

    guard_configs = {
        "v089_field_guard_strict": {
            "require_high_confidence": True,
            "block_low_confidence": False,
            "block_topology_discontinuity": True,
            "block_semantic_ambiguous": True,
            "block_visibility_risk": True,
            "block_normal_front_layer": True,
            "block_segment_blocked": True,
            "row_l1_max": 0.40,
            "semantic_l1_max": 0.32,
            "min_field_improvement": 0.001,
            "max_canonical_regression": 0.0001,
            "max_new_edge_jump": 0.35,
        },
        "v089_field_guard_balanced": {
            "require_high_confidence": False,
            "block_low_confidence": False,
            "block_topology_discontinuity": True,
            "block_semantic_ambiguous": True,
            "block_visibility_risk": True,
            "block_normal_front_layer": True,
            "block_segment_blocked": False,
            "row_l1_max": 0.65,
            "semantic_l1_max": 0.45,
            "min_field_improvement": 0.0005,
            "max_canonical_regression": 0.0002,
            "max_new_edge_jump": 0.45,
        },
        "v089_field_guard_surface": {
            "require_high_confidence": False,
            "block_low_confidence": False,
            "block_topology_discontinuity": True,
            "block_semantic_ambiguous": True,
            "block_visibility_risk": False,
            "block_normal_front_layer": True,
            "block_segment_blocked": False,
            "row_l1_max": 0.55,
            "semantic_l1_max": 0.40,
            "min_field_improvement": 0.0005,
            "max_canonical_regression": 0.0001,
            "max_new_edge_jump": 0.30,
        },
    }

    variants = {
        "v089_base_hybrid_m0010": base,
        "v089_field_prior_topk": field_prior,
        "v089_field_inverse_raw_rt": raw_rt,
        "v089_field_inverse_raw_rts": raw_rts,
    }
    accept_masks = {}
    guard_diag = {}
    for name, config in guard_configs.items():
        raw = raw_rts if name.endswith("surface") else raw_rt
        weights, accept, diag = _make_guarded(raw, base, data, field, config)
        variants[name] = weights
        accept_masks[name] = accept
        guard_diag[name] = diag

    ranking, reports = _summarize_variants(variants, base, data, field)

    influence_names = [str(x) for x in np.asarray(data["influence_names"]).tolist()]
    save_payload = {
        "influence_names": np.asarray(influence_names, dtype="<U512"),
        "supported": supported,
        "visibility_risk": field["visibility_risk"],
        **{f"weights__{name}": weights.astype(np.float32) for name, weights in variants.items()},
        **{f"accept__{name}": mask for name, mask in accept_masks.items()},
    }
    np.savez_compressed(str(OUT_NPZ), **save_payload)

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "variant",
            "canonical_all_p95",
            "canonical_all_max",
            "canonical_rotate_p95",
            "canonical_translate_p95",
            "canonical_scale_p95",
            "field_error_p95",
            "field_error_max",
            "changed_vs_base",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in ranking:
            writer.writerow(row)

    report = {
        "status": "SUCCESS",
        "inputs": {
            "scene_data": str(SCENE_DATA),
            "v087_input": str(V087_INPUT),
            "v088_visibility": str(V088_VISIBILITY),
        },
        "outputs": {
            "npz": str(OUT_NPZ),
            "json": str(OUT_JSON),
            "csv": str(OUT_CSV),
        },
        "field_params": {
            "field_k": FIELD_K,
            "field_sigma": FIELD_SIGMA,
            "normal_power": NORMAL_POWER,
            "family_sigma": FAMILY_SIGMA,
            "field_topk": FIELD_TOPK,
            "solve_topk": SOLVE_TOPK,
        },
        "counts": {
            "target_vertices": int(np.asarray(data["target_points"]).shape[0]),
            "supported": int(np.sum(supported)),
            "visibility_risk": int(np.sum(field["visibility_risk"] & supported)),
            "normal_front_layer": int(np.sum(field["normal_front_layer"] & supported)),
            "target_segment_blocked": int(np.sum(field["target_segment_blocked"] & supported)),
        },
        "ranking": ranking,
        "guard_diag": guard_diag,
        "top_bad": {name: reports[name]["canonical"]["top_bad"][:15] for name in variants},
        "note": "v089 是连续形变场反求测试。候选是否可用必须继续看 Maya actual DG。离线排名不能定版。",
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    main()
