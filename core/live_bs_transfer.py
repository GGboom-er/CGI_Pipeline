"""Live BlendShape 迁移的纯数据工具。"""

from __future__ import annotations

import numpy as np


def compose_live_target_weights(
    base_joints: list[str],
    base_weights: np.ndarray,
    live_joints: list[str],
    live_weights: np.ndarray,
    active_mask: np.ndarray,
) -> tuple[list[str], np.ndarray]:
    """合成 live target 蒙皮权重。

    非 active 区域保留新资产 body 权重；active 区域先清零，再写源 live target 权重。
    这能避免 live face 区域同时保留 50% body 权重和 50% live 权重。
    """
    base_weights = np.asarray(base_weights, dtype=np.float64)
    live_weights = np.asarray(live_weights, dtype=np.float64)
    active_mask = np.asarray(active_mask, dtype=bool)

    if base_weights.ndim != 2 or live_weights.ndim != 2:
        raise ValueError("base_weights/live_weights 必须是二维矩阵")
    if base_weights.shape[0] != live_weights.shape[0]:
        raise ValueError("base_weights 与 live_weights 顶点数必须一致")
    if active_mask.shape[0] != base_weights.shape[0]:
        raise ValueError("active_mask 顶点数必须与权重矩阵一致")
    if base_weights.shape[1] != len(base_joints):
        raise ValueError("base_joints 数量必须匹配 base_weights 列数")
    if live_weights.shape[1] != len(live_joints):
        raise ValueError("live_joints 数量必须匹配 live_weights 列数")

    union_joints: list[str] = []
    for joint in list(base_joints) + list(live_joints):
        if joint not in union_joints:
            union_joints.append(joint)

    out = np.zeros((base_weights.shape[0], len(union_joints)), dtype=np.float64)
    union_index = {joint: idx for idx, joint in enumerate(union_joints)}

    for src_idx, joint in enumerate(base_joints):
        out[:, union_index[joint]] = base_weights[:, src_idx]

    out[active_mask, :] = 0.0
    for src_idx, joint in enumerate(live_joints):
        out[active_mask, union_index[joint]] = live_weights[active_mask, src_idx]

    row_sum = out.sum(axis=1, keepdims=True)
    zero_rows = row_sum[:, 0] <= 1e-12
    row_sum[zero_rows] = 1.0
    out = out / row_sum
    return union_joints, out
