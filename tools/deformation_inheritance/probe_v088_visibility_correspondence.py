"""v088 correspondence visibility probe.

只读探针，不写 Maya。

目标：
1. 检查 v087 target->source provenance 是否有几何可见性问题。
2. 把 ray / visibility / normal layer 证据和真实 Maya DG 错误交叉验证。
3. 只有能提前命中实际失败区域的证据，才允许进入后续权重求解 gate。
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
SCENE_DATA = INFO_DIR / "v082_clean_scene_data.npz"
INPUT_NPZ = INFO_DIR / "v087_all_influence_input.npz"
ACTUAL_DG_ERRORS = INFO_DIR / "v087_actual_dg_multipose_error_arrays.npz"
OUT_JSON = INFO_DIR / "v088_visibility_correspondence_probe.json"
OUT_CSV = INFO_DIR / "v088_visibility_risk_vertices.csv"
OUT_NPZ = INFO_DIR / "v088_visibility_correspondence_probe.npz"

SOURCE_K = 256
TARGET_K = 256
RAY_EPS = 1e-4
OCCLUSION_MARGIN = 2e-3
NORMAL_RAY_LENGTH = 0.55
NORMAL_LAYER_CLOSE = 0.35

BASE_KEY = "v087_base_hybrid_m0010"
COMPARE_KEYS = [
    "v087_rt_raw_topk",
    "v087_rt_guard_strict",
    "v087_rt_guard_balanced",
    "v087_rts_guard_balanced",
]

RED_FACE_RANGES = {
    "mouth_left_9095_9150": (9095, 9150),
    "mouth_mid_9263_9318": (9263, 9318),
}


def _faces_from_flat(counts: np.ndarray, offsets: np.ndarray, flat: np.ndarray) -> list[np.ndarray]:
    faces: list[np.ndarray] = []
    for i, count in enumerate(counts.astype(int).tolist()):
        start = int(offsets[i])
        faces.append(flat[start : start + count].astype(np.int32))
    return faces


def _triangulate(faces: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    tris: list[tuple[int, int, int]] = []
    face_ids: list[int] = []
    for face_id, face in enumerate(faces):
        if len(face) < 3:
            continue
        if len(face) == 3:
            tris.append((int(face[0]), int(face[1]), int(face[2])))
            face_ids.append(face_id)
            continue
        root = int(face[0])
        for i in range(1, len(face) - 1):
            tris.append((root, int(face[i]), int(face[i + 1])))
            face_ids.append(face_id)
    return np.asarray(tris, dtype=np.int32), np.asarray(face_ids, dtype=np.int32)


def _safe_normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n < 1e-12] = 1.0
    return v / n


def _triangle_normals(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    a = points[tris[:, 0]]
    b = points[tris[:, 1]]
    c = points[tris[:, 2]]
    return _safe_normalize(np.cross(b - a, c - a))


def _vertex_normals(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    out = np.zeros_like(points, dtype=np.float64)
    tri_normals = _triangle_normals(points, tris)
    area_weight = np.linalg.norm(
        np.cross(points[tris[:, 1]] - points[tris[:, 0]], points[tris[:, 2]] - points[tris[:, 0]]),
        axis=1,
    )
    weighted = tri_normals * area_weight[:, None]
    for corner in range(3):
        np.add.at(out, tris[:, corner], weighted)
    return _safe_normalize(out)


def _tri_centroids(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    return (points[tris[:, 0]] + points[tris[:, 1]] + points[tris[:, 2]]) / 3.0


def _ray_triangles(origin: np.ndarray, direction: np.ndarray, tri_points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized Moller-Trumbore. Direction must be normalized."""
    v0 = tri_points[:, 0]
    v1 = tri_points[:, 1]
    v2 = tri_points[:, 2]
    e1 = v1 - v0
    e2 = v2 - v0
    pvec = np.cross(direction[None, :], e2)
    det = np.einsum("ij,ij->i", e1, pvec)
    valid = np.abs(det) > 1e-10
    inv_det = np.zeros_like(det)
    inv_det[valid] = 1.0 / det[valid]
    tvec = origin[None, :] - v0
    u = np.einsum("ij,ij->i", tvec, pvec) * inv_det
    qvec = np.cross(tvec, e1)
    v = np.einsum("j,ij->i", direction, qvec) * inv_det
    t = np.einsum("ij,ij->i", e2, qvec) * inv_det
    hit = valid & (u >= -1e-8) & (v >= -1e-8) & ((u + v) <= 1.0 + 1e-8) & (t > RAY_EPS)
    return hit, t


