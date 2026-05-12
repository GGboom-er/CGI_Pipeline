"""
点序打乱 / 姿势偏移的 compare 行为测试。

P0-A 时代这里测 `REORDER` 与 `CPD` 的独立动作级别；当前 compare 实现：
- Step 1/2 使用 index-to-index 距离判定，点序打乱会让同序号距离变大，直接降级。
- 降级后走 Step 3 最近邻匹配，点数一致但点序乱 → 命中率接近 100% → MODIFIED。
- 点数不同 / 分布变动 → 同样走 Step 3，按 match_pct_loose 判定 MODIFIED / MERGE / SPLIT。

也就是说"点序打乱"在当前算法里**不**单独成一个动作级别，它就是 MODIFIED 的一个子情形。
"""
import sys
import os
import numpy as np
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.asset_info_schema import compare, make_empty_info, make_mesh_entry
from core.non_rigid_registration import align_pose_non_rigid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TEST")


def test_point_reorder_downgrades_to_step3():
    logger.info("=== 测试：点数一致 + 点序打乱 → 降级 Step 3 / MODIFIED ===")

    np.random.seed(2)
    pts_rig = np.random.rand(10, 3)
    indices = np.arange(10)
    np.random.shuffle(indices)
    pts_asset = pts_rig[indices]

    info_rig = make_empty_info()
    info_rig["meshes"]["mesh_A"] = make_mesh_entry(10, pts_rig.flatten().tolist())

    info_asset = make_empty_info()
    info_asset["meshes"]["mesh_A"] = make_mesh_entry(10, pts_asset.flatten().tolist())

    result = compare(info_asset, info_rig)
    assert len(result["paired"]) == 1
    paired = result["paired"][0]
    action = paired["actionability"]
    step = paired.get("step")

    logger.info(f"动作级别: {action}, step: {step}")
    # 点序乱 → Step 1/2 index 距离失败 → Step 3 最近邻 → MODIFIED
    assert action == "MODIFIED", f"期望 MODIFIED，实际 {action}"
    assert step == 3, f"期望 step=3，实际 {step}"
    logger.info("✅ 点序打乱降级 Step 3 MODIFIED 通过\n")


def test_cpd_pose_correction():
    logger.info("=== 测试：非刚性配准（Pose 纠正）===")

    angles = np.linspace(0, 2*np.pi, 20, endpoint=False)
    pts_rig = np.stack([np.cos(angles), np.sin(angles), np.zeros(20)], axis=1)

    pts_asset = pts_rig.copy()
    pts_asset[:, 0] += 0.05
    pts_asset[:, 1] *= 1.02

    err_before = np.max(np.linalg.norm(pts_rig - pts_asset, axis=1))
    logger.info(f"配准前最大几何误差: {err_before:.4f}")

    pts_rig_aligned = align_pose_non_rigid(pts_rig, pts_asset, max_iterations=30)

    err_after = np.max(np.linalg.norm(pts_rig_aligned - pts_asset, axis=1))
    logger.info(f"配准后最大几何误差: {err_after:.4f}")

    assert err_after < err_before
    assert err_after < 0.02
    logger.info("✅ CPD 姿势纠正通过\n")


def test_cpd_different_counts():
    logger.info("=== 测试：CPD 对不同点数 + 大幅变形 ===")

    angles = np.linspace(0, 2*np.pi, 20, endpoint=False)
    pts_rig = np.stack([np.cos(angles), np.sin(angles), np.zeros(20)], axis=1)

    angles_new = np.linspace(0, 2*np.pi, 15, endpoint=False)
    pts_asset = np.stack([np.cos(angles_new), np.sin(angles_new), np.zeros(15)], axis=1)

    pts_asset[:, 0] += 0.5
    pts_asset[:, 1] *= 1.5

    pts_rig_aligned = align_pose_non_rigid(pts_rig, pts_asset, max_iterations=50)

    from scipy.spatial import cKDTree
    tree = cKDTree(pts_asset)
    dist_before, _ = tree.query(pts_rig)
    dist_after, _ = tree.query(pts_rig_aligned)

    logger.info(f"配准前平均偏离: {np.mean(dist_before):.4f}")
    logger.info(f"配准后平均偏离: {np.mean(dist_after):.4f}")

    assert np.mean(dist_after) < np.mean(dist_before)
    assert np.mean(dist_after) < 0.15
    logger.info("✅ CPD 大变形 + 不同点数通过\n")


if __name__ == "__main__":
    test_point_reorder_downgrades_to_step3()
    test_cpd_pose_correction()
    test_cpd_different_counts()
    print("ALL TESTS PASSED!")
