from __future__ import annotations

import json
from pathlib import Path

import numpy as np


PROJECT_DIR = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG"
)
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
SOURCE_DATA = INFO_DIR / "v082_clean_scene_data.npz"
SWEEP_DIR = INFO_DIR / "param_sweep"
OUT_PATH = INFO_DIR / "v082_weight_candidates_distance_vs_energy.npz"
SUMMARY_PATH = INFO_DIR / "v082_weight_candidates_distance_vs_energy_summary.json"
INFLUENCE_JSON_PATH = INFO_DIR / "v082_weight_candidates_influences.json"


def _leaf(path: str) -> str:
    return str(path).split("|")[-1].split(":")[-1]


def _find_influence_index(influence_names: np.ndarray, preferred: list[str]) -> int:
    leaves = [_leaf(x).lower() for x in influence_names]
    for name in preferred:
        name_l = name.lower()
        for i, leaf in enumerate(leaves):
            if leaf == name_l:
                return i
    for token in ["m_head_a_jnt", "m_headneck_a_jnt", "head"]:
        for i, leaf in enumerate(leaves):
            if token in leaf:
                return i
    return int(np.argmax(np.asarray([1.0 if "head" in leaf else 0.0 for leaf in leaves])))


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


def _build_weights(data, val, fallback_influence: int) -> np.ndarray:
    source_weights = data["source_weights"].astype(np.float64)
    target_count = len(data["target_points"])
    source_tris = val["source_tris"].astype(np.int32)
    best_tri = val["best_tri"].astype(np.int32)
    best_bary = val["best_bary"].astype(np.float64)
    unsupported = val["unsupported"].astype(bool)

    out = np.zeros((target_count, source_weights.shape[1]), dtype=np.float64)
    supported_ids = np.where(~unsupported)[0]
    tris = source_tris[best_tri[supported_ids]]
    bary = best_bary[supported_ids]
    out[supported_ids] = (
        bary[:, 0:1] * source_weights[tris[:, 0]]
        + bary[:, 1:2] * source_weights[tris[:, 1]]
        + bary[:, 2:3] * source_weights[tris[:, 2]]
    )
    out[unsupported, fallback_influence] = 1.0
    return _topk_normalize(out)


def main() -> int:
    data = np.load(SOURCE_DATA, allow_pickle=True)
    influence_names = data["influence_names"]
    fallback = _find_influence_index(influence_names, ["M_Head_A_jnt", "M_HeadNeck_A_jnt"])
    configs = {
        "distance_k64": SWEEP_DIR / "v082p00_distance_k64_correspondence_validation.npz",
        "energy_p03": SWEEP_DIR / "v082p03_energy_n100_correspondence_validation.npz",
    }
    weights = {}
    summaries = {}
    for name, path in configs.items():
        val = np.load(path, allow_pickle=True)
        w = _build_weights(data, val, fallback)
        weights[name] = w
        row_error = np.abs(w.sum(axis=1) - 1.0)
        changed_supported = int((~val["unsupported"].astype(bool)).sum())
        summaries[name] = {
            "source_validation": str(path),
            "shape": list(w.shape),
            "row_sum_max_error": float(row_error.max()),
            "nonzero_mean": float(np.count_nonzero(w, axis=1).mean()),
            "supported_vertices": changed_supported,
            "unsupported_fallback_vertices": int(val["unsupported"].sum()),
        }

    np.savez_compressed(
        OUT_PATH,
        distance_k64=weights["distance_k64"],
        energy_p03=weights["energy_p03"],
        fallback_influence_index=np.asarray([fallback], dtype=np.int32),
    )
    INFLUENCE_JSON_PATH.write_text(
        json.dumps(
            {
                "status": "SUCCESS",
                "influence_names": [str(x) for x in influence_names],
                "fallback_influence_index": int(fallback),
                "fallback_influence_name": str(influence_names[fallback]),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary = {
        "status": "SUCCESS",
        "output_path": str(OUT_PATH),
        "influence_json_path": str(INFLUENCE_JSON_PATH),
        "fallback_influence_index": int(fallback),
        "fallback_influence_name": str(influence_names[fallback]),
        "candidates": summaries,
        "note": "Unsupported vertices use head fallback only to avoid mouth/lid leakage during visual comparison.",
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
