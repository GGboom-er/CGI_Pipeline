# -*- coding: utf-8 -*-
"""v091 patch-level constrained optimization。

这版不再只做候选替换后验收，而是在 patch 内直接优化：
- 顶点位移误差
- 边长相对变化
- 面面积相对变化
- 面法线方向
- 权重先验与同 patch 连续性

求解方式为离散坐标下降：每个顶点在一组受限权重状态中选择最优状态。
这样能把边/面/法线这类 patch 级目标纳入求解，同时避免连续高维 SLSQP
在生产资产上不可控。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
INPUT_NPZ = INFO_DIR / "v091_patch_objective_input.npz"
V089B_WEIGHTS = INFO_DIR / "v089b_fast_continuous_field_candidates.npz"
V090_WEIGHTS = INFO_DIR / "v090_patch_surface_candidates.npz"
OUT_NPZ = INFO_DIR / "v091_patch_constrained_objective_candidates.npz"
OUT_JSON = INFO_DIR / "v091_patch_constrained_objective_summary.json"
OUT_CSV = INFO_DIR / "v091_patch_constrained_objective_summary.csv"

EPS = 1e-8
TOPK = 10


CONFIGS = {
    "v091_surface_balanced": {
        "vertex": 1.0,
        "edge": 0.85,
        "area": 0.65,
        "normal": 0.22,
        "base": 0.018,
        "expected": 0.008,
        "smooth": 0.012,
        "iterations": 3,
    },
    "v091_surface_visible": {
        "vertex": 1.20,
        "edge": 1.05,
        "area": 0.80,
        "normal": 0.30,
        "base": 0.006,
        "expected": 0.004,
        "smooth": 0.008,
        "iterations": 4,
    },
    "v091_surface_shape_guard": {
        "vertex": 0.85,
        "edge": 1.35,
        "area": 1.15,
        "normal": 0.42,
        "base": 0.012,
        "expected": 0.007,
        "smooth": 0.018,
        "iterations": 4,
    },
}


def _stat(values: np.ndarray) -> dict:
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"count": 0}
    return {
        "count": int(arr.size),
        "mean": float(np.mean(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(np.max(arr)),
    }


def _normalize_rows(weights: np.ndarray) -> np.ndarray:
    out = np.maximum(np.asarray(weights, dtype=np.float64), 0.0)
    row_sum = out.sum(axis=1, keepdims=True)
    row_sum[row_sum <= EPS] = 1.0
    return out / row_sum


def _topk_changed(full: np.ndarray, changed: np.ndarray, topk: int = TOPK) -> np.ndarray:
    out = np.asarray(full, dtype=np.float64).copy()
    for row_id in np.where(changed)[0].astype(np.int64).tolist():
        row = out[row_id]
        if np.count_nonzero(row > EPS) <= topk:
            continue
        keep = np.argpartition(row, -topk)[-topk:]
        new_row = np.zeros_like(row)
        new_row[keep] = row[keep]
        out[row_id] = new_row
    out[changed] = _normalize_rows(out[changed])
    return out


def _blend(base: np.ndarray, other: np.ndarray, alpha: float) -> np.ndarray:
    return _normalize_rows((1.0 - float(alpha)) * base + float(alpha) * other)


def _tri_area(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    if len(tris) == 0:
        return np.zeros((0,), dtype=np.float64)
    return 0.5 * np.linalg.norm(np.cross(points[tris[:, 1]] - points[tris[:, 0]], points[tris[:, 2]] - points[tris[:, 0]]), axis=1)


def _tri_normal(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    if len(tris) == 0:
        return np.zeros((0, 3), dtype=np.float64)
    n = np.cross(points[tris[:, 1]] - points[tris[:, 0]], points[tris[:, 2]] - points[tris[:, 0]])
    length = np.linalg.norm(n, axis=1, keepdims=True)
    length[length <= EPS] = 1.0
    return n / length


class PatchObjective:
    def __init__(
        self,
        state_weights: np.ndarray,
        state_positions: np.ndarray,
        target_neutral: np.ndarray,
        expected_neutral: np.ndarray,
        expected_pose: np.ndarray,
        edges: np.ndarray,
        tris: np.ndarray,
        base_weights_roi: np.ndarray,
        expected_weights_roi: np.ndarray,
        core_local: np.ndarray,
        config: dict,
    ) -> None:
        self.state_weights = state_weights
        self.state_positions = state_positions
        self.target_neutral = target_neutral.astype(np.float64)
        self.expected_neutral = expected_neutral.astype(np.float64)
        self.expected_pose = expected_pose.astype(np.float64)
        self.edges = edges.astype(np.int64)
        self.tris = tris.astype(np.int64)
        self.base_weights_roi = base_weights_roi.astype(np.float64)
        self.expected_weights_roi = expected_weights_roi.astype(np.float64)
        self.core_local = core_local.astype(bool)
        self.config = config

        self.target_edge_neutral = self._edge_lengths(self.target_neutral)
        self.expected_edge_neutral = self._edge_lengths(self.expected_neutral)
        self.target_area_neutral = _tri_area(self.target_neutral, self.tris)
        self.expected_area_neutral = _tri_area(self.expected_neutral, self.tris)
        self.expected_edge_pose = np.stack([self._edge_lengths(p) for p in self.expected_pose]) if len(self.edges) else np.zeros((len(self.expected_pose), 0))
        self.expected_area_pose = np.stack([_tri_area(p, self.tris) for p in self.expected_pose]) if len(self.tris) else np.zeros((len(self.expected_pose), 0))
        self.expected_normal_pose = np.stack([_tri_normal(p, self.tris) for p in self.expected_pose]) if len(self.tris) else np.zeros((len(self.expected_pose), 0, 3))

    def _edge_lengths(self, points: np.ndarray) -> np.ndarray:
        if len(self.edges) == 0:
            return np.zeros((0,), dtype=np.float64)
        return np.linalg.norm(points[self.edges[:, 0]] - points[self.edges[:, 1]], axis=1)

    def positions(self, states: np.ndarray) -> np.ndarray:
        ids = np.arange(len(states), dtype=np.int64)
        return self.state_positions[:, states.astype(np.int64), ids, :].astype(np.float64)

    def weights(self, states: np.ndarray) -> np.ndarray:
        ids = np.arange(len(states), dtype=np.int64)
        return self.state_weights[states.astype(np.int64), ids, :].astype(np.float64)

    def metrics(self, states: np.ndarray) -> dict[str, np.ndarray]:
        pred = self.positions(states)
        pred_disp = pred - self.target_neutral[None, :, :]
        exp_disp = self.expected_pose - self.expected_neutral[None, :, :]
        vertex = np.linalg.norm(pred_disp - exp_disp, axis=2)
        if np.any(self.core_local):
            vertex_weighted = vertex[:, self.core_local]
        else:
            vertex_weighted = vertex

        if len(self.edges):
            pred_edge = np.stack([self._edge_lengths(p) for p in pred])
            edge = np.abs(
                np.log((pred_edge + EPS) / (self.target_edge_neutral[None, :] + EPS))
                - np.log((self.expected_edge_pose + EPS) / (self.expected_edge_neutral[None, :] + EPS))
            )
        else:
            edge = np.zeros((pred.shape[0], 0), dtype=np.float64)

        if len(self.tris):
            pred_area = np.stack([_tri_area(p, self.tris) for p in pred])
            area = np.abs(
                np.log((pred_area + EPS) / (self.target_area_neutral[None, :] + EPS))
                - np.log((self.expected_area_pose + EPS) / (self.expected_area_neutral[None, :] + EPS))
            )
            pred_normal = np.stack([_tri_normal(p, self.tris) for p in pred])
            normal = 1.0 - np.clip(np.einsum("ptj,ptj->pt", pred_normal, self.expected_normal_pose), -1.0, 1.0)
        else:
            area = np.zeros((pred.shape[0], 0), dtype=np.float64)
            normal = np.zeros((pred.shape[0], 0), dtype=np.float64)

        return {"vertex": vertex, "vertex_core": vertex_weighted, "edge": edge, "area": area, "normal": normal}

    def objective(self, states: np.ndarray) -> float:
        metrics = self.metrics(states)
        cfg = self.config
        value = 0.0
        value += cfg["vertex"] * float(np.mean(metrics["vertex_core"] ** 2))
        if metrics["edge"].size:
            value += cfg["edge"] * float(np.mean(metrics["edge"] ** 2))
        if metrics["area"].size:
            value += cfg["area"] * float(np.mean(metrics["area"] ** 2))
        if metrics["normal"].size:
            value += cfg["normal"] * float(np.mean(metrics["normal"] ** 2))

        weights = self.weights(states)
        value += cfg["base"] * float(np.mean((weights - self.base_weights_roi) ** 2))
        value += cfg["expected"] * float(np.mean((weights - self.expected_weights_roi) ** 2))
        if len(self.edges):
            smooth = weights[self.edges[:, 0]] - weights[self.edges[:, 1]]
            value += cfg["smooth"] * float(np.mean(smooth ** 2))
        return float(value)

    def summary(self, states: np.ndarray) -> dict:
        metrics = self.metrics(states)
        return {
            "objective": self.objective(states),
            "vertex": _stat(metrics["vertex"].reshape(-1)),
            "vertex_core": _stat(metrics["vertex_core"].reshape(-1)),
            "edge": _stat(metrics["edge"].reshape(-1)),
            "area": _stat(metrics["area"].reshape(-1)),
            "normal": _stat(metrics["normal"].reshape(-1)),
        }


def _build_state_bank(inp: dict, v089b: dict, v090: dict) -> tuple[list[str], np.ndarray]:
    base_roi = inp["base_weights_roi"].astype(np.float64)
    expected_roi = inp["expected_weights_roi"].astype(np.float64)
    roi_ids = inp["roi_ids"].astype(np.int64)
    states = [("base", base_roi)]

    def add_full(name: str, key: str, alpha: float | None = None):
        if key not in v089b and key not in v090:
            return
        full = (v089b.get(key) if key in v089b else v090.get(key)).astype(np.float64)
        row = full[roi_ids]
        if alpha is not None:
            row = _blend(base_roi, row, alpha)
            states.append(("%s_a%.2f" % (name, alpha), row))
        else:
            states.append((name, _normalize_rows(row)))

    add_full("actual_safe", "weights__v089b_actual_safe_rt", 0.35)
    add_full("actual_safe", "weights__v089b_actual_safe_rt", 0.65)
    add_full("visibility_guard", "weights__v089b_visibility_guard_rt", 0.65)
    add_full("field_prior", "weights__v089b_field_prior_topk_DIAGNOSTIC", 0.18)
    add_full("field_prior", "weights__v089b_field_prior_topk_DIAGNOSTIC", 0.35)
    add_full("v090_balanced", "weights__v090_balanced_patch_blend035", None)
    add_full("v090_broad_smooth", "weights__v090_broad_patch_blend060_smooth", None)
    states.append(("expected_weight_a0.35", _blend(base_roi, expected_roi, 0.35)))
    states.append(("expected_weight_a0.65", _blend(base_roi, expected_roi, 0.65)))

    names = []
    rows = []
    seen = set()
    for name, row in states:
        key = (name, row.shape)
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
        rows.append(_normalize_rows(row))
    return names, np.stack(rows).astype(np.float64)


def _state_positions(state_weights: np.ndarray, basis: np.ndarray) -> np.ndarray:
    # state_weights: [S, N, I], basis: [P, N, I, 3] -> [P, S, N, 3]
    return np.einsum("sni,pnij->psnj", state_weights, basis, optimize=True).astype(np.float32)


def _coordinate_descent(obj: PatchObjective, initial: np.ndarray, iterations: int) -> tuple[np.ndarray, list[dict]]:
    states = initial.copy()
    history = []
    best_value = obj.objective(states)
    order = np.arange(len(states), dtype=np.int64)
    # 优先优化 core 顶点，其余点作为表面连续性缓冲。
    if np.any(obj.core_local):
        core = np.where(obj.core_local)[0]
        shell = np.where(~obj.core_local)[0]
        order = np.concatenate([core, shell]).astype(np.int64)
    for iteration in range(int(iterations)):
        changed = 0
        for vid in order.tolist():
            current_state = int(states[vid])
            local_best_state = current_state
            local_best_value = best_value
            for candidate_state in range(obj.state_weights.shape[0]):
                if candidate_state == current_state:
                    continue
                trial = states.copy()
                trial[vid] = candidate_state
                value = obj.objective(trial)
                if value < local_best_value - 1e-10:
                    local_best_value = value
                    local_best_state = candidate_state
            if local_best_state != current_state:
                states[vid] = local_best_state
                best_value = local_best_value
                changed += 1
        history.append({"iteration": int(iteration + 1), "changed": int(changed), "objective": float(best_value)})
        if changed == 0:
            break
    return states, history


def _candidate_full_weights(inp: dict, state_weights: np.ndarray, states: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    base_full = inp["base_weights"].astype(np.float64).copy()
    roi_ids = inp["roi_ids"].astype(np.int64)
    selected = state_weights[states.astype(np.int64), np.arange(len(states))]
    changed_local = states.astype(np.int64) != 0
    changed_full = np.zeros((base_full.shape[0],), dtype=bool)
    changed_full[roi_ids[changed_local]] = True
    base_full[roi_ids[changed_local]] = selected[changed_local]
    base_full = _topk_changed(base_full, changed_full)
    return base_full.astype(np.float32), changed_full


def main() -> dict:
    inp = {key: value for key, value in np.load(str(INPUT_NPZ), allow_pickle=True).items()}
    v089b = {key: value for key, value in np.load(str(V089B_WEIGHTS), allow_pickle=True).items()}
    v090 = {key: value for key, value in np.load(str(V090_WEIGHTS), allow_pickle=True).items()}

    state_names, state_weights = _build_state_bank(inp, v089b, v090)
    state_pos = _state_positions(state_weights, inp["basis_pose_roi"].astype(np.float64))

    roi_ids = inp["roi_ids"].astype(np.int64)
    core_set = set(inp["core_ids"].astype(np.int64).tolist())
    core_local = np.asarray([int(vid) in core_set for vid in roi_ids.tolist()], dtype=bool)

    outputs = {
        "influence_names": inp["influence_names"].astype(str),
        "roi_ids": roi_ids.astype(np.int32),
        "core_ids": inp["core_ids"].astype(np.int32),
        "local_edges": inp["local_edges"].astype(np.int32),
        "local_tris": inp["local_tris"].astype(np.int32),
        "state_names": np.asarray(state_names).astype(str),
    }
    summary = {
        "status": "SUCCESS",
        "input_npz": str(INPUT_NPZ),
        "output_npz": str(OUT_NPZ),
        "roi_vertices": int(len(roi_ids)),
        "core_vertices": int(np.sum(core_local)),
        "state_names": state_names,
        "configs": {},
        "note": "v091 使用 vertex/edge/area/normal 多目标坐标下降；Maya actual DG 仍是最终门禁。",
    }

    base_states = np.zeros((len(roi_ids),), dtype=np.int16)
    base_obj = PatchObjective(
        state_weights,
        state_pos,
        inp["target_neutral_roi"],
        inp["expected_neutral_roi"],
        inp["expected_pose_roi"],
        inp["local_edges"],
        inp["local_tris"],
        inp["base_weights_roi"],
        inp["expected_weights_roi"],
        core_local,
        CONFIGS["v091_surface_balanced"],
    )
    summary["base_surface_metrics"] = base_obj.summary(base_states)

    rows = []
    for name, config in CONFIGS.items():
        objective = PatchObjective(
            state_weights,
            state_pos,
            inp["target_neutral_roi"],
            inp["expected_neutral_roi"],
            inp["expected_pose_roi"],
            inp["local_edges"],
            inp["local_tris"],
            inp["base_weights_roi"],
            inp["expected_weights_roi"],
            core_local,
            config,
        )
        initial = np.zeros((len(roi_ids),), dtype=np.int16)
        states, history = _coordinate_descent(objective, initial, int(config["iterations"]))
        full_weights, changed_full = _candidate_full_weights(inp, state_weights, states)
        changed_local = states != 0
        row_l1 = np.abs(full_weights[roi_ids] - inp["base_weights_roi"]).sum(axis=1)
        metrics = objective.summary(states)
        base_metrics = objective.summary(initial)
        outputs["weights__" + name] = full_weights.astype(np.float32)
        outputs["accept__" + name] = changed_full.astype(bool)
        outputs["states__" + name] = states.astype(np.int16)
        outputs["row_l1_roi__" + name] = row_l1.astype(np.float32)

        item = {
            "config": config,
            "history": history,
            "changed_vertices": int(np.count_nonzero(changed_local)),
            "changed_core_vertices": int(np.count_nonzero(changed_local & core_local)),
            "changed_state_counts": {state_names[i]: int(np.count_nonzero(states == i)) for i in range(len(state_names))},
            "base_metrics": base_metrics,
            "candidate_metrics": metrics,
            "objective_delta": float(base_metrics["objective"] - metrics["objective"]),
            "row_l1_changed": _stat(row_l1[changed_local]),
            "row_sum_error_max": float(np.max(np.abs(full_weights.sum(axis=1) - 1.0))),
        }
        summary["configs"][name] = item
        rows.append([
            name,
            item["changed_vertices"],
            item["changed_core_vertices"],
            item["objective_delta"],
            item["candidate_metrics"]["vertex_core"].get("p95", 0.0),
            item["candidate_metrics"]["edge"].get("p95", 0.0),
            item["candidate_metrics"]["area"].get("p95", 0.0),
            item["candidate_metrics"]["normal"].get("p95", 0.0),
            item["row_l1_changed"].get("p95", 0.0),
        ])

    np.savez_compressed(str(OUT_NPZ), **outputs)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["candidate", "changed_vertices", "changed_core_vertices", "objective_delta", "vertex_core_p95", "edge_p95", "area_p95", "normal_p95", "row_l1_p95"])
        writer.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    main()
