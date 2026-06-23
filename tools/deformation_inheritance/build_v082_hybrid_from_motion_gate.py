from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import numpy as np


PROJECT_DIR = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG"
)
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
SOURCE_DATA = INFO_DIR / "v082_clean_scene_data.npz"
WEIGHTS_PATH = INFO_DIR / "v082_weight_candidates_distance_vs_energy.npz"
ERROR_ARRAYS_PATH = INFO_DIR / "v082_distance_energy_motion_error_arrays.npz"
OUT_PATH = INFO_DIR / "v082_weight_candidates_hybrid_motion_gate.npz"
SUMMARY_PATH = INFO_DIR / "v082_weight_candidates_hybrid_motion_gate_summary.json"

RED_FACE_RANGES = {
    "mouth_red_left_9095_9150": (9095, 9150),
    "mouth_red_mid_9263_9318": (9263, 9318),
}


def _stat(values: np.ndarray) -> dict:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return {"count": 0}
    return {
        "count": int(len(values)),
        "mean": float(np.mean(values)),
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "max": float(np.max(values)),
    }


def _topk_normalize(weights: np.ndarray, max_influences: int = 12, min_weight: float = 1e-6) -> np.ndarray:
    weights = np.asarray(weights, dtype=np.float64).copy()
    weights[weights < min_weight] = 0.0
    if weights.shape[1] > max_influences:
        keep = np.argpartition(weights, -max_influences, axis=1)[:, -max_influences:]
        mask = np.zeros_like(weights, dtype=bool)
        rows = np.arange(weights.shape[0])[:, None]
        mask[rows, keep] = True
        weights[~mask] = 0.0
    row_sum = weights.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0.0] = 1.0
    weights /= row_sum
    return weights.astype(np.float32)


def _face_range_vertices(data, start_face: int, end_face: int) -> np.ndarray:
    counts = data["target_face_counts"].astype(np.int32)
    offsets = data["target_face_offsets"].astype(np.int32)
    flat = data["target_face_vertices"].astype(np.int32)
    vertices = set()
    for face_id in range(start_face, end_face + 1):
        vertices.update(flat[offsets[face_id] : offsets[face_id] + counts[face_id]].tolist())
    return np.asarray(sorted(vertices), dtype=np.int32)


def _build_adjacency(data, vertex_count: int) -> list[list[int]]:
    counts = data["target_face_counts"].astype(np.int32)
    offsets = data["target_face_offsets"].astype(np.int32)
    flat = data["target_face_vertices"].astype(np.int32)
    adjacency = [set() for _ in range(vertex_count)]
    for face_id, count in enumerate(counts):
        ids = flat[offsets[face_id] : offsets[face_id] + count].tolist()
        if len(ids) < 2:
            continue
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                if a != b:
                    adjacency[a].add(b)
                    adjacency[b].add(a)
    return [sorted(x) for x in adjacency]


def _component_filter(mask: np.ndarray, adjacency: list[list[int]], min_size: int) -> tuple[np.ndarray, list[int]]:
    if min_size <= 1:
        return mask.copy(), []
    mask = np.asarray(mask, dtype=bool)
    visited = np.zeros_like(mask, dtype=bool)
    out = np.zeros_like(mask, dtype=bool)
    rejected_sizes = []
    for start in np.where(mask & (~visited))[0]:
        queue = deque([int(start)])
        visited[start] = True
        component = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for nxt in adjacency[current]:
                if mask[nxt] and not visited[nxt]:
                    visited[nxt] = True
                    queue.append(nxt)
        if len(component) >= min_size:
            out[component] = True
        else:
            rejected_sizes.append(len(component))
    return out, rejected_sizes


