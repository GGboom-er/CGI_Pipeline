from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from validate_mhead_to_a_v082_correspondence import INFO_DIR, DEFAULT_INPUT, run_validator


SWEEP_DIR = INFO_DIR / "param_sweep"


USER_CHECK_POINTS = [
    1165,
    1166,
    1631,
    9377,
    9379,
    8796,
    8949,
    9232,
    9233,
    9288,
    9289,
]


FACE_RANGES = {
    "mouth_red_left_9095_9150": (9095, 9150),
    "mouth_red_mid_9263_9318": (9263, 9318),
}


CONFIGS = [
    {
        "name": "v082p00_distance_k64",
        "candidate_k": 64,
        "selection_mode": "distance",
    },
    {
        "name": "v082p01_distance_k128",
        "candidate_k": 128,
        "selection_mode": "distance",
    },
    {
        "name": "v082p02_energy_n050",
        "candidate_k": 128,
        "selection_mode": "energy",
        "normal_weight": 0.50,
        "family_purity_weight": 0.10,
    },
    {
        "name": "v082p03_energy_n100",
        "candidate_k": 128,
        "selection_mode": "energy",
        "normal_weight": 1.00,
        "family_purity_weight": 0.15,
    },
    {
        "name": "v082p04_energy_n160",
        "candidate_k": 160,
        "selection_mode": "energy",
        "normal_weight": 1.60,
        "family_purity_weight": 0.25,
    },
    {
        "name": "v082p05_energy_n100_loose_family",
        "candidate_k": 128,
        "selection_mode": "energy",
        "normal_weight": 1.00,
        "family_purity_weight": 0.10,
        "family_conf_threshold": 0.50,
    },
    {
        "name": "v082p06_energy_n100_tight_face",
        "candidate_k": 128,
        "selection_mode": "energy",
        "normal_weight": 1.00,
        "family_purity_weight": 0.15,
        "face_semantic_l1": 1.15,
    },
]


def _faces_from_data(data):
    counts = data["target_face_counts"].astype(int)
    offsets = data["target_face_offsets"].astype(int)
    flat = data["target_face_vertices"].astype(int)
    return counts, offsets, flat


def _face_range_vertices(data, start_face: int, end_face: int) -> np.ndarray:
    counts, offsets, flat = _faces_from_data(data)
    vertices: set[int] = set()
    for face_id in range(start_face, end_face + 1):
        vertices.update(flat[offsets[face_id] : offsets[face_id] + counts[face_id]].tolist())
    return np.asarray(sorted(vertices), dtype=np.int32)


def _score_summary(summary, val, data):
    row = {
        "name": summary["thresholds"]["selection_mode"],
        "unsupported": summary["counts"]["unsupported"],
        "supported": summary["counts"]["supported"],
        "low_confidence_actionable": summary["counts"]["low_confidence_actionable"],
        "strict_block": summary["counts"]["strict_block"],
        "normal_mismatch": summary["counts"]["normal_mismatch"],
        "semantic_ambiguous": summary["counts"]["semantic_ambiguous"],
        "topology_discontinuity": summary["counts"]["topology_discontinuity"],
        "face_semantic_discontinuity": summary["counts"]["face_semantic_discontinuity"],
    }
    low = val["low_confidence"].astype(bool)
    strict = (
        val["normal_mismatch"].astype(bool)
        | val["semantic_ambiguous"].astype(bool)
        | val["topology_discontinuity"].astype(bool)
        | val["face_semantic_discontinuity"].astype(bool)
    ) & (~val["unsupported"].astype(bool))
    row["user_points_low_count"] = int(sum(bool(low[i]) for i in USER_CHECK_POINTS))
    row["user_points_strict_count"] = int(sum(bool(strict[i]) for i in USER_CHECK_POINTS))
    for label, face_range in FACE_RANGES.items():
        ids = _face_range_vertices(data, face_range[0], face_range[1])
        row[f"{label}_verts"] = int(len(ids))
        row[f"{label}_low"] = int(low[ids].sum())
        row[f"{label}_strict"] = int(strict[ids].sum())
    return row


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    data = np.load(DEFAULT_INPUT, allow_pickle=True)
    rows = []
    for config in CONFIGS:
        name = config["name"]
        print(f"[sweep] running {name}")
        summary = run_validator(
            DEFAULT_INPUT,
            SWEEP_DIR,
            output_prefix=name,
            candidate_k=config.get("candidate_k", 64),
            selection_mode=config.get("selection_mode", "distance"),
            normal_weight=config.get("normal_weight", 0.0),
            family_purity_weight=config.get("family_purity_weight", 0.0),
            normal_mismatch_threshold=config.get("normal_mismatch_threshold", 0.25),
            family_conf_threshold=config.get("family_conf_threshold", 0.58),
            near_scale=config.get("near_scale", 2.5),
            support_scale=config.get("support_scale", 5.0),
            far_scale=config.get("far_scale", 8.0),
            ambiguity_scale=config.get("ambiguity_scale", 1.10),
            source_jump_scale=config.get("source_jump_scale", 7.0),
            face_semantic_l1=config.get("face_semantic_l1", 1.35),
        )
        val = np.load(SWEEP_DIR / f"{name}_correspondence_validation.npz", allow_pickle=True)
        row = _score_summary(summary, val, data)
        row["name"] = name
        row["candidate_k"] = config.get("candidate_k", 64)
        row["selection_mode"] = config.get("selection_mode", "distance")
        row["normal_weight"] = config.get("normal_weight", 0.0)
        row["family_purity_weight"] = config.get("family_purity_weight", 0.0)
        row["family_conf_threshold"] = config.get("family_conf_threshold", 0.58)
        row["face_semantic_l1"] = config.get("face_semantic_l1", 1.35)
        rows.append(row)

    out_json = SWEEP_DIR / "v082_param_sweep_summary.json"
    out_json.write_text(json.dumps({"status": "SUCCESS", "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    out_csv = SWEEP_DIR / "v082_param_sweep_summary.csv"
    keys = list(rows[0].keys())
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        fh.write(",".join(keys) + "\n")
        for row in rows:
            fh.write(",".join(str(row[k]) for k in keys) + "\n")

    print(json.dumps({"status": "SUCCESS", "summary_json": str(out_json), "rows": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
