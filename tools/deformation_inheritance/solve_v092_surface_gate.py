# -*- coding: utf-8 -*-
"""v092 actual-surface gated patch candidate.

v091 已经把 vertex / edge / area / normal 放进 patch objective，但真实 Maya DG
验收显示全局略回归。v092 不再扩大搜索，而是把 v091 的 shape_guard 结果投影
回一个更严格的可接受域：

1. 用真实 Maya DG 导出的 tri_score 比较 v091 与 base。
2. 只允许 incident triangles 在多姿态下有明确收益且回归受控的顶点改变。
3. 权重仍保持非负、归一，变化只发生在被门禁接受的 patch 顶点。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
V089B_WEIGHTS = INFO_DIR / "v089b_fast_continuous_field_candidates.npz"
V090_TOPO = INFO_DIR / "v090_patch_surface_candidates.npz"
V091_WEIGHTS = INFO_DIR / "v091_patch_constrained_objective_candidates.npz"
V091_ARRAYS = INFO_DIR / "v091_patch_surface_candidates_objective_arrays.npz"
OUT_NPZ = INFO_DIR / "v092_surface_gate_candidates.npz"
OUT_JSON = INFO_DIR / "v092_surface_gate_summary.json"
OUT_CSV = INFO_DIR / "v092_surface_gate_summary.csv"

BASE_KEY = "v089b_base_hybrid_m0010"
SOURCE_KEY = "v091_surface_shape_guard"
BASE_WEIGHT_KEY = "weights__v089b_base_hybrid_m0010"
SOURCE_WEIGHT_KEY = "weights__v091_surface_shape_guard"
EPS = 1e-8


CONFIGS = {
    "v092_gate_tight": {
        "max_regress": 0.0015,
        "mean_improve": 0.0020,
        "good_ratio": 0.86,
        "alpha_cap": 0.72,
        "smooth_iters": 1,
    },
    "v092_gate_balanced": {
        "max_regress": 0.0040,
        "mean_improve": 0.0012,
        "good_ratio": 0.72,
        "alpha_cap": 0.82,
        "smooth_iters": 2,
    },
    "v092_gate_visible": {
        "max_regress": 0.0080,
        "mean_improve": 0.0005,
        "good_ratio": 0.60,
        "alpha_cap": 0.92,
        "smooth_iters": 2,
    },
    "v092_gate_patch_loose": {
        "max_regress": 0.0200,
        "mean_improve": 0.0001,
        "good_ratio": 0.25,
        "vote_ratio": 1.0,
        "alpha_cap": 0.62,
        "smooth_iters": 2,
    },
    "v092_gate_patch_votes": {
        "max_regress": 0.0300,
        "mean_improve": 0.0000,
        "good_ratio": 0.0,
        "vote_ratio": 1.10,
        "alpha_cap": 0.52,
        "smooth_iters": 2,
    },
}


def _normalize_rows(weights: np.ndarray) -> np.ndarray:
    out = np.maximum(np.asarray(weights, dtype=np.float64), 0.0)
    row_sum = out.sum(axis=1, keepdims=True)
    row_sum[row_sum <= EPS] = 1.0
    return out / row_sum


def _pose_names(arrays: np.lib.npyio.NpzFile) -> list[str]:
    suffix = "__%s__tri_score" % BASE_KEY
    return sorted(key[: -len(suffix)] for key in arrays.files if key.endswith(suffix))


def _incident_triangles(tris: np.ndarray, vertex_count: int) -> list[list[int]]:
    out: list[list[int]] = [[] for _ in range(vertex_count)]
    for tri_id, tri in enumerate(tris.astype(np.int64).tolist()):
        for vid in tri:
            out[int(vid)].append(int(tri_id))
    return out


def _adjacency(edges: np.ndarray, vertex_count: int) -> list[list[int]]:
    out: list[list[int]] = [[] for _ in range(vertex_count)]
    for a, b in edges.astype(np.int64).tolist():
        out[int(a)].append(int(b))
        out[int(b)].append(int(a))
    return out


def _smooth_alpha(alpha: np.ndarray, adjacency: list[list[int]], allowed: np.ndarray, iterations: int) -> np.ndarray:
    out = np.asarray(alpha, dtype=np.float64).copy()
    for _ in range(int(iterations)):
        nxt = out.copy()
        changed = np.where(allowed)[0].astype(np.int64).tolist()
        for vid in changed:
            nb = [n for n in adjacency[int(vid)] if allowed[int(n)]]
            if not nb:
                continue
            nxt[int(vid)] = 0.55 * out[int(vid)] + 0.45 * float(np.mean(out[np.asarray(nb, dtype=np.int64)]))
        out = nxt
    out[~allowed] = 0.0
    return np.clip(out, 0.0, 1.0)


def _build_candidate(
    base_weights: np.ndarray,
    source_weights: np.ndarray,
    changed_mask: np.ndarray,
    incident: list[list[int]],
    adjacency: list[list[int]],
    tri_mean_delta: np.ndarray,
    tri_max_delta: np.ndarray,
    tri_improve_count: np.ndarray,
    tri_regress_count: np.ndarray,
    config: dict,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    vertex_count = base_weights.shape[0]
    alpha = np.zeros((vertex_count,), dtype=np.float64)
    allowed = np.zeros((vertex_count,), dtype=bool)
    for vid in np.where(changed_mask)[0].astype(np.int64).tolist():
        tri_ids = incident[int(vid)]
        if not tri_ids:
            continue
        ids = np.asarray(tri_ids, dtype=np.int64)
        mean_delta = float(np.nanmean(tri_mean_delta[ids]))
        max_delta = float(np.nanmax(tri_max_delta[ids]))
        good = float(np.count_nonzero(tri_mean_delta[ids] < -config["mean_improve"]))
        finite_count = max(1.0, float(np.count_nonzero(np.isfinite(tri_mean_delta[ids]))))
        good_ratio = good / finite_count
        improve_votes = float(np.nansum(tri_improve_count[ids]))
        regress_votes = float(np.nansum(tri_regress_count[ids]))
        if max_delta > config["max_regress"]:
            continue
        if mean_delta >= -config["mean_improve"]:
            continue
        if good_ratio < config["good_ratio"]:
            continue
        vote_ratio = float(config.get("vote_ratio", 1.0))
        if improve_votes < regress_votes * vote_ratio:
            continue
        penalty = max(0.0, max_delta)
        gain = max(0.0, -mean_delta)
        raw_alpha = gain / (gain + 2.0 * penalty + EPS)
        alpha[int(vid)] = min(config["alpha_cap"], config["alpha_cap"] * (0.35 + 0.65 * raw_alpha))
        allowed[int(vid)] = True

    alpha = _smooth_alpha(alpha, adjacency, allowed, int(config["smooth_iters"]))
    weights = _normalize_rows((1.0 - alpha[:, None]) * base_weights + alpha[:, None] * source_weights)
    accept = alpha > 1e-5
    return weights, accept, alpha


def _execute() -> dict:
    v089b = np.load(str(V089B_WEIGHTS), allow_pickle=True)
    topo = np.load(str(V090_TOPO), allow_pickle=True)
    v091 = np.load(str(V091_WEIGHTS), allow_pickle=True)
    arrays = np.load(str(V091_ARRAYS), allow_pickle=True)

    influence_names = np.asarray(v091["influence_names"]).astype(str)
    tris = np.asarray(topo["target_tris"], dtype=np.int32)
    edges = np.asarray(topo["target_edges"], dtype=np.int32)
    base = _normalize_rows(np.asarray(v089b[BASE_WEIGHT_KEY], dtype=np.float64))
    source = _normalize_rows(np.asarray(v091[SOURCE_WEIGHT_KEY], dtype=np.float64))
    changed_mask = np.sum(np.abs(source - base), axis=1) > 1e-6

    poses = _pose_names(arrays)
    if not poses:
        raise RuntimeError("v091 验收数组里没有 tri_score")
    base_scores = np.stack([np.asarray(arrays["%s__%s__tri_score" % (pose, BASE_KEY)], dtype=np.float64) for pose in poses])
    source_scores = np.stack([np.asarray(arrays["%s__%s__tri_score" % (pose, SOURCE_KEY)], dtype=np.float64) for pose in poses])
    delta = source_scores - base_scores
    finite = np.isfinite(delta)
    tri_mean_delta = np.where(np.any(finite, axis=0), np.nanmean(delta, axis=0), np.nan)
    tri_max_delta = np.where(np.any(finite, axis=0), np.nanmax(delta, axis=0), np.nan)
    tri_improve_count = np.sum(np.where(finite, delta < -0.01, False), axis=0)
    tri_regress_count = np.sum(np.where(finite, delta > 0.01, False), axis=0)

    incident = _incident_triangles(tris, base.shape[0])
    adjacency = _adjacency(edges, base.shape[0])

    output = {
        "influence_names": influence_names,
        "target_tris": tris,
        "target_edges": edges,
        "source_candidate": np.asarray(SOURCE_KEY),
        "pose_names": np.asarray(poses).astype(str),
    }
    summary = {
        "status": "SUCCESS",
        "source_candidate": SOURCE_KEY,
        "pose_count": len(poses),
        "changed_input_vertices": int(np.count_nonzero(changed_mask)),
        "variants": {},
    }
    rows = []
    for name, cfg in CONFIGS.items():
        weights, accept, alpha = _build_candidate(
            base_weights=base,
            source_weights=source,
            changed_mask=changed_mask,
            incident=incident,
            adjacency=adjacency,
            tri_mean_delta=tri_mean_delta,
            tri_max_delta=tri_max_delta,
            tri_improve_count=tri_improve_count,
            tri_regress_count=tri_regress_count,
            config=cfg,
        )
        output["weights__" + name] = weights.astype(np.float32)
        output["accept__" + name] = accept.astype(bool)
        output["alpha__" + name] = alpha.astype(np.float32)
        row_l1 = np.sum(np.abs(weights - base), axis=1)
        accepted_ids = np.where(accept)[0].astype(np.int64)
        incident_ids = sorted(set(t for vid in accepted_ids.tolist() for t in incident[int(vid)]))
        incident_arr = np.asarray(incident_ids, dtype=np.int64)
        payload = {
            "config": cfg,
            "changed_vertices": int(accepted_ids.size),
            "row_l1_p95": float(np.percentile(row_l1[accept], 95)) if np.any(accept) else 0.0,
            "row_l1_max": float(np.max(row_l1)) if row_l1.size else 0.0,
            "accepted_incident_tris": int(incident_arr.size),
            "incident_tri_mean_delta": float(np.nanmean(tri_mean_delta[incident_arr])) if incident_arr.size else 0.0,
            "incident_tri_max_delta": float(np.nanmax(tri_max_delta[incident_arr])) if incident_arr.size else 0.0,
            "incident_improve_votes": int(np.nansum(tri_improve_count[incident_arr])) if incident_arr.size else 0,
            "incident_regress_votes": int(np.nansum(tri_regress_count[incident_arr])) if incident_arr.size else 0,
        }
        summary["variants"][name] = payload
        rows.append({"candidate": name, **{k: v for k, v in payload.items() if k != "config"}})

    np.savez_compressed(str(OUT_NPZ), **output)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return summary


if __name__ == "__main__":
    print(json.dumps(_execute(), ensure_ascii=False, indent=2))
