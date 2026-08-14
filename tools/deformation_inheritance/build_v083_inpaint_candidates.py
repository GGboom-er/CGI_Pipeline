from __future__ import annotations

import json
from pathlib import Path

import numpy as np


PROJECT_DIR = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG"
)
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
SOURCE_DATA = INFO_DIR / "v082_clean_scene_data.npz"
VALIDATION_PATH = INFO_DIR / "param_sweep" / "v082p00_distance_k64_correspondence_validation.npz"
BASE_WEIGHTS_PATH = INFO_DIR / "v082_weight_candidates_hybrid_motion_gate.npz"
ERROR_ARRAYS_PATH = INFO_DIR / "v082_distance_energy_motion_error_arrays.npz"
OUT_PATH = INFO_DIR / "v083_weight_candidates_inpaint.npz"
SUMMARY_PATH = INFO_DIR / "v083_weight_candidates_inpaint_summary.json"


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


def _expand(mask: np.ndarray, adjacency: list[list[int]], rings: int) -> np.ndarray:
    out = mask.copy()
    frontier = mask.copy()
    for _ in range(rings):
        nxt = out.copy()
        for vid in np.where(frontier)[0]:
            nxt[adjacency[int(vid)]] = True
        frontier = nxt & (~out)
        out = nxt
    return out


def _inpaint(base: np.ndarray, domain: np.ndarray, adjacency: list[list[int]], iterations: int) -> np.ndarray:
    current = np.asarray(base, dtype=np.float64).copy()
    ids = np.where(domain)[0]
    for _ in range(iterations):
        nxt = current.copy()
        for vid in ids:
            neighbors = adjacency[int(vid)]
            if not neighbors:
                continue
            nxt[vid] = current[neighbors].mean(axis=0)
        current = _topk_normalize(nxt).astype(np.float64)
    return current


def main() -> int:
    data = np.load(SOURCE_DATA, allow_pickle=True)
    val = np.load(VALIDATION_PATH, allow_pickle=True)
    base_data = np.load(BASE_WEIGHTS_PATH, allow_pickle=True)
    errors = np.load(ERROR_ARRAYS_PATH, allow_pickle=True)

    base = base_data["hybrid_m0010_cc3"].astype(np.float64)
    err = errors["hybrid_m0010_cc3__distance_k64_motion_error"].astype(np.float64)
    finite = np.isfinite(err)
    unsupported = val["unsupported"].astype(bool)
    strict = (
        val["normal_mismatch"].astype(bool)
        | val["semantic_ambiguous"].astype(bool)
        | val["topology_discontinuity"].astype(bool)
        | val["face_semantic_discontinuity"].astype(bool)
    ) & (~unsupported)
    supported = finite & (~unsupported)
    p95 = float(np.percentile(err[supported], 95))
    high_error = supported & (err >= p95)
    strict_error = strict & (err >= 0.005)
    core = high_error | strict_error
    adjacency = _build_adjacency(data, base.shape[0])
    domain = _expand(core, adjacency, rings=1) & supported
    inpainted = _inpaint(base, domain, adjacency, iterations=35)

    variants = {
        "v083_inpaint_a025": 0.25,
        "v083_inpaint_a050": 0.50,
        "v083_inpaint_a100": 1.00,
    }
    output = {}
    summaries = {
        "status": "SUCCESS",
        "base_weights": "hybrid_m0010_cc3",
        "base_weights_path": str(BASE_WEIGHTS_PATH),
        "error_arrays_path": str(ERROR_ARRAYS_PATH),
        "validation_path": str(VALIDATION_PATH),
        "output_path": str(OUT_PATH),
        "roi": {
            "supported": int(supported.sum()),
            "p95_threshold": p95,
            "high_error_vertices": int(high_error.sum()),
            "strict_error_vertices": int(strict_error.sum()),
            "core_vertices": int(cgi_pipeline.core.sum()),
            "domain_1ring_vertices": int(domain.sum()),
            "base_core_error": _stat(err[core]),
            "base_domain_error": _stat(err[domain]),
        },
        "variants": {},
        "note": "Weight inpainting is a candidate generator only. It must be accepted or rejected by fresh Maya DG validation.",
    }

    for name, alpha in variants.items():
        candidate = base.copy()
        candidate[domain] = (1.0 - alpha) * base[domain] + alpha * inpainted[domain]
        candidate = _topk_normalize(candidate)
        output[name] = candidate
        output[name + "_domain_mask"] = domain.astype(np.bool_)
        output[name + "_core_mask"] = cgi_pipeline.core.astype(np.bool_)
        row_delta = np.abs(candidate - base).sum(axis=1)
        summaries["variants"][name] = {
            "alpha": float(alpha),
            "changed_vertices": int((row_delta > 1e-6).sum()),
            "row_delta": _stat(row_delta[row_delta > 1e-6]),
        }

    np.savez_compressed(OUT_PATH, **output)
    SUMMARY_PATH.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
