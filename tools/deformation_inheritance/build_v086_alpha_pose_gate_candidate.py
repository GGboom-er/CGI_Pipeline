import json
from pathlib import Path

import numpy as np


INFO_DIR = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\.info\a_weight_transfer_v082_reboot"
)
HYBRID_WEIGHTS_PATH = INFO_DIR / "v082_weight_candidates_hybrid_motion_gate.npz"
V084_WEIGHTS_PATH = INFO_DIR / "v084_weight_candidates_constrained_inverse.npz"
VECTORS_PATH = INFO_DIR / "v086_alpha_pose_vectors.npz"
OUTPUT_PATH = INFO_DIR / "v086_weight_candidates_alpha_pose_gate.npz"
SUMMARY_PATH = INFO_DIR / "v086_weight_candidates_alpha_pose_gate_summary.json"

BASE_KEY = "hybrid_m0010_cc3"
MOTION_KEY = "v084_inverse_motion_accepted"
ALPHA_GRID = np.asarray([i / 20.0 for i in range(21)], dtype=np.float64)

VARIANTS = {
    "v086_alphaGate_safe005": {
        "nonjaw_regression_limit": 0.005,
        "jaw_regression_limit": 0.001,
        "min_jaw_reward": 0.0002,
    },
    "v086_alphaGate_strict003": {
        "nonjaw_regression_limit": 0.003,
        "jaw_regression_limit": 0.0005,
        "min_jaw_reward": 0.0002,
    },
    "v086_alphaGate_balanced": {
        "nonjaw_regression_limit": 0.004,
        "jaw_regression_limit": 0.001,
        "min_jaw_reward": 0.00005,
    },
}


def _row_l1(a, b):
    return np.sum(np.abs(a - b), axis=1)


def _stat(values):
    values = np.asarray(values)
    values = values[np.isfinite(values)]
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


def _load_pose_vectors(vec):
    supported_ids = vec["supported_ids"].astype(np.int32)
    poses = []
    for key in vec.files:
        suffix = "__" + BASE_KEY + "__motion_error_vec"
        if not key.endswith(suffix):
            continue
        pose = key[: -len(suffix)]
        motion_key = pose + "__" + MOTION_KEY + "__motion_error_vec"
        if motion_key not in vec:
            continue
        poses.append(
            {
                "name": pose,
                "is_jaw": pose.startswith("jaw_"),
                "base": vec[key].astype(np.float64),
                "motion": vec[motion_key].astype(np.float64),
            }
        )
    poses.sort(key=lambda item: item["name"])
    return supported_ids, poses


def _choose_alphas(supported_ids, poses, base_weights, motion_weights, config):
    vcount = base_weights.shape[0]
    alpha = np.zeros((vcount,), dtype=np.float32)
    reason = np.full((vcount,), "unchanged", dtype=object)
    changed = _row_l1(motion_weights, base_weights) > 1e-5
    supported_changed_local = np.where(changed[supported_ids])[0]
    nonjaw = [p for p in poses if not p["is_jaw"]]
    jaw = [p for p in poses if p["is_jaw"]]

    for local_i in supported_changed_local:
        vid = int(supported_ids[local_i])
        best_alpha = 0.0
        best_jaw_reward = 0.0
        for a in ALPHA_GRID[1:]:
            safe = True
            if nonjaw:
                for pose in nonjaw:
                    base_vec = pose["base"][local_i]
                    motion_vec = pose["motion"][local_i]
                    err = np.linalg.norm(base_vec + a * (motion_vec - base_vec))
                    base_err = np.linalg.norm(base_vec)
                    if err > base_err + config["nonjaw_regression_limit"]:
                        safe = False
                        break
            if not safe:
                continue

            jaw_reward = 0.0
            jaw_safe = True
            if jaw:
                base_errs = []
                alpha_errs = []
                for pose in jaw:
                    base_vec = pose["base"][local_i]
                    motion_vec = pose["motion"][local_i]
                    base_err = np.linalg.norm(base_vec)
                    err = np.linalg.norm(base_vec + a * (motion_vec - base_vec))
                    base_errs.append(base_err)
                    alpha_errs.append(err)
                    if err > base_err + config["jaw_regression_limit"]:
                        jaw_safe = False
                        break
                if not jaw_safe:
                    continue
                jaw_reward = float(np.mean(base_errs) - np.mean(alpha_errs))

            if jaw_reward >= config["min_jaw_reward"]:
                best_alpha = float(a)
                best_jaw_reward = jaw_reward

        if best_alpha > 0.0:
            alpha[vid] = best_alpha
            if best_alpha >= 0.999:
                reason[vid] = "full_motion_safe"
            else:
                reason[vid] = "partial_alpha_safe"
        else:
            reason[vid] = "blocked_by_pose_gate"

    out = base_weights + alpha[:, None].astype(np.float32) * (motion_weights - base_weights)
    row_sum = out.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0.0] = 1.0
    out = out / row_sum
    return out.astype(np.float32), alpha, reason


def main():
    hybrid = np.load(str(HYBRID_WEIGHTS_PATH), allow_pickle=True)
    v084 = np.load(str(V084_WEIGHTS_PATH), allow_pickle=True)
    vec = np.load(str(VECTORS_PATH), allow_pickle=True)

    base_weights = hybrid[BASE_KEY].astype(np.float32)
    motion_weights = v084[MOTION_KEY].astype(np.float32)
    supported_ids, poses = _load_pose_vectors(vec)

    outputs = {}
    summary = {
        "status": "SUCCESS",
        "base_key": BASE_KEY,
        "motion_key": MOTION_KEY,
        "vectors_path": str(VECTORS_PATH),
        "output_path": str(OUTPUT_PATH),
        "alpha_grid": [float(x) for x in ALPHA_GRID.tolist()],
        "supported_count": int(len(supported_ids)),
        "changed_vs_base_count": int(np.sum(_row_l1(motion_weights, base_weights) > 1e-5)),
        "poses": [{"name": p["name"], "is_jaw": bool(p["is_jaw"])} for p in poses],
        "variants": {},
    }

    for name, config in VARIANTS.items():
        weights, alpha, reason = _choose_alphas(supported_ids, poses, base_weights, motion_weights, config)
        changed_ids = np.where(_row_l1(weights, base_weights) > 1e-5)[0].astype(np.int32)
        partial_ids = np.where((alpha > 1e-6) & (alpha < 0.999))[0].astype(np.int32)
        full_ids = np.where(alpha >= 0.999)[0].astype(np.int32)
        blocked_ids = np.where(reason == "blocked_by_pose_gate")[0].astype(np.int32)

        outputs[name] = weights
        outputs[name + "_alpha"] = alpha.astype(np.float32)
        outputs[name + "_changed_ids"] = changed_ids
        outputs[name + "_partial_alpha_ids"] = partial_ids
        outputs[name + "_full_alpha_ids"] = full_ids
        outputs[name + "_blocked_ids"] = blocked_ids

        nonzero_alpha = alpha[alpha > 1e-6]
        summary["variants"][name] = {
            "config": config,
            "changed_ids": int(len(changed_ids)),
            "partial_alpha_ids": int(len(partial_ids)),
            "full_alpha_ids": int(len(full_ids)),
            "blocked_ids": int(len(blocked_ids)),
            "alpha": _stat(nonzero_alpha),
            "row_l1_vs_base": _stat(_row_l1(weights, base_weights)[changed_ids]),
        }

    np.savez_compressed(str(OUTPUT_PATH), **outputs)
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
