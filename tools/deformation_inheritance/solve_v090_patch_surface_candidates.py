"""v090 patch-level surface objective 权重候选生成。

本脚本只做离线求解，不写 Maya。

目标：
1. 消费 v090 真实 Maya DG 面级评估结果。
2. 在三角面 patch 级别选择“真实 surface score 下降且无明显回归”的候选。
3. 把面级选择投回顶点权重，输出可写入 Maya 的新候选。

注意：
- 这里不是重新发明全局权重传递，而是在已经存在的 v089b 候选之间做
  patch-level objective selection。
- field_prior 只允许在极严格的小范围 patch 内使用，因为 v090 已证明它全局会炸。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from collections import defaultdict, deque

import numpy as np


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
SCENE_DATA = INFO_DIR / "v082_clean_scene_data.npz"
V089B_WEIGHTS = INFO_DIR / "v089b_fast_continuous_field_candidates.npz"
V090_SURFACE = INFO_DIR / "v090_patch_surface_objective_arrays.npz"

OUT_NPZ = INFO_DIR / "v090_patch_surface_candidates.npz"
OUT_JSON = INFO_DIR / "v090_patch_surface_candidates_summary.json"
OUT_CSV = INFO_DIR / "v090_patch_surface_candidates_summary.csv"

BASE_KEY = "v089b_base_hybrid_m0010"
SOURCE_KEYS = [
    "v089b_actual_safe_rt",
    "v089b_visibility_guard_rt",
    "v089b_final_alpha035_rt",
    "v089b_field_prior_topk_DIAGNOSTIC",
]

EPS = 1e-12
TOPK = 10


RULES = {
    "v089b_actual_safe_rt": {
        "min_improve": 0.006,
        "max_regress": 0.003,
        "min_component_tris": 5,
        "min_vertex_vote": 2,
        "min_vertex_ratio": 0.35,
    },
    "v089b_visibility_guard_rt": {
        "min_improve": 0.006,
        "max_regress": 0.003,
        "min_component_tris": 5,
        "min_vertex_vote": 2,
        "min_vertex_ratio": 0.35,
    },
    "v089b_final_alpha035_rt": {
        "min_improve": 0.005,
        "max_regress": 0.002,
        "min_component_tris": 4,
        "min_vertex_vote": 2,
        "min_vertex_ratio": 0.35,
    },
    "v089b_field_prior_topk_DIAGNOSTIC": {
        "min_improve": 0.025,
        "max_regress": 0.001,
        "min_component_tris": 8,
        "min_vertex_vote": 3,
        "min_vertex_ratio": 0.55,
    },
}


VARIANTS = {
    "v090_patch_direct": {"blend": 1.00, "smooth_iters": 0, "smooth_strength": 0.0},
    "v090_patch_blend060": {"blend": 0.60, "smooth_iters": 0, "smooth_strength": 0.0},
    "v090_patch_blend035": {"blend": 0.35, "smooth_iters": 0, "smooth_strength": 0.0},
    "v090_patch_blend060_smooth": {"blend": 0.60, "smooth_iters": 2, "smooth_strength": 0.35},
}


PROFILES = {
    "strict": RULES,
    "balanced": {
        "v089b_actual_safe_rt": {
            "min_improve": 0.003,
            "max_regress": 0.005,
            "min_component_tris": 3,
            "min_vertex_vote": 1,
            "min_vertex_ratio": 0.20,
        },
        "v089b_visibility_guard_rt": {
            "min_improve": 0.003,
            "max_regress": 0.005,
            "min_component_tris": 3,
            "min_vertex_vote": 1,
            "min_vertex_ratio": 0.20,
        },
        "v089b_final_alpha035_rt": {
            "min_improve": 0.003,
            "max_regress": 0.004,
            "min_component_tris": 3,
            "min_vertex_vote": 1,
            "min_vertex_ratio": 0.20,
        },
        "v089b_field_prior_topk_DIAGNOSTIC": {
            "min_improve": 0.025,
            "max_regress": 0.003,
            "min_component_tris": 3,
            "min_vertex_vote": 2,
            "min_vertex_ratio": 0.35,
        },
    },
    "broad": {
        "v089b_actual_safe_rt": {
            "min_improve": 0.001,
            "max_regress": 0.006,
            "min_component_tris": 2,
            "min_vertex_vote": 1,
            "min_vertex_ratio": 0.12,
        },
        "v089b_visibility_guard_rt": {
            "min_improve": 0.001,
            "max_regress": 0.006,
            "min_component_tris": 2,
            "min_vertex_vote": 1,
            "min_vertex_ratio": 0.12,
        },
        "v089b_final_alpha035_rt": {
            "min_improve": 0.001,
            "max_regress": 0.005,
            "min_component_tris": 2,
            "min_vertex_vote": 1,
            "min_vertex_ratio": 0.12,
        },
        "v089b_field_prior_topk_DIAGNOSTIC": {
            "min_improve": 0.018,
            "max_regress": 0.004,
            "min_component_tris": 2,
            "min_vertex_vote": 1,
            "min_vertex_ratio": 0.20,
        },
    },
}


def _load_npz(path: Path) -> dict[str, np.ndarray]:
    loaded = np.load(str(path), allow_pickle=True)
    return {key: loaded[key] for key in loaded.files}


def _faces_from_flat(counts: np.ndarray, offsets: np.ndarray, flat: np.ndarray) -> list[np.ndarray]:
    faces = []
    for face_id, count in enumerate(np.asarray(counts, dtype=np.int64).tolist()):
        start = int(offsets[face_id])
        end = int(offsets[face_id + 1])
        if count >= 3:
            faces.append(np.asarray(flat[start:end], dtype=np.int64))
    return faces


def _triangulate(faces: list[np.ndarray]) -> np.ndarray:
    tris: list[list[int]] = []
    for face in faces:
        if len(face) < 3:
            continue
        for i in range(1, len(face) - 1):
            tris.append([int(face[0]), int(face[i]), int(face[i + 1])])
    return np.asarray(tris, dtype=np.int64)


def _build_edges(faces: list[np.ndarray]) -> np.ndarray:
    edges = set()
    for face in faces:
        n = len(face)
        for i in range(n):
            a = int(face[i])
            b = int(face[(i + 1) % n])
            if a == b:
                continue
            if a > b:
                a, b = b, a
            edges.add((a, b))
    if not edges:
        return np.zeros((0, 2), dtype=np.int64)
    return np.asarray(sorted(edges), dtype=np.int64)


def _build_tri_neighbors(tris: np.ndarray) -> list[list[int]]:
    edge_to_tri: dict[tuple[int, int], list[int]] = defaultdict(list)
    for tri_id, tri in enumerate(tris.tolist()):
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            if a > b:
                a, b = b, a
            edge_to_tri[(int(a), int(b))].append(tri_id)
    neighbors = [[] for _ in range(len(tris))]
    for bucket in edge_to_tri.values():
        if len(bucket) < 2:
            continue
        for a in bucket:
            for b in bucket:
                if a != b:
                    neighbors[a].append(b)
    return [sorted(set(row)) for row in neighbors]


def _keep_large_components(mask: np.ndarray, neighbors: list[list[int]], min_size: int) -> tuple[np.ndarray, list[int]]:
    mask = np.asarray(mask, dtype=bool)
    keep = np.zeros_like(mask)
    visited = np.zeros_like(mask)
    sizes = []
    for seed in np.where(mask & (~visited))[0].tolist():
        queue = deque([int(seed)])
        visited[seed] = True
        comp = []
        while queue:
            tri_id = queue.popleft()
            comp.append(tri_id)
            for nb in neighbors[tri_id]:
                if mask[nb] and not visited[nb]:
                    visited[nb] = True
                    queue.append(nb)
        sizes.append(len(comp))
        if len(comp) >= min_size:
            keep[np.asarray(comp, dtype=np.int64)] = True
    return keep, sizes


def _normalize(weights: np.ndarray) -> np.ndarray:
    out = np.maximum(np.asarray(weights, dtype=np.float64), 0.0)
    sums = out.sum(axis=1, keepdims=True)
    good = sums[:, 0] > EPS
    out[good] /= sums[good]
    return out


def _topk_prune(weights: np.ndarray, topk: int, mask: np.ndarray | None = None) -> np.ndarray:
    out = np.asarray(weights, dtype=np.float64).copy()
    if topk <= 0 or out.shape[1] <= topk:
        return _normalize(out)
    if mask is None:
        row_ids = range(out.shape[0])
    else:
        row_ids = np.where(np.asarray(mask, dtype=bool))[0].astype(np.int64).tolist()
    for row_id in row_ids:
        row = out[row_id]
        nz = np.count_nonzero(row > EPS)
        if nz <= topk:
            continue
        keep = np.argpartition(row, -topk)[-topk:]
        new_row = np.zeros_like(row)
        new_row[keep] = row[keep]
        out[row_id] = new_row
    return _normalize(out)


def _smooth_changed(weights: np.ndarray, base: np.ndarray, changed: np.ndarray, edges: np.ndarray, iterations: int, strength: float) -> np.ndarray:
    if iterations <= 0 or strength <= 0.0:
        return weights
    neighbors: list[list[int]] = [[] for _ in range(weights.shape[0])]
    for a, b in edges.tolist():
        neighbors[int(a)].append(int(b))
        neighbors[int(b)].append(int(a))
    out = weights.copy()
    ids = np.where(changed)[0].astype(np.int64)
    fixed = ~changed
    for _ in range(iterations):
        old = out.copy()
        old[fixed] = base[fixed]
        for vid in ids.tolist():
            nb = neighbors[vid]
            if not nb:
                continue
            mean = old[np.asarray(nb, dtype=np.int64)].mean(axis=0)
            out[vid] = (1.0 - strength) * old[vid] + strength * mean
        out = _topk_prune(out, TOPK, changed)
    return out


def _pose_names(surface: dict[str, np.ndarray]) -> list[str]:
    names = sorted({key.split("__")[0] for key in surface if key.endswith("__tri_score")})
    if not names:
        raise RuntimeError("v090 surface arrays 缺少 tri_score")
    return names


def _stack(surface: dict[str, np.ndarray], poses: list[str], candidate: str, metric: str) -> np.ndarray:
    return np.stack([np.asarray(surface[f"{pose}__{candidate}__{metric}"], dtype=np.float64) for pose in poses])


def _score_stats(values: np.ndarray) -> dict[str, float]:
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"count": 0}
    return {
        "count": int(arr.size),
        "mean": float(np.mean(arr)),
        "p95": float(np.percentile(arr, 95)),
        "max": float(np.max(arr)),
    }


def _nanmax(values: np.ndarray, axis: int) -> np.ndarray:
    out = np.nanmax(values, axis=axis)
    return np.nan_to_num(out, nan=np.inf if axis == 0 else 0.0)


def _select_profile(
    profile_name: str,
    rules_by_candidate: dict,
    surface: dict[str, np.ndarray],
    poses: list[str],
    target_tris: np.ndarray,
    tri_neighbors: list[list[int]],
    supported: np.ndarray,
    base_weights: np.ndarray,
) -> dict:
    base_tri = _stack(surface, poses, BASE_KEY, "tri_score")
    incident_count = np.zeros((base_weights.shape[0],), dtype=np.int32)
    for tri in target_tris:
        incident_count[tri] += 1

    vertex_candidate_vote: dict[str, np.ndarray] = {
        key: np.zeros((base_weights.shape[0],), dtype=np.int32) for key in SOURCE_KEYS
    }
    vertex_candidate_improve: dict[str, np.ndarray] = {
        key: np.zeros((base_weights.shape[0],), dtype=np.float64) for key in SOURCE_KEYS
    }
    candidate_reports = {}

    for candidate in SOURCE_KEYS:
        rules = rules_by_candidate[candidate]
        cand_tri = _stack(surface, poses, candidate, "tri_score")
        delta = cand_tri - base_tri
        improve = _nanmax(base_tri - cand_tri, axis=0)
        max_regress = _nanmax(delta, axis=0)
        raw_accept = (improve >= rules["min_improve"]) & (max_regress <= rules["max_regress"])
        raw_accept &= np.all(supported[target_tris], axis=1)
        kept, component_sizes = _keep_large_components(raw_accept, tri_neighbors, int(rules["min_component_tris"]))
        for tri_id in np.where(kept)[0].tolist():
            tri = target_tris[tri_id]
            for vid in tri.tolist():
                vertex_candidate_vote[candidate][int(vid)] += 1
                vertex_candidate_improve[candidate][int(vid)] += float(max(improve[tri_id], 0.0))
        candidate_reports[candidate] = {
            "rule": rules,
            "raw_accept_tri_count": int(np.count_nonzero(raw_accept)),
            "kept_tri_count": int(np.count_nonzero(kept)),
            "component_count": int(len(component_sizes)),
            "component_sizes_top10": sorted([int(x) for x in component_sizes], reverse=True)[:10],
            "improve_stats_kept": _score_stats(improve[kept]),
            "max_regress_stats_kept": _score_stats(max_regress[kept]),
        }

    selected_key = np.full((base_weights.shape[0],), BASE_KEY, dtype=object)
    selected_score = np.zeros((base_weights.shape[0],), dtype=np.float64)
    for candidate in SOURCE_KEYS:
        rules = rules_by_candidate[candidate]
        votes = vertex_candidate_vote[candidate]
        ratio = votes / np.maximum(incident_count, 1)
        valid = (votes >= int(rules["min_vertex_vote"])) & (ratio >= float(rules["min_vertex_ratio"])) & supported
        score = vertex_candidate_improve[candidate] * (0.25 + ratio)
        replace = valid & (score > selected_score)
        selected_key[replace] = candidate
        selected_score[replace] = score[replace]

    selected_counts = {BASE_KEY: int(np.count_nonzero(selected_key == BASE_KEY))}
    for candidate in SOURCE_KEYS:
        selected_counts[candidate] = int(np.count_nonzero(selected_key == candidate))

    return {
        "profile": profile_name,
        "selected_key": selected_key,
        "changed": selected_key != BASE_KEY,
        "selected_counts": selected_counts,
        "candidate_reports": candidate_reports,
    }


def main() -> dict:
    scene = _load_npz(SCENE_DATA)
    weight_data = _load_npz(V089B_WEIGHTS)
    surface = _load_npz(V090_SURFACE)

    target_faces = _faces_from_flat(scene["target_face_counts"], scene["target_face_offsets"], scene["target_face_vertices"])
    target_tris = _triangulate(target_faces)
    target_edges = _build_edges(target_faces)
    tri_neighbors = _build_tri_neighbors(target_tris)

    poses = _pose_names(surface)
    base_weights = np.asarray(weight_data["weights__" + BASE_KEY], dtype=np.float64)
    supported = np.asarray(weight_data["supported"], dtype=bool)
    weights_by_key = {BASE_KEY: base_weights}
    for candidate in SOURCE_KEYS:
        weights_by_key[candidate] = np.asarray(weight_data["weights__" + candidate], dtype=np.float64)

    out_arrays: dict[str, np.ndarray] = {
        "influence_names": np.asarray(weight_data["influence_names"]).astype(str),
        "supported": supported,
        "target_tris": target_tris.astype(np.int32),
        "target_edges": target_edges.astype(np.int32),
    }
    profiles_report = {}
    variant_reports = {}

    for profile_name, rules_by_candidate in PROFILES.items():
        selection = _select_profile(
            profile_name,
            rules_by_candidate,
            surface,
            poses,
            target_tris,
            tri_neighbors,
            supported,
            base_weights,
        )
        selected_key = selection["selected_key"]
        changed = selection["changed"]
        out_arrays[f"{profile_name}__selected_candidate_code"] = np.asarray(
            [([BASE_KEY] + SOURCE_KEYS).index(str(x)) for x in selected_key.tolist()],
            dtype=np.int16,
        )
        out_arrays[f"{profile_name}__changed_vertex_mask"] = changed.astype(bool)
        profiles_report[profile_name] = {
            "candidate_reports": selection["candidate_reports"],
            "selected_vertex_counts": selection["selected_counts"],
            "changed_vertices": int(np.count_nonzero(changed)),
        }

        for variant, params in VARIANTS.items():
            name = f"v090_{profile_name}_{variant.replace('v090_patch_', 'patch_')}"
            blend = float(params["blend"])
            weights = base_weights.copy()
            for candidate in SOURCE_KEYS:
                ids = selected_key == candidate
                if not np.any(ids):
                    continue
                cand = weights_by_key[candidate]
                weights[ids] = (1.0 - blend) * base_weights[ids] + blend * cand[ids]
            weights = _topk_prune(weights, TOPK, changed)
            weights = _smooth_changed(
                weights,
                base_weights,
                changed,
                target_edges,
                int(params["smooth_iters"]),
                float(params["smooth_strength"]),
            )
            out_arrays["weights__" + name] = weights.astype(np.float32)
            out_arrays["accept__" + name] = changed.astype(bool)
            diff_l1 = np.abs(weights - base_weights).sum(axis=1)
            variant_reports[name] = {
                "profile": profile_name,
                "params": params,
                "changed_vertices": int(np.count_nonzero(changed)),
                "row_l1_delta": _score_stats(diff_l1[changed]),
                "row_sum_error_max": float(np.max(np.abs(weights.sum(axis=1) - 1.0))),
            }

    report = {
        "status": "SUCCESS",
        "inputs": {
            "scene_data": str(SCENE_DATA),
            "weights": str(V089B_WEIGHTS),
            "surface": str(V090_SURFACE),
        },
        "output_npz": str(OUT_NPZ),
        "poses": poses,
        "base_key": BASE_KEY,
        "source_keys": SOURCE_KEYS,
        "profiles": profiles_report,
        "variants": variant_reports,
        "note": "候选必须继续写入 Maya 并跑真实 DG surface objective；本脚本只做离线 patch 选择。",
    }

    np.savez_compressed(str(OUT_NPZ), **out_arrays)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["variant", "profile", "changed_vertices", "row_l1_mean", "row_l1_p95", "row_l1_max", "row_sum_error_max"])
        for variant, item in variant_reports.items():
            stats = item["row_l1_delta"]
            writer.writerow([
                variant,
                item["profile"],
                item["changed_vertices"],
                stats.get("mean", 0.0),
                stats.get("p95", 0.0),
                stats.get("max", 0.0),
                item["row_sum_error_max"],
            ])
    return report


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
