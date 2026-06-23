import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / "workflows" / "tex_to_rig_verify_and_sync.json"
CHECK_SKILL = ROOT / "skills" / "maya_check_asset_hierarchy" / "maya_check_asset_hierarchy.py"
FIX_SKILL = ROOT / "skills" / "maya_fix_asset_hierarchy" / "maya_fix_asset_hierarchy.py"
SYNC_SKILL = ROOT / "skills" / "maya_sync_rig_incremental" / "maya_sync_rig_incremental.py"


def _ok(condition, message):
    assert condition, message
    print(f"  [PASS] {message}")


def test_workflow_presync_order():
    print("\n=== Test 1: workflow 预同步层级顺序 ===")
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    steps = wf["steps"]
    order = {step["step_id"]: index for index, step in enumerate(steps)}
    _ok("check_hierarchy_ready" not in order,
        "不再保留拼装前第二次层级检查")
    _ok(order["check_hierarchy_pre"] < order["fix_hierarchy_pre"] < order["compare_pre"] < order["sync"] < order["check_hierarchy_post"],
        "层级流程为 check/fix/compare/sync/check")

    check_pre = steps[order["check_hierarchy_pre"]]
    _ok(check_pre.get("source_path") == "{{outputs.resolve_files.result.rig_path}}",
        "Maya 段第一步负责打开 rig 沙盒副本")
    _ok(check_pre["parameters"].get("phase") == "pre_sync",
        "预检查使用 pre_sync 阶段")
    _ok(check_pre["parameters"].get("block_extra_top_nodes") is False,
        "预检查不把额外顶层节点作为阻断项")

    fix_pre = steps[order["fix_hierarchy_pre"]]
    _ok(fix_pre["parameters"].get("delete_extra_top_nodes") is True,
        "fix 会删除 check 判定为 safe 的顶层残渣")

    check_post = steps[order["check_hierarchy_post"]]
    _ok(check_post["parameters"].get("phase") == "post_sync",
        "拼装后检查使用 post_sync 阶段")
    _ok(check_post["parameters"].get("block_extra_top_nodes") is False,
        "拼装后检查仍只报告额外顶层节点")

    compare_pre = steps[order["compare_pre"]]
    sync = steps[order["sync"]]
    expected = "{{outputs.fix_hierarchy_pre.result.active_rig_root}};{{config.stages.rig.geom_roots.0}}"
    _ok(compare_pre["parameters"].get("cache_group") == expected,
        "compare_pre 使用 fix 输出的 active_rig_root")
    _ok(sync["parameters"].get("cache_group") == expected,
        "sync 使用同一 active_rig_root，避免 compare_result 路径漂移")


def test_check_outputs_presync_facts():
    print("\n=== Test 2: check 输出预同步事实 ===")
    source = CHECK_SKILL.read_text(encoding="utf-8")
    for token in (
        "legacy_geo_roots",
        "active_rig_root",
        "active_rig_mesh_count",
        "safe_delete_top_nodes",
        "manual_review_top_nodes",
        "block_extra_top_nodes",
        "displayPoints",
        'phase == "pre_sync"',
    ):
        _ok(token in source, f"check 输出/使用 {token}")


def test_fix_protects_binding_top_nodes():
    print("\n=== Test 3: fix 保护绑定顶层 ===")
    source = FIX_SKILL.read_text(encoding="utf-8")
    for token in (
        "_normalize_legacy_geo_roots",
        "_rename_top_to_group_if_needed",
        '"RIG_geo"',
        "manual_review_top_nodes",
        "safe_delete_top_nodes",
    ):
        _ok(token in source, f"fix 包含 {token}")
    _ok('_as_bool(params.get("delete_extra_top_nodes"), False)' in source,
        "默认不删除顶层节点")
    _ok("normalized_roots, normalized_created, renamed_tops, preserved_tops = _normalize_legacy_geo_roots" in source,
        "fix 先归一 legacy geo，再补标准 cache 容器")
    _ok("check_result.get(\"extra_top_nodes\") or []" not in source,
        "fix 不再按 extra_top_nodes 执行删除")


def test_sync_prefers_top_group_geometry():
    print("\n=== Test 4: sync 创建标准 cache 位置 ===")
    source = SYNC_SKILL.read_text(encoding="utf-8")
    _ok('cmds.objExists("|Group")' in source,
        "sync 优先使用顶层 |Group")
    _ok('cmds.ls("|Group|Geometry"' in source,
        "sync 优先使用 |Group|Geometry 创建 cache")
    _ok('cmds.ls("Geometry", long=True' not in source,
        "sync 不再随便选择任意 Geometry 节点")


if __name__ == "__main__":
    test_workflow_presync_order()
    test_check_outputs_presync_facts()
    test_fix_protects_binding_top_nodes()
    test_sync_prefers_top_group_geometry()
    print("\nALL HIERARCHY PRESYNC CONTRACT TESTS PASSED!")
