from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize


PROJECT_DIR = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG"
)
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
INPUT_PATH = INFO_DIR / "v084_inverse_input.npz"
SOURCE_DATA = INFO_DIR / "v082_clean_scene_data.npz"
VALIDATION_PATH = INFO_DIR / "param_sweep" / "v082p00_distance_k64_correspondence_validation.npz"
BASE_WEIGHTS_PATH = INFO_DIR / "v082_weight_candidates_hybrid_motion_gate.npz"
OUT_PATH = INFO_DIR / "v084_weight_candidates_constrained_inverse.npz"
SUMMARY_PATH = INFO_DIR / "v084_weight_candidates_constrained_inverse_summary.json"


def _stat(values: np.ndarray) -> dict:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
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


def _build_adjacency(data, vertex_count: int) -> list[list[int]]:
    counts = data["target_face_counts"].astype(np.int32)
    offsets = data["target_face_offsets"].astype(np.int32)
    flat = data["target_face_vertices"].astype(np.int32)
    adjacency = [set() for _ in range(vertex_count)]
    for face_id, count in enumerate(counts):
        ids = flat[offsets[face_id] : offsets[face_id] + count].tolist()
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                if a != b:
                    adjacency[a].add(b)
                    adjacency[b].add(a)
    return [sorted(x) for x in adjacency]


def _expected_weights(data, val, vertex_ids: np.ndarray) -> np.ndarray:
    source_weights = data["source_weights"].astype(np.float64)
    source_tris = val["source_tris"].astype(np.int32)
    best_tri = val["best_tri"].astype(np.int32)
    best_bary = val["best_bary"].astype(np.float64)
    tris = source_tris[best_tri[vertex_ids]]
    bary = best_bary[vertex_ids]
    return (
        bary[:, 0:1] * source_weights[tris[:, 0]]
        + bary[:, 1:2] * source_weights[tris[:, 1]]
        + bary[:, 2:3] * source_weights[tris[:, 2]]
    )


def _candidate_columns(
    vid: int,
    row_index: int,
    base: np.ndarray,
    expected: np.ndarray,
    adjacency: list[list[int]],
    max_cols: int = 28,
) -> np.ndarray:
    scores = np.zeros((base.shape[1],), dtype=np.float64)
    scores += base[vid] * 1.0
    scores += expected[row_index] * 1.4
    for nb in adjacency[vid]:
        scores += base[nb] * 0.18
    cols = set(np.where(base[vid] > 1e-5)[0].tolist())
    cols.update(np.where(expected[row_index] > 1e-5)[0].tolist())
    if scores.size:
        top = np.argsort(scores)[::-1][:max_cols]
        cols.update(top.tolist())
    cols = np.asarray(sorted(cols), dtype=np.int32)
    if len(cols) > max_cols:
        order = np.argsort(scores[cols])[::-1][:max_cols]
        cols = cols[order]
        cols.sort()
    return cols


def _solve_row(
    basis: np.ndarray,
    desired: np.ndarray,
    base_row: np.ndarray,
    expected_row: np.ndarray,
    cols: np.ndarray,
    lambda_base: float,
    lambda_expected: float,
    lambda_entropy: float,
) -> tuple[np.ndarray, bool, float]:
    B = basis[cols].T.astype(np.float64)  # [3, k]
    target = desired.astype(np.float64)
    base_sub = base_row[cols].astype(np.float64)
    expected_sub = expected_row[cols].astype(np.float64)
    x0 = 0.7 * base_sub + 0.3 * expected_sub
    if x0.sum() <= 1e-12:
        x0 = np.ones((len(cols),), dtype=np.float64) / max(1, len(cols))
    else:
        x0 = x0 / x0.sum()

    def objective(x):
        pos = B @ x
        motion = pos - target
        prior = x - base_sub
        semantic = x - expected_sub
        nz = x[x > 1e-12]
        entropy = np.sum(nz * np.log(nz))
        return (
            float(np.dot(motion, motion))
            + float(lambda_base) * float(np.dot(prior, prior))
            + float(lambda_expected) * float(np.dot(semantic, semantic))
            + float(lambda_entropy) * float(entropy)
        )

    cons = ({"type": "eq", "fun": lambda x: float(np.sum(x) - 1.0)},)
    res = minimize(
        objective,
        x0,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * len(cols),
        constraints=cons,
        options={"ftol": 1e-10, "maxiter": 120, "disp": False},
    )
    x = np.asarray(res.x if res.success else x0, dtype=np.float64)
    x[x < 1e-8] = 0.0
    total = x.sum()
    if total <= 1e-12:
        x = x0
        total = x.sum()
    x = x / total
    out = np.zeros_like(base_row)
    out[cols] = x
    return out, bool(res.success), float(res.fun if np.isfinite(res.fun) else objective(x))


