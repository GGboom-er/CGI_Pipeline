"""
Sync action 分流契约测试（pairing_groups 版）。

maya_sync_rig_incremental 依赖 maya.cmds 无法在 mayapy 外直接跑，但决策树
的输入（compare 产出的 pairing_groups）和每种 action 的预期处理路径是纯逻辑。

本测试验证：
  1. compare 产出的 4 个 group action，sync 决策树都有明确分支
  2. 老标签（MODIFIED/MERGE/SPLIT/NEW/DELETE 作为 sync 决策分支）已不存在
  3. 死标签（AUTO_SAFE/SPATIAL_VOTING/REVIEW/REORDER/PARTIAL_MATCH）仍被清理
  4. sync 通过 sync_contract 消费 pairing_groups 的核心字段（action/abc_dags/rig_dags/layer_name）
  5. 关键 helper 存在：_relocate_rig_mesh / _check_sync_already_done / _scan_hardcoded_refs
"""
import sys, os, re

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.asset_info_schema import compare, make_empty_info, make_mesh_entry

SYNC_PATH = os.path.join(
    os.path.dirname(__file__), '..',
    'skills', 'maya_sync_rig_incremental', 'maya_sync_rig_incremental.py'
)

GROUP_ACTIONS = {"IDENTICAL", "ORIG_INJECT", "PAIRED", "UNPAIRED"}
DEAD_ACTIONS = {"AUTO_SAFE", "SPATIAL_VOTING", "REVIEW", "REORDER",
                "PARTIAL_MATCH", "SYNC_ORIG"}


def _ok(cond, msg):
    assert cond, msg
    print(f"  [PASS] {msg}")


def _read_sync_source():
    with open(SYNC_PATH, encoding='utf-8') as f:
        return f.read()


def test_sync_consumes_pairing_groups():
    """sync 应消费 pairing_groups 字段（从 compare report 读取），而不是旧 instructions。"""
    print("\n=== Test 1: sync 消费 pairing_groups ===")
    source = _read_sync_source()
    _ok('from skills.maya_sync_rig_incremental.sync_contract import' in source,
        'sync 使用纯契约层')
    _ok('pairing_groups = report["pairing_groups"]' in source,
        'sync 从契约校验后的 report 读取 pairing_groups')
    _ok('target_only_dags = report["target_only_dags"]' in source,
        'sync 从契约校验后的 report 读取 target_only_dags')
    _ok('parse_sync_inputs(payload)' in source,
        'sync 通过契约层解析输入')
    _ok('validate_sync_inputs(sync_inputs)' in source,
        'sync 要求前置 compare_result/source 数据/source_path')
    _ok('instructions = report.get("instructions"' not in source,
        'sync 不再读 instructions 旧字段')


def test_sync_handles_all_group_actions():
    """pairing_groups 的 4 个 action，sync 决策树要都有显式分支。"""
    print("\n=== Test 2: sync 覆盖 4 个 group action ===")
    source = _read_sync_source()
    for a in GROUP_ACTIONS:
        # action in ("IDENTICAL", "ORIG_INJECT") 或 action == "PAIRED" 都算
        pattern_eq = rf'action\s*==\s*["\']{a}["\']'
        pattern_in = rf'action\s+in\s*\([^)]*["\']{a}["\']'
        found = re.search(pattern_eq, source) or re.search(pattern_in, source)
        _ok(found is not None, f'sync 有 {a} 显式分支')


def test_no_dead_labels_in_sync():
    """P0-A 老标签仍不应出现。"""
    print("\n=== Test 3: sync 清理老标签 ===")
    source = _read_sync_source()
    for a in DEAD_ACTIONS:
        _ok(a not in source, f'sync 不再引用老标签 {a}')


def test_no_legacy_action_branches():
    """旧的 MODIFIED/MERGE/SPLIT/NEW/DELETE 不应再作为 sync 决策分支。

    这些标签只在 compare 的 paired[i].actionability 里（审计用途），
    sync 决策树应只消费 pairing_groups 的 4 个 action。
    """
    print("\n=== Test 4: 旧 7 标签不再作 sync 决策分支 ===")
    source = _read_sync_source()
    legacy = ["MODIFIED", "MERGE", "SPLIT", "NEW", "DELETE"]
    for a in legacy:
        _ok(not re.search(rf'action\s*==\s*["\']{a}["\']', source),
            f'sync 无 action == "{a}" 分支')
        _ok(not re.search(rf'action\s+in\s*\([^)]*["\']{a}["\']', source),
            f'sync 无 action in (... "{a}" ...) 分支')


