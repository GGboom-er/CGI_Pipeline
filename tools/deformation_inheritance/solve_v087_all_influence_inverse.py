"""v087 全 influence R/T/S 位移画像反求权重候选。

这版不再以 Jaw 为中心。每个 target 点都用所有参与蒙皮的
influence 通道做闭式非负最小二乘，然后用 correspondence 置信、
权重语义一致性和全通道误差改善决定是否接受。
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
INPUT_NPZ = INFO_DIR / "v087_all_influence_input.npz"
OUT_NPZ = INFO_DIR / "v087_all_influence_candidates.npz"
OUT_JSON = INFO_DIR / "v087_all_influence_candidates_summary.json"
OUT_CSV = INFO_DIR / "v087_all_influence_candidates_summary.csv"

ROTATE_DEGREES = 10.0
TRANSLATE_CM = 1.0
SCALE_FACTOR = 1.1
ACTIVE_WEIGHT_EPS = 1e-3
TOP_K = 8
EPS = 1e-12


def _stats(values: np.ndarray) -> dict:
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"count": 0}
    return {
        "count": int(arr.size),
        "mean": float(np.mean(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(np.max(arr)),
    }


def _normalize_rows(weights: np.ndarray) -> np.ndarray:
    out = np.maximum(np.asarray(weights, dtype=np.float64), 0.0).copy()
    sums = out.sum(axis=1, keepdims=True)
    good = sums[:, 0] > EPS
    out[good] /= sums[good]
    return out


def _topk_prune(weights: np.ndarray, k: int, mask: np.ndarray | None = None) -> np.ndarray:
    out = np.asarray(weights, dtype=np.float64).copy()
    if k <= 0 or k >= out.shape[1]:
        return _normalize_rows(out)
    rows_to_process = np.where(mask)[0] if mask is not None else np.arange(out.shape[0])
    if rows_to_process.size == 0:
        return _normalize_rows(out)
    sub = out[rows_to_process]
    keep = np.argpartition(sub, -int(k), axis=1)[:, -int(k):]
    keep_mask = np.zeros_like(sub, dtype=bool)
    keep_mask[np.arange(sub.shape[0])[:, None], keep] = True
    sub[~keep_mask] = 0.0
    out[rows_to_process] = sub
    return _normalize_rows(out)


def _translation(dx: float, dy: float, dz: float) -> np.ndarray:
    m = np.eye(4, dtype=np.float64)
    m[3, 0] = float(dx)
    m[3, 1] = float(dy)
    m[3, 2] = float(dz)
    return m


def _rotation_axis(axis: str, degrees: float) -> np.ndarray:
    rad = math.radians(float(degrees))
    c = math.cos(rad)
    s = math.sin(rad)
    m = np.eye(4, dtype=np.float64)
    if axis == "rx":
        m[1, 1] = c
        m[1, 2] = s
        m[2, 1] = -s
        m[2, 2] = c
    elif axis == "ry":
        m[0, 0] = c
        m[0, 2] = -s
        m[2, 0] = s
        m[2, 2] = c
    elif axis == "rz":
        m[0, 0] = c
        m[0, 1] = s
        m[1, 0] = -s
        m[1, 1] = c
    else:
        raise ValueError(axis)
    return m


def _rotate_around(pivot: np.ndarray, axis: str, degrees: float) -> np.ndarray:
    p = np.asarray(pivot, dtype=np.float64)
    return _translation(-p[0], -p[1], -p[2]) @ _rotation_axis(axis, degrees) @ _translation(p[0], p[1], p[2])


def _scale_axis(axis: str, factor: float) -> np.ndarray:
    m = np.eye(4, dtype=np.float64)
    if axis == "sx":
        m[0, 0] = float(factor)
    elif axis == "sy":
        m[1, 1] = float(factor)
    elif axis == "sz":
        m[2, 2] = float(factor)
    else:
        raise ValueError(axis)
    return m


def _scale_around(pivot: np.ndarray, axis: str, factor: float) -> np.ndarray:
    p = np.asarray(pivot, dtype=np.float64)
    return _translation(-p[0], -p[1], -p[2]) @ _scale_axis(axis, factor) @ _translation(p[0], p[1], p[2])


def _channel_delta(pivot: np.ndarray, channel: str) -> np.ndarray:
    if channel in {"rx", "ry", "rz"}:
        return _rotate_around(pivot, channel, ROTATE_DEGREES)
    if channel == "tx":
        return _translation(TRANSLATE_CM, 0.0, 0.0)
    if channel == "ty":
        return _translation(0.0, TRANSLATE_CM, 0.0)
    if channel == "tz":
        return _translation(0.0, 0.0, TRANSLATE_CM)
    if channel in {"sx", "sy", "sz"}:
        return _scale_around(pivot, channel, SCALE_FACTOR)
    raise ValueError(channel)


def _apply_matrix(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    p4 = np.concatenate([np.asarray(points, dtype=np.float64), np.ones((points.shape[0], 1), dtype=np.float64)], axis=1)
    return (p4 @ matrix)[:, :3]


def _map_source_values(data: dict, source_values: np.ndarray) -> np.ndarray:
    values = np.asarray(source_values, dtype=np.float64)
    source_tris = np.asarray(data["source_tris"], dtype=np.int64)
    best_tri = np.asarray(data["best_tri"], dtype=np.int64)
    bary = np.asarray(data["best_bary"], dtype=np.float64)
    valid = best_tri >= 0
    safe_tri = best_tri.copy()
    safe_tri[~valid] = 0
    tri = source_tris[safe_tri]
    mapped = np.einsum("nk,nkc->nc", bary, values[tri])
    mapped[~valid] = 0.0
    return mapped


def _map_source_scalar(data: dict, source_values: np.ndarray) -> np.ndarray:
    return _map_source_values(data, np.asarray(source_values, dtype=np.float64)[:, None])[:, 0]


def _channels(groups: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for group in groups:
        if group == "rotate":
            out += ["rx", "ry", "rz"]
        elif group == "translate":
            out += ["tx", "ty", "tz"]
        elif group == "scale":
            out += ["sx", "sy", "sz"]
        else:
            raise ValueError(group)
    return out


def _load_data() -> dict:
    loaded = np.load(str(INPUT_NPZ), allow_pickle=True)
    return {key: loaded[key] for key in loaded.files}


def solve_inverse_weights(data: dict, channel_groups: tuple[str, ...]) -> np.ndarray:
    channels = _channels(channel_groups)
    source_points = np.asarray(data["source_points"], dtype=np.float64)
    target_points = np.asarray(data["target_points"], dtype=np.float64)
    source_weights = np.asarray(data["source_weights"], dtype=np.float64)
    source_matrices = np.asarray(data["source_matrices"], dtype=np.float64)
    target_matrices = np.asarray(data["target_matrices"], dtype=np.float64)
    source_pivots = np.asarray(data["source_pivots"], dtype=np.float64)
    domain = ~np.asarray(data["unsupported"], dtype=bool)

    n_target, n_joints = target_points.shape[0], source_weights.shape[1]
    numerator = np.zeros((n_target, n_joints), dtype=np.float64)
    denominator = np.zeros((n_target, n_joints), dtype=np.float64)

    for joint_id in range(n_joints):
        source_neutral = _apply_matrix(source_points, source_matrices[joint_id])
        target_neutral = _apply_matrix(target_points, target_matrices[joint_id])
        for channel in channels:
            delta = _channel_delta(source_pivots[joint_id], channel)
            source_delta = _apply_matrix(source_points, source_matrices[joint_id] @ delta) - source_neutral
            source_motion = source_weights[:, joint_id][:, None] * source_delta
            mapped_source_motion = _map_source_values(data, source_motion)
            target_delta = _apply_matrix(target_points, target_matrices[joint_id] @ delta) - target_neutral
            numerator[:, joint_id] += np.einsum("ij,ij->i", target_delta, mapped_source_motion)
            denominator[:, joint_id] += np.einsum("ij,ij->i", target_delta, target_delta)

    raw = np.zeros_like(numerator)
    valid = denominator > EPS
    raw[valid] = numerator[valid] / denominator[valid]
    raw = np.clip(raw, 0.0, 1.0)
    raw[~domain] = np.asarray(data["base_weights"], dtype=np.float64)[~domain]
    return _normalize_rows(raw)


def per_vertex_error(weights: np.ndarray, data: dict, channel_groups: tuple[str, ...] = ("rotate", "translate", "scale")) -> np.ndarray:
    channels = _channels(channel_groups)
    source_points = np.asarray(data["source_points"], dtype=np.float64)
    target_points = np.asarray(data["target_points"], dtype=np.float64)
    source_weights = np.asarray(data["source_weights"], dtype=np.float64)
    source_matrices = np.asarray(data["source_matrices"], dtype=np.float64)
    target_matrices = np.asarray(data["target_matrices"], dtype=np.float64)
    source_pivots = np.asarray(data["source_pivots"], dtype=np.float64)
    domain = ~np.asarray(data["unsupported"], dtype=bool)

    err_sum = np.zeros(target_points.shape[0], dtype=np.float64)
    count = np.zeros(target_points.shape[0], dtype=np.float64)
    mapped_weight_cache = [_map_source_scalar(data, source_weights[:, j]) for j in range(source_weights.shape[1])]
    for joint_id in range(source_weights.shape[1]):
        mapped_source_weight = mapped_weight_cache[joint_id]
        target_weight = weights[:, joint_id]
        active = domain & ((mapped_source_weight > ACTIVE_WEIGHT_EPS) | (target_weight > ACTIVE_WEIGHT_EPS))
        if not np.any(active):
            continue
        source_neutral = _apply_matrix(source_points, source_matrices[joint_id])
        target_neutral = _apply_matrix(target_points, target_matrices[joint_id])
        for channel in channels:
            delta = _channel_delta(source_pivots[joint_id], channel)
            source_motion = source_weights[:, joint_id][:, None] * (
                _apply_matrix(source_points, source_matrices[joint_id] @ delta) - source_neutral
            )
            mapped_source_motion = _map_source_values(data, source_motion)
            target_motion = target_weight[:, None] * (
                _apply_matrix(target_points, target_matrices[joint_id] @ delta) - target_neutral
            )
            error = np.linalg.norm(target_motion - mapped_source_motion, axis=1)
            err_sum[active] += error[active]
            count[active] += 1.0
    out = np.zeros_like(err_sum)
    good = count > 0
    out[good] = err_sum[good] / count[good]
    return out


def evaluate_weights(weights: np.ndarray, data: dict) -> dict:
    channels = _channels(("rotate", "translate", "scale"))
    groups = {"rotate": {"rx", "ry", "rz"}, "translate": {"tx", "ty", "tz"}, "scale": {"sx", "sy", "sz"}}
    source_points = np.asarray(data["source_points"], dtype=np.float64)
    target_points = np.asarray(data["target_points"], dtype=np.float64)
    source_weights = np.asarray(data["source_weights"], dtype=np.float64)
    source_matrices = np.asarray(data["source_matrices"], dtype=np.float64)
    target_matrices = np.asarray(data["target_matrices"], dtype=np.float64)
    source_pivots = np.asarray(data["source_pivots"], dtype=np.float64)
    domain = ~np.asarray(data["unsupported"], dtype=bool)

    global_all: list[np.ndarray] = []
    by_group = {name: [] for name in groups}
    by_channel = {name: [] for name in channels}
    rows = []
    mapped_weight_cache = [_map_source_scalar(data, source_weights[:, j]) for j in range(source_weights.shape[1])]

    for joint_id in range(source_weights.shape[1]):
        mapped_source_weight = mapped_weight_cache[joint_id]
        target_weight = weights[:, joint_id]
        active = domain & ((mapped_source_weight > ACTIVE_WEIGHT_EPS) | (target_weight > ACTIVE_WEIGHT_EPS))
        source_neutral = _apply_matrix(source_points, source_matrices[joint_id])
        target_neutral = _apply_matrix(target_points, target_matrices[joint_id])
        group_bucket = {name: [] for name in groups}
        worst_channel = None
        worst_p95 = -1.0
        worst_max = 0.0
        for channel in channels:
            delta = _channel_delta(source_pivots[joint_id], channel)
            source_motion = source_weights[:, joint_id][:, None] * (
                _apply_matrix(source_points, source_matrices[joint_id] @ delta) - source_neutral
            )
            mapped_source_motion = _map_source_values(data, source_motion)
            target_motion = target_weight[:, None] * (
                _apply_matrix(target_points, target_matrices[joint_id] @ delta) - target_neutral
            )
            error = np.linalg.norm(target_motion - mapped_source_motion, axis=1)
            if np.any(active):
                active_error = error[active]
                stats = _stats(active_error)
                global_all.append(active_error)
                by_channel[channel].append(active_error)
                for group, group_channels in groups.items():
                    if channel in group_channels:
                        by_group[group].append(active_error)
                        group_bucket[group].append(active_error)
            else:
                stats = {"count": 0}
            p95 = float(stats.get("p95", 0.0) or 0.0)
            if p95 > worst_p95:
                worst_p95 = p95
                worst_max = float(stats.get("max", 0.0) or 0.0)
                worst_channel = channel
        joint_name = str(data["influence_names"][joint_id])
        rows.append(
            {
                "joint_index": int(joint_id),
                "joint": joint_name,
                "joint_leaf": joint_name.split("|")[-1].split(":")[-1],
                "active_count": int(np.sum(active)),
                "worst_channel": worst_channel,
                "worst_channel_p95": float(max(worst_p95, 0.0)),
                "worst_channel_max": float(worst_max),
                "rotate_p95": _stats(np.concatenate(group_bucket["rotate"]) if group_bucket["rotate"] else np.asarray([])).get("p95"),
                "translate_p95": _stats(np.concatenate(group_bucket["translate"]) if group_bucket["translate"] else np.asarray([])).get("p95"),
                "scale_p95": _stats(np.concatenate(group_bucket["scale"]) if group_bucket["scale"] else np.asarray([])).get("p95"),
            }
        )

    def cat_stats(bucket: list[np.ndarray]) -> dict:
        return _stats(np.concatenate(bucket) if bucket else np.asarray([], dtype=np.float64))

    return {
        "global": {
            "all_channel_error": cat_stats(global_all),
            "by_group": {group: cat_stats(values) for group, values in by_group.items()},
            "by_channel": {channel: cat_stats(values) for channel, values in by_channel.items()},
        },
        "top_bad": sorted(rows, key=lambda row: (row["active_count"] > 0, row["worst_channel_p95"]), reverse=True)[:30],
    }


def make_guarded_variant(raw: np.ndarray, base: np.ndarray, data: dict, config: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    domain = ~np.asarray(data["unsupported"], dtype=bool)
    high = np.asarray(data["high_confidence"], dtype=bool)
    topology_bad = np.asarray(data["topology_discontinuity"], dtype=bool)
    semantic_bad = np.asarray(data["semantic_ambiguous"], dtype=bool) | np.asarray(data["face_semantic_discontinuity"], dtype=bool)
    mapped_weights = _map_source_values(data, np.asarray(data["source_weights"], dtype=np.float64))
    semantic_l1 = np.sum(np.abs(mapped_weights - base), axis=1)
    row_l1 = np.sum(np.abs(raw - base), axis=1)
    base_error = per_vertex_error(base, data, ("rotate", "translate", "scale"))
    raw_error = per_vertex_error(raw, data, ("rotate", "translate", "scale"))

    allow = domain.copy()
    if config.get("require_high_confidence", False):
        allow &= high
    if config.get("block_topology_discontinuity", False):
        allow &= ~topology_bad
    if config.get("block_semantic_ambiguous", False):
        allow &= ~semantic_bad
    allow &= semantic_l1 <= float(config["semantic_l1_max"])
    allow &= row_l1 <= float(config["row_l1_max"])
    allow &= raw_error + float(config["min_error_improvement"]) < base_error

    out = base.copy()
    out[allow] = raw[allow]
    out[allow] = _normalize_rows(out[allow])
    diag = {
        "config": config,
        "accepted_count": int(np.sum(allow)),
        "domain_count": int(np.sum(domain)),
        "semantic_l1_domain": _stats(semantic_l1[domain]),
        "row_l1_accepted": _stats(row_l1[allow]),
        "base_error_domain": _stats(base_error[domain]),
        "raw_error_domain": _stats(raw_error[domain]),
        "accepted_base_error": _stats(base_error[allow]),
        "accepted_raw_error": _stats(raw_error[allow]),
        "blocked_counts": {
            "unsupported": int(np.sum(~domain)),
            "not_high_confidence": int(np.sum(domain & ~high)),
            "topology_discontinuity": int(np.sum(domain & topology_bad)),
            "semantic_ambiguous": int(np.sum(domain & semantic_bad)),
            "semantic_l1_gt_threshold": int(np.sum(domain & (semantic_l1 > float(config["semantic_l1_max"])))),
            "row_l1_gt_threshold": int(np.sum(domain & (row_l1 > float(config["row_l1_max"])))),
            "no_all_channel_improvement": int(np.sum(domain & (raw_error + float(config["min_error_improvement"]) >= base_error))),
        },
    }
    return out, allow, diag


def main() -> dict:
    data = _load_data()
    domain = ~np.asarray(data["unsupported"], dtype=bool)
    base = _topk_prune(np.asarray(data["base_weights"], dtype=np.float64), TOP_K, domain)

    raw_rt = _topk_prune(solve_inverse_weights(data, ("rotate", "translate")), TOP_K, domain)
    raw_rts = _topk_prune(solve_inverse_weights(data, ("rotate", "translate", "scale")), TOP_K, domain)

    guard_configs = {
        "v087_rt_guard_strict": {
            "semantic_l1_max": 0.35,
            "row_l1_max": 0.45,
            "min_error_improvement": 0.001,
            "require_high_confidence": True,
            "block_topology_discontinuity": True,
            "block_semantic_ambiguous": True,
        },
        "v087_rt_guard_balanced": {
            "semantic_l1_max": 0.55,
            "row_l1_max": 0.75,
            "min_error_improvement": 0.0005,
            "require_high_confidence": False,
            "block_topology_discontinuity": True,
            "block_semantic_ambiguous": True,
        },
        "v087_rts_guard_balanced": {
            "semantic_l1_max": 0.55,
            "row_l1_max": 0.75,
            "min_error_improvement": 0.0005,
            "require_high_confidence": False,
            "block_topology_discontinuity": True,
            "block_semantic_ambiguous": True,
        },
        "v087_rt_guard_wide": {
            "semantic_l1_max": 0.75,
            "row_l1_max": 1.0,
            "min_error_improvement": 0.0002,
            "require_high_confidence": False,
            "block_topology_discontinuity": False,
            "block_semantic_ambiguous": True,
        },
    }

    variants = {
        "v087_base_hybrid_m0010": base,
        "v087_rt_raw_topk": raw_rt,
        "v087_rts_raw_topk": raw_rts,
    }
    guard_diag = {}
    accept_masks = {}
    for name, config in guard_configs.items():
        raw = raw_rts if name.startswith("v087_rts") else raw_rt
        weights, accept, diag = make_guarded_variant(raw, base, data, config)
        variants[name] = weights
        accept_masks[name] = accept
        guard_diag[name] = diag

    reports = {name: evaluate_weights(weights, data) for name, weights in variants.items()}
    ranking = sorted(
        [
            {
                "variant": name,
                "all_p95": report["global"]["all_channel_error"].get("p95"),
                "all_max": report["global"]["all_channel_error"].get("max"),
                "rotate_p95": report["global"]["by_group"]["rotate"].get("p95"),
                "translate_p95": report["global"]["by_group"]["translate"].get("p95"),
                "scale_p95": report["global"]["by_group"]["scale"].get("p95"),
                "changed_vs_base": int(np.sum(np.sum(np.abs(weights - base), axis=1) > 1e-8)),
            }
            for name, weights in variants.items()
            for report in [reports[name]]
        ],
        key=lambda row: (float(row["all_p95"] or 1e9), float(row["all_max"] or 1e9)),
    )

    influence_names = [str(x) for x in data["influence_names"].tolist()]
    save_payload = {
        # Maya 2025 的 NumPy 版本读不了 NumPy 2 object pickle，
        # 所以这里固定成 Unicode 数组，禁止 object dtype。
        "influence_names": np.asarray(influence_names, dtype="<U512"),
        "domain_mask": domain,
        **{f"weights__{name}": weights.astype(np.float32) for name, weights in variants.items()},
        **{f"accept__{name}": mask for name, mask in accept_masks.items()},
    }
    np.savez_compressed(str(OUT_NPZ), **save_payload)

    report_json = {
        "status": "SUCCESS",
        "input_npz": str(INPUT_NPZ),
        "output_npz": str(OUT_NPZ),
        "ranking": ranking,
        "guard_diag": guard_diag,
        "top_bad": {name: reports[name]["top_bad"][:20] for name in variants},
        "note": "v087 是全 influence R/T/S 反求与验收，不以 Jaw 为唯一目标。raw 只作诊断，推荐先看 guard 版本。",
    }
    OUT_JSON.write_text(json.dumps(report_json, ensure_ascii=False, indent=2), encoding="utf-8")

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["variant", "all_p95", "all_max", "rotate_p95", "translate_p95", "scale_p95", "changed_vs_base"])
        writer.writeheader()
        for row in ranking:
            writer.writerow(row)

    result = {
        "status": "SUCCESS",
        "output_npz": str(OUT_NPZ),
        "report_json": str(OUT_JSON),
        "report_csv": str(OUT_CSV),
        "ranking": ranking,
        "recommended_visual_candidates": [
            "v087_base_hybrid_m0010",
            "v087_rt_guard_strict",
            "v087_rt_guard_balanced",
            "v087_rts_guard_balanced",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    main()
