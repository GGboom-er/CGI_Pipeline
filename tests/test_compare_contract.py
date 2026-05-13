"""
compare() 返回结构契约测试。

验证 core.asset_info_schema.compare() 返回字典符合 CompareResult TypedDict
定义——顶层字段齐全，pairing_groups 语义正确。

覆盖：
  1. 正常对比 → pairing_groups + target_only_dags + 审计字段完整
  2. check_positions=False → early-return，pairing_groups 仍有（所有 abc 进 UNPAIRED）
  3. meshes_b 为空 → early-return
  4. 自比 → IDENTICAL 组，rig_dags 单元素
  5. MERGE 连通分量 → 多 rig 归一组，target_only 排除已消费
  6. 1→1 IDENTICAL / PAIRED action 语义
  7. UNPAIRED 组 layer_name 为空
"""
import sys, os
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.asset_info_schema import compare, make_empty_info, make_mesh_entry, _make_layer_name


TOP_REQUIRED = {
    "total_issues", "label_a", "label_b", "source_a", "source_b",
    "paired", "only_a", "only_b", "hierarchy", "textures",
    "merge_groups", "split_groups", "pairing_groups", "target_only_dags",
}

GROUP_REQUIRED = {"group_id", "action", "abc_dags", "rig_dags", "layer_name", "reason"}
VALID_ACTIONS = {"IDENTICAL", "ORIG_INJECT", "PAIRED", "UNPAIRED"}


def _ok(cond, msg):
    assert cond, msg
    print(f"  [PASS] {msg}")


def test_full_contract_normal():
    print("\n=== Test 1: 正常对比契约 ===")
    np.random.seed(100)
    pts = np.random.rand(30, 3)

    info_a = make_empty_info()
    info_a["meshes"]["body"] = make_mesh_entry(30, pts.flatten().tolist())
    info_b = make_empty_info()
    info_b["meshes"]["body"] = make_mesh_entry(30, pts.flatten().tolist())

    result = compare(info_a, info_b, label_a="tex", label_b="rig")

    missing = TOP_REQUIRED - set(result.keys())
    _ok(not missing, f"顶层字段齐全（缺：{missing if missing else '无'}）")
    _ok(result["label_a"] == "tex" and result["label_b"] == "rig", "label 透传")
    _ok("instructions" not in result, "旧 instructions 字段已移除")

    _ok(len(result["pairing_groups"]) == 1, "一对一应产出 1 个组")
    g = result["pairing_groups"][0]
    missing_g = GROUP_REQUIRED - set(g.keys())
    _ok(not missing_g, f"PairingGroup 字段齐全（缺：{missing_g if missing_g else '无'}）")
    _ok(g["action"] in VALID_ACTIONS, f"action 合法: {g['action']}")
    _ok(g["action"] == "IDENTICAL", "几何全等应为 IDENTICAL")
    _ok(g["abc_dags"] == ["body"] and g["rig_dags"] == ["body"], "组内 abc/rig 正确")
    _ok(g["layer_name"] == "body", "layer_name 取 abc 短名")
    _ok(g["group_id"].startswith("g"), "group_id 以 g 开头")


def test_early_return_check_positions_false():
    print("\n=== Test 2: early-return (check_positions=False) ===")
    info_a = make_empty_info()
    info_a["meshes"]["m1"] = make_mesh_entry(3, [0.0]*9)
    info_b = make_empty_info()
    info_b["meshes"]["m2"] = make_mesh_entry(3, [1.0]*9)

    result = compare(info_a, info_b, check_positions=False)
    _ok("pairing_groups" in result, "early-return 也产出 pairing_groups")
    _ok("target_only_dags" in result, "early-return 也产出 target_only_dags")
    # 所有 abc 落 UNPAIRED，所有 rig 落 target_only
    groups = result["pairing_groups"]
    _ok(len(groups) == 1 and groups[0]["action"] == "UNPAIRED",
        "early-return: abc 侧 mesh 归 UNPAIRED")
    _ok(result["target_only_dags"] == ["m2"], "early-return: rig 侧 mesh 归 target_only")


def test_early_return_empty_meshes():
    print("\n=== Test 3: early-return (meshes_b 为空) ===")
    info_a = make_empty_info()
    info_a["meshes"]["m1"] = make_mesh_entry(3, [0.0]*9)
    info_b = make_empty_info()

    result = compare(info_a, info_b)
    _ok(len(result["pairing_groups"]) == 1, "meshes_b 为空：abc 仍产出 UNPAIRED 组")
    _ok(result["pairing_groups"][0]["action"] == "UNPAIRED", "action=UNPAIRED")
    _ok(result["target_only_dags"] == [], "target_only 为空")


def test_self_compare_identical():
    print("\n=== Test 4: 自比 IDENTICAL ===")
    np.random.seed(200)
    pts = np.random.rand(15, 3)
    info = make_empty_info()
    info["meshes"]["body"] = make_mesh_entry(15, pts.flatten().tolist())

    result = compare(info, info)
    _ok(len(result["pairing_groups"]) == 1, "自比一组")
    g = result["pairing_groups"][0]
    _ok(g["action"] == "IDENTICAL", "自比 action=IDENTICAL")
    _ok(len(g["rig_dags"]) == 1 and len(g["abc_dags"]) == 1, "1→1 单元素")