def _nearest_hit(
    points: np.ndarray,
    tris: np.ndarray,
    tri_normals: np.ndarray,
    tree: cKDTree,
    origin: np.ndarray,
    direction: np.ndarray,
    max_t: float,
    k: int,
    include_tri: int | None = None,
    exclude_vertex: int | None = None,
    exclude_tri_set: set[int] | None = None,
) -> tuple[int, float, float]:
    if max_t <= RAY_EPS:
        return -1, math.inf, 0.0
    direction = np.asarray(direction, dtype=np.float64)
    norm = float(np.linalg.norm(direction))
    if norm < 1e-12:
        return -1, math.inf, 0.0
    direction = direction / norm

    mid = origin + direction * min(max_t * 0.5, NORMAL_RAY_LENGTH * 0.5)
    _, ids = tree.query(mid, k=min(k, len(tris)))
    ids = np.atleast_1d(ids).astype(np.int64)
    if include_tri is not None:
        ids = np.unique(np.concatenate([ids, np.asarray([include_tri], dtype=np.int64)]))

    if exclude_vertex is not None:
        mask = ~np.any(tris[ids] == int(exclude_vertex), axis=1)
        ids = ids[mask]
    if exclude_tri_set:
        ids = np.asarray([idx for idx in ids.tolist() if int(idx) not in exclude_tri_set], dtype=np.int64)
    if ids.size == 0:
        return -1, math.inf, 0.0

    hit, t = _ray_triangles(origin, direction, points[tris[ids]])
    hit &= t < max_t
    if not np.any(hit):
        return -1, math.inf, 0.0
    local = int(np.argmin(np.where(hit, t, math.inf)))
    tri_id = int(ids[local])
    front_dot = float(np.dot(tri_normals[tri_id], direction))
    return tri_id, float(t[local]), front_dot


def _face_range_vertices(faces: list[np.ndarray], start: int, end: int) -> np.ndarray:
    verts: set[int] = set()
    for face_id in range(start, end + 1):
        if 0 <= face_id < len(faces):
            verts.update(int(x) for x in faces[face_id].tolist())
    return np.asarray(sorted(verts), dtype=np.int32)


def _actual_error_labels(vertex_count: int) -> dict[str, np.ndarray]:
    arrays = np.load(str(ACTUAL_DG_ERRORS), allow_pickle=True)
    base_max = np.full((vertex_count,), np.nan, dtype=np.float64)
    for key in arrays.files:
        if "__%s__supported" % BASE_KEY not in key:
            continue
        arr = np.asarray(arrays[key], dtype=np.float64)
        if np.all(np.isnan(base_max)):
            base_max = arr.copy()
        else:
            good = np.isfinite(arr)
            base_max[good] = np.fmax(np.nan_to_num(base_max[good], nan=-math.inf), arr[good])
    base_high_015 = np.isfinite(base_max) & (base_max > 0.015)
    base_high_020 = np.isfinite(base_max) & (base_max > 0.020)

    regression_any = np.zeros((vertex_count,), dtype=bool)
    for compare_key in COMPARE_KEYS:
        for key in arrays.files:
            if "__%s__supported" % compare_key not in key:
                continue
            pose = key.split("__", 1)[0]
            base_key = "%s__%s__supported" % (pose, BASE_KEY)
            if base_key not in arrays.files:
                continue
            cand = np.asarray(arrays[key], dtype=np.float64)
            base = np.asarray(arrays[base_key], dtype=np.float64)
            good = np.isfinite(cand) & np.isfinite(base)
            regression_any |= good & ((cand - base) > 0.005)

    return {
        "base_max_error": base_max,
        "base_high_015": base_high_015,
        "base_high_020": base_high_020,
        "candidate_regression_any": regression_any,
    }


