import json
from pathlib import Path

import numpy as np


INFO_DIR = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\.info\a_weight_transfer_v082_reboot"
)
HYBRID_WEIGHTS_PATH = INFO_DIR / "v082_weight_candidates_hybrid_motion_gate.npz"
V084_WEIGHTS_PATH = INFO_DIR / "v084_weight_candidates_constrained_inverse.npz"
MULTIPOSE_ERRORS_PATH = INFO_DIR / "v084_multipose_actual_dg_error_arrays.npz"
OUTPUT_PATH = INFO_DIR / "v085_weight_candidates_multipose_gate.npz"
SUMMARY_PATH = INFO_DIR / "v085_weight_candidates_multipose_gate_summary.json"


BASE_KEY = "hybrid_m0010_cc3"
V084_KEY = "v084_inverse_motion_accepted"
OUTPUT_KEY = "v085_inverse_motion_multiposeGated"
REGRESSION_THRESHOLD = 0.005


def _row_l1(a, b):
    return np.sum(np.abs(a - b), axis=1)


def _stat(values):
    values = np.asarray(values)
    if values.size == 0:
        return {"count": 0}
    return {
        "count": int(values.size),
        "mean": float(np.mean(values)),
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "max": float(np.max(values)),
    }


def main():
    hybrid = np.load(str(HYBRID_WEIGHTS_PATH), allow_pickle=True)
    v084 = np.load(str(V084_WEIGHTS_PATH), allow_pickle=True)
    errors = np.load(str(MULTIPOSE_ERRORS_PATH), allow_pickle=True)

    base_weights = hybrid[BASE_KEY].astype(np.float32)
    candidate = v084[V084_KEY].astype(np.float32)
    gated = candidate.copy()

    regression_ids = set()
    pose_breakdown = {}
    for key in errors.files:
        if not key.endswith("__%s__supported_motion_error" % V084_KEY):
            continue
        pose = key[: -len("__%s__supported_motion_error" % V084_KEY)]
        base_key = pose + "__" + BASE_KEY + "__supported_motion_error"
        if base_key not in errors:
            continue
        cand_err = errors[key]
        base_err = errors[base_key]
        valid = np.isfinite(cand_err) & np.isfinite(base_err)
        reg = np.where(valid & ((cand_err - base_err) > REGRESSION_THRESHOLD))[0]
        pose_breakdown[pose] = {
            "regression_count": int(len(reg)),
            "max_regression": float(np.max(cand_err[reg] - base_err[reg])) if len(reg) else 0.0,
        }
        regression_ids.update([int(v) for v in reg.tolist()])

    regression_ids = np.asarray(sorted(regression_ids), dtype=np.int32)
    if len(regression_ids):
        gated[regression_ids] = base_weights[regression_ids]

    row_sum = gated.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0.0] = 1.0
    gated = gated / row_sum

    changed_before = np.where(_row_l1(candidate, base_weights) > 1e-5)[0].astype(np.int32)
    changed_after = np.where(_row_l1(gated, base_weights) > 1e-5)[0].astype(np.int32)

    np.savez_compressed(
        str(OUTPUT_PATH),
        **{
            OUTPUT_KEY: gated.astype(np.float32),
            OUTPUT_KEY + "_regression_reverted_ids": regression_ids,
            OUTPUT_KEY + "_changed_before_gate_ids": changed_before,
            OUTPUT_KEY + "_changed_after_gate_ids": changed_after,
        },
    )

    summary = {
        "status": "SUCCESS",
        "base_key": BASE_KEY,
        "input_key": V084_KEY,
        "output_key": OUTPUT_KEY,
        "threshold": REGRESSION_THRESHOLD,
        "output_path": str(OUTPUT_PATH),
        "regression_reverted_count": int(len(regression_ids)),
        "changed_before_gate_count": int(len(changed_before)),
        "changed_after_gate_count": int(len(changed_after)),
        "row_l1_candidate_vs_base": _stat(_row_l1(candidate, base_weights)[changed_before]),
        "row_l1_gated_vs_base": _stat(_row_l1(gated, base_weights)[changed_after]),
        "pose_breakdown": pose_breakdown,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
