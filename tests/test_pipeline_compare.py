"""
compare 引擎的集成行为测试：
- CPD 开关对"大幅姿势偏移"场景的影响
- 点数不同时的 MODIFIED 判定（P0-A 时代叫 PARTIAL_MATCH，现已拆成 MODIFIED/MERGE/SPLIT）
"""
import sys
import os
import numpy as np
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.asset_info_schema import compare, make_empty_info, make_mesh_entry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TEST_INTEGRATION")


def test_compare_with_cpd_integration():
    logger.info("=== 测试：CPD 开关对大幅姿势偏移的影响 ===")

    np.random.seed(0)
    pts_rig = np.random.rand(50, 3)

    # 故意严重形变：X 平移 5cm + Y 拉伸 2 倍
    pts_asset = pts_rig.copy()
    pts_asset[:, 0] += 5.0
    pts_asset[:, 1] *= 2.0

    info_rig = make_empty_info()
    info_rig["meshes"]["body"] = make_mesh_entry(50, pts_rig.flatten().tolist())
    info_asset = make_empty_info()
    info_asset["meshes"]["body"] = make_mesh_entry(50, pts_asset.flatten().tolist())

    # A: 不开 CPD
    logger.info(">>> A: enable_cpd=False（传统盲投射）")
    result_no_cpd = compare(info_asset, info_rig, enable_cpd=False)
    # 5cm 平移远超 loose_thr，几何上完全不吻合：
    # 预期走到 Step 3 的局部匹配也会被 20% 阈值拒绝，最终 a 落入 only_a，b 落入 only_b
    assert len(result_no_cpd["paired"]) == 0, "没有 CPD 时，竟然配对成功了？"
    assert len(result_no_cpd["only_a"]) > 0, "Asset 应该被判无配对"
    assert len(result_no_cpd["only_b"]) > 0, "Rig 应该被判无配对"
    logger.info("  预期：only_a + only_b，无配对 ✓")

    # B: 开 CPD
    logger.info("\n>>> B: enable_cpd=True（姿势拦截器）")
    info_rig = make_empty_info()
    info_rig["meshes"]["body"] = make_mesh_entry(50, pts_rig.flatten().tolist())
    result_cpd = compare(info_asset, info_rig, enable_cpd=True)

    assert len(result_cpd["paired"]) == 1, "CPD 后应配对成功"
    paired = result_cpd["paired"][0]
    action = paired["actionability"]
    offset = paired.get("max_offset", 999)
    logger.info(f"  动作级别: {action}, 压平后 max_offset: {offset}")
    # CPD 压平姿势后，compare 能在某一层匹配到：
    # - 若压平到 < precision_loose，走 Step 1/2 → IDENTICAL / ORIG_INJECT
    # - 若仍有小幅残差，走 Step 3 最近邻 → MODIFIED
    # 只要不再是 only_a + only_b 即算通过
    assert action in ("IDENTICAL", "ORIG_INJECT", "MODIFIED"), f"CPD 后应有配对，实际 {action}"
    assert offset < 0.5, f"CPD 后 max_offset={offset}，理应远小于原 5cm 偏移"
    logger.info("✅ CPD 集成测试通过\n")


def test_partial_match_integration():
    logger.info("=== 测试：点数不同的局部匹配（P0-A 老 PARTIAL_MATCH → 新 MODIFIED）===")

    np.random.seed(1)
    pts_rig = np.random.rand(20, 3)

    # 新 Asset 25 个点：前 15 个与 rig 前 15 个重合，后 10 个是新增拓扑（放远）
    pts_asset = np.zeros((25, 3))
    pts_asset[:15] = pts_rig[:15]
    pts_asset[15:] = np.random.rand(10, 3) + 10.0

    info_rig = make_empty_info()
    info_rig["meshes"]["body"] = make_mesh_entry(20, pts_rig.flatten().tolist())

    info_asset = make_empty_info()
    info_asset["meshes"]["body"] = make_mesh_entry(25, pts_asset.flatten().tolist())

    result = compare(info_asset, info_rig, enable_cpd=False)
    assert len(result["paired"]) == 1, "应有一对配对（MODIFIED）"
    paired = result["paired"][0]
    action = paired["actionability"]

    logger.info(f"  动作级别: {action}, step: {paired.get('step')}")
    # 点数不一致，走 Step 3；单对单命中，15/20=75% 高于 modified_min → MODIFIED
    assert action == "MODIFIED", f"期望 MODIFIED，实际 {action}"
    assert paired.get("step") == 3, "应从 Step 3 产出"
    logger.info("✅ 局部匹配 → MODIFIED 通过\n")


if __name__ == "__main__":
    test_compare_with_cpd_integration()
    test_partial_match_integration()
    print("ALL TESTS PASSED!")