def _score_flag(flag: np.ndarray, label: np.ndarray, domain: np.ndarray) -> dict:
    mask = domain.astype(bool)
    f = flag & mask
    y = label & mask
    tp = int(np.sum(f & y))
    fp = int(np.sum(f & ~y))
    fn = int(np.sum(~f & y))
    tn = int(np.sum(~f & ~y))
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    lift = precision / max(float(np.mean(y)) if np.any(mask) else 0.0, 1e-12)
    return {
        "flag_count": int(np.sum(f)),
        "label_count": int(np.sum(y)),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "lift": lift,
    }


def main() -> dict:
    scene = np.load(str(SCENE_DATA), allow_pickle=True)
    data = np.load(str(INPUT_NPZ), allow_pickle=True)

    source_points = np.asarray(scene["source_points"], dtype=np.float64)
    target_points = np.asarray(scene["target_points"], dtype=np.float64)
    source_faces = _faces_from_flat(scene["source_face_counts"], scene["source_face_offsets"], scene["source_face_vertices"])
    target_faces = _faces_from_flat(scene["target_face_counts"], scene["target_face_offsets"], scene["target_face_vertices"])
    source_tris, _source_face_ids = _triangulate(source_faces)
    target_tris, _target_face_ids = _triangulate(target_faces)

    # v087 source_tris should match scene triangulation, but keep the authoritative v087 order for best_tri.
    v087_source_tris = np.asarray(data["source_tris"], dtype=np.int32)
    if v087_source_tris.shape == source_tris.shape and np.all(v087_source_tris == source_tris):
        source_tris = v087_source_tris

    source_tri_normals = _triangle_normals(source_points, source_tris)
    target_tri_normals = _triangle_normals(target_points, target_tris)
    target_normals = _vertex_normals(target_points, target_tris)

    source_tree = cKDTree(_tri_centroids(source_points, source_tris))
    target_tree = cKDTree(_tri_centroids(target_points, target_tris))

    best_tri = np.asarray(data["best_tri"], dtype=np.int64)
    best_bary = np.asarray(data["best_bary"], dtype=np.float64)
    best_dist = np.asarray(data["best_dist"], dtype=np.float64)
    unsupported = np.asarray(data["unsupported"], dtype=bool)
    supported = ~unsupported
    high_confidence = np.asarray(data["high_confidence"], dtype=bool)
    low_confidence = np.asarray(data["low_confidence"], dtype=bool)

    safe_tri = best_tri.copy()
    safe_tri[best_tri < 0] = 0
    chosen_tris = source_tris[safe_tri]
    chosen_source_points = np.einsum("nk,nkc->nc", best_bary, source_points[chosen_tris])

    n = target_points.shape[0]
    source_occluded = np.zeros(n, dtype=bool)
    source_first_not_best = np.zeros(n, dtype=bool)
    source_first_tri = np.full(n, -1, dtype=np.int32)
    source_first_t = np.full(n, np.nan, dtype=np.float64)
    source_first_normal_dot = np.full(n, np.nan, dtype=np.float64)
    target_segment_blocked = np.zeros(n, dtype=bool)
    target_segment_tri = np.full(n, -1, dtype=np.int32)
    target_segment_t = np.full(n, np.nan, dtype=np.float64)
    target_plus_layer_hit = np.zeros(n, dtype=bool)
    target_minus_layer_hit = np.zeros(n, dtype=bool)
    target_plus_hit_t = np.full(n, np.nan, dtype=np.float64)
    target_minus_hit_t = np.full(n, np.nan, dtype=np.float64)
    target_plus_hit_front = np.zeros(n, dtype=bool)
    target_minus_hit_front = np.zeros(n, dtype=bool)

    supported_ids = np.where(supported)[0]
    for idx_i, vid in enumerate(supported_ids.tolist()):
        p = target_points[vid]
        q = chosen_source_points[vid]
        vec = q - p
        dist = float(np.linalg.norm(vec))
        if dist > RAY_EPS:
            direction = vec / dist
            s_tri, s_t, s_dot = _nearest_hit(
                source_points,
                source_tris,
                source_tri_normals,
                source_tree,
                p + direction * RAY_EPS,
                direction,
                dist + OCCLUSION_MARGIN,
                SOURCE_K,
                include_tri=int(best_tri[vid]),
            )
            source_first_tri[vid] = s_tri
            source_first_t[vid] = s_t if math.isfinite(s_t) else np.nan
            source_first_normal_dot[vid] = s_dot if math.isfinite(s_t) else np.nan
            if s_tri >= 0 and s_tri != int(best_tri[vid]):
                source_first_not_best[vid] = True
                if s_t < max(dist - OCCLUSION_MARGIN, RAY_EPS):
                    source_occluded[vid] = True

            t_tri, t_t, _t_dot = _nearest_hit(
                target_points,
                target_tris,
                target_tri_normals,
                target_tree,
                p + direction * RAY_EPS,
                direction,
                dist - OCCLUSION_MARGIN,
                TARGET_K,
                exclude_vertex=vid,
            )
            if t_tri >= 0:
                target_segment_blocked[vid] = True
                target_segment_tri[vid] = t_tri
                target_segment_t[vid] = t_t

        normal = target_normals[vid]
        for sign, hit_flag, hit_t, hit_front in (
            (1.0, target_plus_layer_hit, target_plus_hit_t, target_plus_hit_front),
            (-1.0, target_minus_layer_hit, target_minus_hit_t, target_minus_hit_front),
        ):
            direction = normal * sign
            t_tri, t_t, t_dot = _nearest_hit(
                target_points,
                target_tris,
                target_tri_normals,
                target_tree,
                p + direction * RAY_EPS,
                direction,
                NORMAL_RAY_LENGTH,
                TARGET_K,
                exclude_vertex=vid,
            )
            if t_tri >= 0 and t_t < NORMAL_LAYER_CLOSE:
                hit_flag[vid] = True
                hit_t[vid] = t_t
                # front-facing relative to ray direction.
                hit_front[vid] = t_dot < -0.15

        if idx_i and idx_i % 1000 == 0:
            print("[v088] visibility probe %d/%d" % (idx_i, supported_ids.size))

    layer_hit_any = target_plus_layer_hit | target_minus_layer_hit
    layer_front_any = target_plus_hit_front | target_minus_hit_front
    visibility_risk = supported & (
        source_occluded
        | target_segment_blocked
        | (layer_hit_any & layer_front_any)
        | (source_first_not_best & (best_dist > 0.03))
    )

    labels = _actual_error_labels(n)
    eval_domain = supported.copy()
    flag_map = {
        "source_occluded": source_occluded,
        "source_first_not_best": source_first_not_best,
        "target_segment_blocked": target_segment_blocked,
        "target_normal_layer_hit": layer_hit_any,
        "target_normal_front_layer_hit": layer_hit_any & layer_front_any,
        "v088_visibility_risk": visibility_risk,
        "v087_low_confidence": low_confidence,
    }
    label_map = {
        "actual_base_high_error_gt_0p015": labels["base_high_015"],
        "actual_base_high_error_gt_0p020": labels["base_high_020"],
        "actual_candidate_regression_any": labels["candidate_regression_any"],
    }
    scores = {
        flag_name: {label_name: _score_flag(flag, label, eval_domain) for label_name, label in label_map.items()}
        for flag_name, flag in flag_map.items()
    }

    red_ranges = {}
    for name, (start, end) in RED_FACE_RANGES.items():
        ids = _face_range_vertices(target_faces, start, end)
        ids = ids[supported[ids]]
        red_ranges[name] = {
            "count": int(ids.size),
            "visibility_risk": int(np.sum(visibility_risk[ids])),
            "source_occluded": int(np.sum(source_occluded[ids])),
            "source_first_not_best": int(np.sum(source_first_not_best[ids])),
            "target_segment_blocked": int(np.sum(target_segment_blocked[ids])),
            "target_normal_front_layer_hit": int(np.sum((layer_hit_any & layer_front_any)[ids])),
            "low_confidence": int(np.sum(low_confidence[ids])),
            "base_high_error_gt_0p015": int(np.sum(labels["base_high_015"][ids])),
            "candidate_regression_any": int(np.sum(labels["candidate_regression_any"][ids])),
        }

    risk_ids = np.where(visibility_risk)[0]
    risk_order = sorted(
        risk_ids.tolist(),
        key=lambda i: (
            -float(labels["base_max_error"][i]) if np.isfinite(labels["base_max_error"][i]) else 0.0,
            float(best_dist[i]),
        ),
    )

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "vertex",
                "best_dist",
                "base_max_error",
                "low_confidence",
                "source_occluded",
                "source_first_not_best",
                "target_segment_blocked",
                "target_normal_layer_hit",
                "target_normal_front_layer_hit",
                "source_first_tri",
                "best_tri",
            ],
        )
        writer.writeheader()
        for vid in risk_order[:1200]:
            writer.writerow(
                {
                    "vertex": int(vid),
                    "best_dist": "%.6f" % float(best_dist[vid]),
                    "base_max_error": "%.6f" % float(labels["base_max_error"][vid]) if np.isfinite(labels["base_max_error"][vid]) else "",
                    "low_confidence": int(low_confidence[vid]),
                    "source_occluded": int(source_occluded[vid]),
                    "source_first_not_best": int(source_first_not_best[vid]),
                    "target_segment_blocked": int(target_segment_blocked[vid]),
                    "target_normal_layer_hit": int(layer_hit_any[vid]),
                    "target_normal_front_layer_hit": int((layer_hit_any & layer_front_any)[vid]),
                    "source_first_tri": int(source_first_tri[vid]),
                    "best_tri": int(best_tri[vid]),
                }
            )

    np.savez_compressed(
        str(OUT_NPZ),
        visibility_risk=visibility_risk,
        source_occluded=source_occluded,
        source_first_not_best=source_first_not_best,
        target_segment_blocked=target_segment_blocked,
        target_normal_layer_hit=layer_hit_any,
        target_normal_front_layer_hit=layer_hit_any & layer_front_any,
        source_first_tri=source_first_tri,
        source_first_t=source_first_t,
        source_first_normal_dot=source_first_normal_dot,
        target_segment_tri=target_segment_tri,
        target_segment_t=target_segment_t,
        target_plus_layer_hit=target_plus_layer_hit,
        target_minus_layer_hit=target_minus_layer_hit,
        target_plus_hit_t=target_plus_hit_t,
        target_minus_hit_t=target_minus_hit_t,
        base_max_error=labels["base_max_error"],
        base_high_015=labels["base_high_015"],
        base_high_020=labels["base_high_020"],
        candidate_regression_any=labels["candidate_regression_any"],
    )

    report = {
        "status": "SUCCESS",
        "input": {
            "scene_data": str(SCENE_DATA),
            "v087_input": str(INPUT_NPZ),
            "actual_dg_errors": str(ACTUAL_DG_ERRORS),
        },
        "output": {
            "json": str(OUT_JSON),
            "csv": str(OUT_CSV),
            "npz": str(OUT_NPZ),
        },
        "params": {
            "source_k": SOURCE_K,
            "target_k": TARGET_K,
            "ray_eps": RAY_EPS,
            "occlusion_margin": OCCLUSION_MARGIN,
            "normal_ray_length": NORMAL_RAY_LENGTH,
            "normal_layer_close": NORMAL_LAYER_CLOSE,
        },
        "counts": {
            "target_vertices": int(n),
            "supported": int(np.sum(supported)),
            "high_confidence": int(np.sum(high_confidence)),
            "low_confidence": int(np.sum(low_confidence)),
            "source_occluded": int(np.sum(source_occluded & supported)),
            "source_first_not_best": int(np.sum(source_first_not_best & supported)),
            "target_segment_blocked": int(np.sum(target_segment_blocked & supported)),
            "target_normal_layer_hit": int(np.sum(layer_hit_any & supported)),
            "target_normal_front_layer_hit": int(np.sum((layer_hit_any & layer_front_any) & supported)),
            "visibility_risk": int(np.sum(visibility_risk)),
        },
        "scores": scores,
        "red_face_ranges": red_ranges,
        "top_visibility_risk_vertices": [int(x) for x in risk_order[:120]],
        "conclusion_hint": (
            "若 visibility_risk 对 actual_base_high_error 或 candidate_regression 的 lift 接近 1，"
            "说明射线证据不能单独作为高级 gate；只能作为低置信 penalty。"
        ),
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    main()