def _predict_error(weights_roi: np.ndarray, basis: np.ndarray, desired: np.ndarray) -> np.ndarray:
    pred = np.einsum("ri,rij->rj", weights_roi, basis)
    return np.linalg.norm(pred - desired, axis=1)


def main() -> int:
    inv = np.load(INPUT_PATH, allow_pickle=True)
    data = np.load(SOURCE_DATA, allow_pickle=True)
    val = np.load(VALIDATION_PATH, allow_pickle=True)
    base_full = np.load(BASE_WEIGHTS_PATH, allow_pickle=True)["hybrid_m0010_cc3"].astype(np.float64)

    roi_ids = inv["roi_ids"].astype(np.int32)
    core_ids = set(inv["core_ids"].astype(np.int32).tolist())
    basis = inv["basis_jaw25_roi"].astype(np.float64)
    desired = inv["desired_jaw_roi"].astype(np.float64)
    base_roi = inv["base_weights_roi"].astype(np.float64)
    base_err = inv["base_motion_error_roi"].astype(np.float64)
    expected_roi = _expected_weights(data, val, roi_ids)
    adjacency = _build_adjacency(data, base_full.shape[0])

    configs = {
        "v084_inverse_strongBase": {"lambda_base": 0.020, "lambda_expected": 0.004, "lambda_entropy": 0.000},
        "v084_inverse_balanced": {"lambda_base": 0.006, "lambda_expected": 0.003, "lambda_entropy": 0.000},
        "v084_inverse_motion": {"lambda_base": 0.0015, "lambda_expected": 0.001, "lambda_entropy": 0.000},
    }

    outputs = {}
    summary = {
        "status": "SUCCESS",
        "input_path": str(INPUT_PATH),
        "output_path": str(OUT_PATH),
        "roi_vertices": int(len(roi_ids)),
        "core_vertices": int(len(core_ids)),
        "base_error": _stat(base_err),
        "variants": {},
        "note": "SLSQP solve per ROI vertex with non-negative sum=1 weights and a limited candidate influence set. Maya DG validation is still required.",
    }

    for name, cfg in configs.items():
        solved_full = base_full.copy()
        accepted_mask = np.zeros((len(roi_ids),), dtype=bool)
        success_count = 0
        candidate_sizes = []
        raw_err = np.zeros((len(roi_ids),), dtype=np.float64)
        raw_l1 = np.zeros((len(roi_ids),), dtype=np.float64)
        for row, vid in enumerate(roi_ids):
            cols = _candidate_columns(int(vid), row, base_full, expected_roi, adjacency)
            candidate_sizes.append(len(cols))
            solved_row, success, _ = _solve_row(
                basis[row],
                desired[row],
                base_full[int(vid)],
                expected_roi[row],
                cols,
                cfg["lambda_base"],
                cfg["lambda_expected"],
                cfg["lambda_entropy"],
            )
            success_count += int(success)
            solved_full[int(vid)] = solved_row
            raw_l1[row] = np.abs(solved_row - base_full[int(vid)]).sum()

        solved_full = _topk_normalize(solved_full).astype(np.float64)
        raw_err = _predict_error(solved_full[roi_ids], basis, desired)
        improvement = base_err - raw_err
        # 只接收有实际预测收益且权重变化不过大的行；避免求解器为单姿态过拟合。
        accepted_mask = (improvement > 0.001) & (raw_l1 <= 0.85)
        accepted_full = base_full.copy()
        accepted_full[roi_ids[accepted_mask]] = solved_full[roi_ids[accepted_mask]]
        accepted_full = _topk_normalize(accepted_full)
        accepted_err = _predict_error(accepted_full[roi_ids], basis, desired)

        outputs[name + "_raw"] = solved_full.astype(np.float32)
        outputs[name + "_accepted"] = accepted_full.astype(np.float32)
        outputs[name + "_accepted_roi_mask"] = accepted_mask.astype(np.bool_)
        outputs[name + "_raw_error_roi"] = raw_err.astype(np.float64)
        outputs[name + "_accepted_error_roi"] = accepted_err.astype(np.float64)
        outputs[name + "_row_l1_roi"] = raw_l1.astype(np.float64)

        core_local = np.asarray([int(vid) in core_ids for vid in roi_ids], dtype=bool)
        summary["variants"][name] = {
            "config": cfg,
            "solver_success": int(success_count),
            "candidate_size": _stat(np.asarray(candidate_sizes, dtype=np.float64)),
            "raw_error": _stat(raw_err),
            "raw_improvement": _stat(improvement),
            "row_l1": _stat(raw_l1),
            "accepted_vertices": int(accepted_mask.sum()),
            "accepted_core_vertices": int((accepted_mask & core_local).sum()),
            "accepted_error": _stat(accepted_err),
            "accepted_error_core": _stat(accepted_err[core_local]),
            "base_error_core": _stat(base_err[core_local]),
        }

    np.savez_compressed(OUT_PATH, **outputs)
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