def test_merge_connected_component():
    print("\n=== Test 5: MERGE 连通分量 ===")
    np.random.seed(300)
    rig1_pts = np.random.rand(20, 3) * 0.1
    rig2_pts = np.random.rand(20, 3) * 0.1 + 10.0
    tex_pts = np.vstack([rig1_pts, rig2_pts])

    info_tex = make_empty_info()
    info_tex["meshes"]["combined"] = make_mesh_entry(40, tex_pts.flatten().tolist())
    info_rig = make_empty_info()
    info_rig["meshes"]["part1"] = make_mesh_entry(20, rig1_pts.flatten().tolist())
    info_rig["meshes"]["part2"] = make_mesh_entry(20, rig2_pts.flatten().tolist())

    result = compare(info_tex, info_rig)
    paired_groups = [g for g in result["pairing_groups"] if g["action"] == "PAIRED"]
    _ok(len(paired_groups) == 1, "应合并成单个 PAIRED 连通分量")
    g = paired_groups[0]
    _ok(set(g["abc_dags"]) == {"combined"}, "abc_dags 含 combined")
    _ok(set(g["rig_dags"]) == {"part1", "part2"}, "rig_dags 含 part1+part2")
    _ok(g["layer_name"] == "combined_Layer", "PAIRED 单 source 也使用 资产名_Layer")
    _ok(result["target_only_dags"] == [], "part1/part2 归入组，target_only 为空")


def test_unpaired_and_target_only():
    print("\n=== Test 6: UNPAIRED + target_only ===")
    np.random.seed(400)
    tex_only = np.random.rand(10, 3)
    rig_only = np.random.rand(10, 3) + 100.0

    info_a = make_empty_info()
    info_a["meshes"]["newprop"] = make_mesh_entry(10, tex_only.flatten().tolist())
    info_b = make_empty_info()
    info_b["meshes"]["oldhair"] = make_mesh_entry(10, rig_only.flatten().tolist())

    result = compare(info_a, info_b)
    unpaired = [g for g in result["pairing_groups"] if g["action"] == "UNPAIRED"]
    _ok(len(unpaired) == 1, "应有一个 UNPAIRED 组")
    g = unpaired[0]
    _ok(g["abc_dags"] == ["newprop"], "UNPAIRED 含 newprop")
    _ok(g["rig_dags"] == [], "UNPAIRED 无 rig 源")
    _ok(g["layer_name"] == "", "UNPAIRED layer_name 为空（进 _source_only 大 layer）")
    _ok(result["target_only_dags"] == ["oldhair"], "rig 独有进 target_only")


def test_group_ids_unique():
    print("\n=== Test 7: group_id 唯一 ===")
    np.random.seed(500)
    info_a = make_empty_info()
    info_b = make_empty_info()
    for i in range(5):
        pts = np.random.rand(10, 3) + i * 10
        info_a["meshes"][f"m{i}"] = make_mesh_entry(10, pts.flatten().tolist())
        info_b["meshes"][f"m{i}"] = make_mesh_entry(10, pts.flatten().tolist())

    result = compare(info_a, info_b)
    ids = [g["group_id"] for g in result["pairing_groups"]]
    _ok(len(ids) == len(set(ids)), f"group_id 全唯一（{len(ids)} 个）")


def test_multi_source_layer_name_readable_and_safe():
    print("\n=== Test 8: 多源 PAIRED layer 命名 ===")
    single_layer = _make_layer_name([
        "ABC|Group|cache|body|cdfBaiXingG_body1|cdfBaiXingG_body1Shape"
    ])
    _ok(single_layer == "cdfBaiXingG_body1_Layer",
        "单 source PAIRED layer 使用资产 transform + _Layer，避免撞 mesh 名")

    dags = [
        "ABC|Group|cache|cloth|cdfBaiXingG_Accessories1|cdfBaiXingG_Accessories1Shape",
        "ABC|Group|cache|cloth|cdfBaiXingG_Accessories2|cdfBaiXingG_Accessories2Shape",
    ]

    layer_name = _make_layer_name(dags)
    _ok(layer_name == "cdfBaiXingG_Accessories1_cdfBaiXingG_Accessories2_Layer",
        "多个 source mesh 以资产 transform 名拼 layer")
    _ok("+more" not in layer_name, "多源 layer 不退化为 +Nmore")
    _ok(all(ch.isalnum() or ch == "_" for ch in layer_name), "layer 名只含 Maya 安全字符")

    long_dags = [
        f"ABC|Group|cache|grp|VeryLongAccessoryNameForLayerBudget{i}|VeryLongAccessoryNameForLayerBudget{i}Shape"
        for i in range(8)
    ]
    fallback = _make_layer_name(long_dags)
    _ok(fallback.startswith("VeryLongAccessoryNameForLayerBudget0"), "过长时保留首个资产名")
    _ok(fallback.endswith("_GRP_Layer"), "过长时退化为 A_GRP_Layer")
    _ok("+more" not in fallback, "过长 fallback 不生成非法 +more 名")


if __name__ == "__main__":
    test_full_contract_normal()
    test_early_return_check_positions_false()
    test_early_return_empty_meshes()
    test_self_compare_identical()
    test_merge_connected_component()
    test_unpaired_and_target_only()
    test_group_ids_unique()
    test_multi_source_layer_name_readable_and_safe()
    print("\nALL CONTRACT TESTS PASSED!")