def _make_variant(
    name: str,
    distance_weights: np.ndarray,
    energy_weights: np.ndarray,
    distance_error: np.ndarray,
    energy_error: np.ndarray,
    improvement_margin: float,
    adjacency: list[list[int]],
    component_min_size: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    finite = np.isfinite(distance_error) & np.isfinite(energy_error)
    raw_mask = finite & ((distance_error - energy_error) > improvement_margin)
    accepted_mask, rejected_sizes = _component_filter(raw_mask, adjacency, component_min_size)
    hybrid = np.asarray(distance_weights, dtype=np.float64).copy()
    hybrid[accepted_mask] = energy_weights[accepted_mask]
    hybrid = _topk_normalize(hybrid)
    predicted_error = distance_error.copy()
    predicted_error[accepted_mask] = energy_error[accepted_mask]
    summary = {
        "name": name,
        "improvement_margin": float(improvement_margin),
        "component_min_size": int(component_min_size),
        "raw_accept_vertices": int(raw_mask.sum()),
        "accepted_vertices": int(accepted_mask.sum()),
        "rejected_small_components": int(len(rejected_sizes)),
        "rejected_small_component_vertices": int(sum(rejected_sizes)),
        "supported_predicted_error": _stat(predicted_error[finite]),
        "accepted_distance_error": _stat(distance_error[accepted_mask]),
        "accepted_energy_error": _stat(energy_error[accepted_mask]),
        "accepted_improvement": _stat((distance_error - energy_error)[accepted_mask]),
    }
    return hybrid, accepted_mask, predicted_error, summary


def main() -> int:
    data = np.load(SOURCE_DATA, allow_pickle=True)
    weights_data = np.load(WEIGHTS_PATH, allow_pickle=True)
    errors = np.load(ERROR_ARRAYS_PATH, allow_pickle=True)

    distance_weights = weights_data["distance_k64"].astype(np.float64)
    energy_weights = weights_data["energy_p03"].astype(np.float64)
    # 使用同一套 canonical provenance 做门控，否则两个候选各按自己的映射验收会混淆收益。
    canonical_validation = "distance_k64"
    distance_error = errors[
        "distance_k64__" + canonical_validation + "_motion_error"
    ].astype(np.float64)
    energy_error = errors["energy_p03__" + canonical_validation + "_motion_error"].astype(np.float64)
    vertex_count = distance_weights.shape[0]
    adjacency = _build_adjacency(data, vertex_count)
    finite = np.isfinite(distance_error) & np.isfinite(energy_error)

    variants = [
        ("hybrid_m0005_cc3", 0.0005, 3),
        ("hybrid_m0010_cc3", 0.0010, 3),
        ("hybrid_m0020_cc3", 0.0020, 3),
        ("hybrid_m0030_cc3", 0.0030, 3),
        ("hybrid_m0050_cc3", 0.0050, 3),
        ("hybrid_m0010_cc8", 0.0010, 8),
    ]

    output_arrays = {}
    summaries = {
        "status": "SUCCESS",
        "source_weights_path": str(WEIGHTS_PATH),
        "source_error_arrays_path": str(ERROR_ARRAYS_PATH),
        "output_path": str(OUT_PATH),
        "canonical_validation": canonical_validation,
        "baseline_supported_error": {
            "distance_k64": _stat(distance_error[finite]),
            "energy_p03": _stat(energy_error[finite]),
        },
        "variants": {},
        "note": "Hybrid starts from distance_k64 and only uses energy_p03 rows when Jaw25 Maya motion-delta error is better by margin. This is diagnostic and must still pass fresh Maya DG tests.",
    }

    red_vertices = {
        name: _face_range_vertices(data, start, end) for name, (start, end) in RED_FACE_RANGES.items()
    }

    for name, margin, component_min_size in variants:
        hybrid, accepted, predicted_error, summary = _make_variant(
            name,
            distance_weights,
            energy_weights,
            distance_error,
            energy_error,
            margin,
            adjacency,
            component_min_size,
        )
        output_arrays[name] = hybrid
        output_arrays[name + "_accepted_mask"] = accepted.astype(np.bool_)
        output_arrays[name + "_predicted_motion_error"] = predicted_error.astype(np.float64)
        summary["red_face_acceptance"] = {
            red_name: {
                "accepted_vertices": int(accepted[ids].sum()),
                "total_vertices": int(len(ids)),
                "predicted_error": _stat(predicted_error[ids]),
            }
            for red_name, ids in red_vertices.items()
        }
        summaries["variants"][name] = summary

    np.savez_compressed(OUT_PATH, **output_arrays)
    SUMMARY_PATH.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