def test_paired_group_contract():
    """PAIRED 组的契约：M→N 里 abc_dags 和 rig_dags 都非空。"""
    print("\n=== Test 5: PAIRED 组契约 ===")
    import numpy as np
    np.random.seed(123)
    rig1 = np.random.rand(20, 3) * 0.1
    rig2 = np.random.rand(20, 3) * 0.1 + 10.0
    tex = np.vstack([rig1, rig2])

    info_tex = make_empty_info()
    info_tex["meshes"]["combined"] = make_mesh_entry(40, tex.flatten().tolist())
    info_rig = make_empty_info()
    info_rig["meshes"]["part1"] = make_mesh_entry(20, rig1.flatten().tolist())
    info_rig["meshes"]["part2"] = make_mesh_entry(20, rig2.flatten().tolist())

    result = compare(info_tex, info_rig)
    paired_groups = [g for g in result["pairing_groups"] if g["action"] == "PAIRED"]
    _ok(len(paired_groups) > 0, 'compare 产出 PAIRED 组')
    for g in paired_groups:
        _ok(len(g["abc_dags"]) >= 1, 'PAIRED 组 abc_dags 非空')
        _ok(len(g["rig_dags"]) >= 1, 'PAIRED 组 rig_dags 非空')
        _ok(g["layer_name"], 'PAIRED 组 layer_name 非空')


def test_unpaired_group_contract():
    """UNPAIRED 组的契约：rig_dags 为空、layer_name 为空（进 _source_only 大 layer）。"""
    print("\n=== Test 6: UNPAIRED 组契约 ===")
    import numpy as np
    np.random.seed(200)
    pts = np.random.rand(10, 3) + 100.0

    info_a = make_empty_info()
    info_a["meshes"]["newprop"] = make_mesh_entry(10, pts.flatten().tolist())
    info_b = make_empty_info()
    # rig 侧和 abc 完全不同位置，避免空间配对
    info_b["meshes"]["old"] = make_mesh_entry(10, (pts - 500).flatten().tolist())

    result = compare(info_a, info_b)
    unpaired = [g for g in result["pairing_groups"] if g["action"] == "UNPAIRED"]
    _ok(len(unpaired) > 0, 'compare 产出 UNPAIRED 组')
    for g in unpaired:
        _ok(g["rig_dags"] == [], 'UNPAIRED 组 rig_dags 为空')
        _ok(g["layer_name"] == "", 'UNPAIRED 组 layer_name 为空')


def test_sync_layer_helper_exists():
    """sync 要有关键 helper：搬运、幂等检测、硬编码扫描。"""
    print("\n=== Test 7: 关键 helper 存在 ===")
    source = _read_sync_source()
    _ok('def _relocate_rig_mesh(' in source,
        'sync 有 _relocate_rig_mesh（IDENTICAL/ORIG_INJECT 搬运路径）')
    _ok('def _ensure_collectable_shape_orig(' in source,
        'sync 有新建 mesh ShapeOrig 可采集性补齐')
    _ok('def _safe_maya_node_name(' in source,
        'sync 有 displayLayer 名称合法化 helper')
    _ok('_safe_maya_node_name(layer_name' in source,
        'sync 建 layer 前先合法化 layer_name')
    _ok('_ensure_collectable_shape_orig(node)' in source,
        'sync 会对新建 mesh 执行 ShapeOrig 可采集性补齐')
    _ok('def _check_sync_already_done(' in source,
        'sync 有 _check_sync_already_done（幂等保护）')
    _ok('def _scan_hardcoded_refs(' in source,
        'sync 有 _scan_hardcoded_refs（硬编码路径扫描）')
    _ok('def _mark_sync_done(' in source,
        'sync 有 _mark_sync_done（打标记防重跑）')


def test_sync_layer_organization():
    """sync 应按 pairing_groups 组织 layer，不再是老的 SYNC_layer / FAST_PATH_layer / NEW_layer。"""
    print("\n=== Test 8: layer 组织按 pairing_groups ===")
    source = _read_sync_source()
    # 老 layer 名字不再硬编码
    for legacy_layer in ("SYNC_layer", "FAST_PATH_layer", "NEW_layer", "BACKUP_layer"):
        _ok(legacy_layer not in source, f'sync 不再建老 layer {legacy_layer}')
    # 新 layer 系统
    _ok('_source_only' in source, 'sync 有 _source_only 大 layer')
    _ok('_target_only' in source, 'sync 有 _target_only 大 layer')
    _ok('group_records' in source, 'sync 维护 group_records 结构')


def test_sync_failure_boundaries():
    """入口/契约/执行阶段应有可定位的失败边界。"""
    print("\n=== Test 9: 失败边界 ===")
    source = _read_sync_source()
    for phase in ("input_contract", "input_files", "compare_result_contract",
                  "source_load", "target_collect", "compare_target_match", "execute"):
        _ok(phase in source, f'sync 有 {phase} 失败阶段')
    _ok('未识别 action=' not in source, 'sync 不再把非法 action 静默降级')
    _ok('format_action_summary(action_counts)' in source, 'sync 成功摘要来自统一统计函数')


if __name__ == "__main__":
    test_sync_consumes_pairing_groups()
    test_sync_handles_all_group_actions()
    test_no_dead_labels_in_sync()
    test_no_legacy_action_branches()
    test_paired_group_contract()
    test_unpaired_group_contract()
    test_sync_layer_helper_exists()
    test_sync_layer_organization()
    test_sync_failure_boundaries()
    print("\nALL SYNC DISPATCH TESTS PASSED!")
