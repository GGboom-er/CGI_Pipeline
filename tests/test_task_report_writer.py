# -*- coding: utf-8 -*-
# tests/test_task_report_writer.py
# 验证运行时 REPORT.md 的 block upsert 与 receipt 渲染。

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import task_report_writer as writer


def _sandbox(name: str) -> Path:
    path = ROOT / "projects" / "_test_report_writer" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_step_upsert_no_duplicate():
    sbx = _sandbox("upsert")
    report = sbx / "REPORT.md"
    ctx = {
        "task_id": "task-upsert",
        "asset_name": "demo",
        "project": "ysj",
        "run_dir": str(sbx),
        "source_path": str(sbx / "demo.ma"),
    }
    writer.init_report(report, ctx)

    step_ctx = {
        "step_index": 0,
        "step_total": 1,
        "skill_id": "pipeline_compare_asset",
        "parameters": {"input_source": "a.abc", "input_target": "b.ma"},
        "source_path": str(sbx / "demo.ma"),
    }
    writer.upsert_step_started(report, step_ctx)
    writer.upsert_step_finished(report, step_ctx, {
        "skill_id": "pipeline_compare_asset",
        "status": "SUCCESS",
        "elapsed_min": 0.1,
        "summary": {"input": "a vs b", "action": "对比通过", "output_count": 0, "output_label": "差异"},
        "items": [],
        "outputs": {"output_path": str(sbx / ".info" / "compare_result.json")},
    })

    text = report.read_text(encoding="utf-8")
    assert text.count("[//]: # (report:block:start step:main:0:pipeline_compare_asset)") == 1, text
    assert "<!-- report:block:start" not in text
    step_block = text.split("report:block:start step:main:0:pipeline_compare_asset", 1)[1]
    step_block = step_block.split("report:block:end step:main:0:pipeline_compare_asset", 1)[0]
    assert "RUNNING" not in step_block, text
    assert "SUCCESS" in text
    assert "## Step 1/1 | pipeline_compare_geometry_sources | SUCCESS" in text
    assert "**Input**" in text
    assert "**Output**" in text
    assert "compare_result.json" in text
    print(f"✓ test_step_upsert_no_duplicate → {report}")


def test_error_block_contains_full_detail():
    sbx = _sandbox("error")
    report = sbx / "REPORT.md"
    writer.init_report(report, {"task_id": "task-error", "asset_name": "demo", "run_dir": str(sbx)})
    step_ctx = {"step_index": 1, "step_total": 2, "skill_id": "maya_sync_rig_incremental", "parameters": {}}
    writer.upsert_step_started(report, step_ctx)
    writer.upsert_step_finished(report, step_ctx, {
        "skill_id": "maya_sync_rig_incremental",
        "status": "ERROR",
        "elapsed_min": 0.02,
        "summary": {"action": "执行异常"},
        "items": [],
        "outputs": {},
        "error": "绑定目标缺少 ShapeOrig",
        "traceback": "Traceback line 1\nTraceback line 2",
        "recovery_hint": "先运行命名修复或检查绑定源。",
    }, worker_status="ERROR", raw_detail="raw failure")
    writer.finalize_report(report, {"task_id": "task-error", "asset_name": "demo", "run_dir": str(sbx)}, "ERROR", 0.02)

    text = report.read_text(encoding="utf-8")
    assert "**Error Detail**" in text
    assert "绑定目标缺少 ShapeOrig" in text
    assert "Traceback line 2" in text
    assert "先运行命名修复" not in text
    print(f"✓ test_error_block_contains_full_detail → {report}")


def test_sections_and_report_content():
    sbx = _sandbox("sections")
    report = sbx / "REPORT.md"
    writer.init_report(report, {"task_id": "task-sections", "asset_name": "demo", "run_dir": str(sbx)})
    step_ctx = {"step_index": 0, "step_total": 1, "skill_id": "maya_compare_asset_in_scene", "parameters": {}}
    writer.upsert_step_finished(report, step_ctx, {
        "skill_id": "maya_compare_asset_in_scene",
        "status": "SUCCESS",
        "elapsed_min": 0.03,
        "summary": {"action": "配对 73, 差异 0"},
        "items": [],
        "outputs": {},
        "report_sections": [
            {"title": "通过配对", "summary": "73 项", "items": [{"name": "bodyShape", "detail": "一致"}]},
            {"title": "几何差异", "summary": "0 项", "items": []},
        ],
        "report_content": "旧格式完整对比报告",
    })
    text = report.read_text(encoding="utf-8")
    assert "**Details**" in text
    assert "<summary>通过配对 (73 项)</summary>" in text
    assert "bodyShape" in text
    assert "<summary>几何差异 (0 项)</summary>" in text
    assert "旧格式完整对比报告" not in text
    print(f"✓ test_sections_and_report_content → {report}")


def test_compare_result_renders_action_details_without_json_dump():
    sbx = _sandbox("compare_details")
    report = sbx / "REPORT.md"
    writer.init_report(report, {"task_id": "task-compare-details", "asset_name": "demo", "run_dir": str(sbx)})
    step_ctx = {"step_index": 4, "step_total": 8, "skill_id": "maya_compare_asset_in_scene", "parameters": {}}
    compare_result = {
        "schema_version": "compare_result.v1",
        "compare": {
            "pairing_groups": [
                {
                    "group_id": "g0001",
                    "action": "ORIG_INJECT",
                    "abc_dags": ["ABC|Group|cache|body|bodyShape"],
                    "rig_dags": ["|Group|Geometry|RIG_geo|body_msh|body_mshShape"],
                    "layer_name": "body",
                    "reason": "same topology",
                },
                {
                    "group_id": "g0002",
                    "action": "PAIRED",
                    "abc_dags": ["ABC|Group|cache|hatA|hatAShape", "ABC|Group|cache|hatB|hatBShape"],
                    "rig_dags": ["|Group|Geometry|RIG_geo|hat_msh|hat_mshShape"],
                    "layer_name": "hatA_hatB_Layer",
                    "reason": "split pair",
                },
            ],
            "target_only_dags": ["|Group|Geometry|RIG_geo|extra|extraShape"],
        },
    }
    writer.upsert_step_finished(report, step_ctx, {
        "skill_id": "maya_compare_asset_in_scene",
        "status": "SUCCESS",
        "elapsed_sec": 1.4,
        "input": {"input_source": "a.abc", "cache_group": "|Group|Geometry|RIG_geo"},
        "output": {
            "matched_same": 1,
            "matched_different": 1,
            "only_source": 0,
            "only_target": 1,
            "compare_result": compare_result,
        },
    })
    text = report.read_text(encoding="utf-8")
    assert "compare_result" not in text
    assert "**Details**" not in text
    assert "<th>action</th>" not in text
    assert "bodyShape" not in text
    assert "hatA_hatB_Layer" not in text
    print(f"✓ test_compare_result_renders_action_details_without_json_dump → {report}")


def test_embedded_result_contract_stays_out_of_output_summary():
    sbx = _sandbox("embedded_result")
    report = sbx / "REPORT.md"
    writer.init_report(report, {"task_id": "task-embedded-result", "asset_name": "demo", "run_dir": str(sbx)})
    step_ctx = {"step_index": 0, "step_total": 1, "skill_id": "maya_check_asset_hierarchy", "parameters": {}}
    full_result = {
        "passed": False,
        "code": "ASSET_HIERARCHY_INVALID",
        "extra_top_nodes": ["|Asset", "|transform1"],
        "manual_review_top_nodes": ["|Asset", "|transform1"],
        "issues": [{"type": "extra_top_node", "node": "|Asset", "severity": "warning"}],
    }
    writer.upsert_step_finished(report, step_ctx, {
        "skill_id": "maya_check_asset_hierarchy",
        "status": "SUCCESS",
        "elapsed_sec": 0.4,
        "input": {"source_path": "rig.ma", "phase": "pre_sync"},
        "output": {
            "passed": False,
            "code": "ASSET_HIERARCHY_INVALID",
            "issue_count": 3,
            "result": full_result,
        },
        "report_sections": [
            {"title": "extra_top_nodes", "summary": "2", "items": full_result["extra_top_nodes"]},
            {"title": "issues", "summary": "1", "items": full_result["issues"]},
        ],
    })
    text = report.read_text(encoding="utf-8")
    output_block = text.split("**Output**", 1)[1].split("**Details**", 1)[0]
    assert "- `issue_count`: 3" in output_block
    assert "- `phase`:" not in output_block
    assert "- `extra_top_nodes`:" not in output_block
    assert "- `issues`:" not in output_block
    assert "<summary>extra_top_nodes (2)</summary>" in text
    assert "<summary>issues (1)</summary>" in text
    print(f"✓ test_embedded_result_contract_stays_out_of_output_summary → {report}")


def test_file_flow_table_has_io_status_elapsed():
    sbx = _sandbox("file_flow")
    report = sbx / "REPORT.md"
    writer.init_report(report, {"task_id": "task-file-flow", "asset_name": "demo", "run_dir": str(sbx)})
    writer.upsert_file_staged(report, [
        {
            "node": "资产路径解析",
            "param": "asset_name",
            "input": "ciweiguai",
            "output": "X:/Project/ysj/pub/assets/chr/ciweiguai/rig/rigMaster/a.ma",
            "status": "SUCCESS",
            "elapsed_sec": 3.0,
        },
        {
            "role": "source_path",
            "origin": "X:/Project/ysj/pub/assets/chr/ciweiguai/rig/rigMaster/a.ma",
            "sandbox": str(sbx / "a.ma"),
            "elapsed_sec": 1.2,
        },
    ])
    text = report.read_text(encoding="utf-8")
    assert "| Node | Param | Input | Output | Status | Elapsed |" in text
    assert "资产路径解析" in text
    assert "pipeline_stage_file_to_sandbox" in text
    assert "ciweiguai" in text
    assert "STAGED" in text
    print(f"✓ test_file_flow_table_has_io_status_elapsed → {report}")


def test_internal_chain_history_hidden_from_step_input():
    sbx = _sandbox("internal_history")
    report = sbx / "REPORT.md"
    writer.init_report(report, {"task_id": "task-history", "asset_name": "demo", "run_dir": str(sbx)})
    step_ctx = {
        "step_index": 0,
        "step_total": 1,
        "skill_id": "save_scene",
        "source_path": str(sbx / "demo.ma"),
        "parameters": {
            "_chain_history": [{"detail": '{"compare_result": {"schema_version": "compare_result.v1"}}'}],
            "_open_elapsed_sec": 2.0,
        },
    }
    writer.upsert_step_finished(report, step_ctx, {
        "skill_id": "save_scene",
        "status": "SUCCESS",
        "elapsed_sec": 1.0,
        "input": {},
        "output": {"output_path": str(sbx / "demo_v002.ma")},
    })
    text = report.read_text(encoding="utf-8")
    assert "_chain_history" not in text
    assert "compare_result" not in text
    assert "source_path" in text
    assert "demo_v002.ma" in text
    print(f"✓ test_internal_chain_history_hidden_from_step_input → {report}")


def test_file_flow_merges_across_segments():
    sbx = _sandbox("file_flow_merge")
    report = sbx / "REPORT.md"
    writer.init_report(report, {"task_id": "task-file-flow-merge", "asset_name": "demo", "run_dir": str(sbx)})
    writer.upsert_file_staged(report, [
        {
            "role": "source_path",
            "origin": "X:/Project/ysj/pub/assets/chr/demo/tex/texMaster/demo.blend",
            "sandbox": str(sbx / "demo.blend"),
            "elapsed_sec": 0.2,
        },
    ])
    writer.upsert_file_staged(report, [
        {
            "role": "step.maya_compare_asset_in_scene.input_source",
            "origin": str(sbx / ".info" / "demo.abc"),
            "sandbox": str(sbx / ".info" / "demo.abc"),
            "skipped": True,
            "elapsed_sec": 0.0,
        },
        {
            "role": "source_path",
            "origin": "X:/Project/ysj/pub/assets/chr/demo/rig/rigMaster/demo.ma",
            "sandbox": str(sbx / "demo.ma"),
            "elapsed_sec": 1.1,
        },
    ])
    text = report.read_text(encoding="utf-8")
    assert "demo.blend" in text
    assert "demo.abc" in text
    assert "demo.ma" in text
    assert text.count("demo.blend") >= 1
    print(f"✓ test_file_flow_merges_across_segments → {report}")


def test_open_scene_merges_across_segments():
    sbx = _sandbox("open_scene_merge")
    report = sbx / "REPORT.md"
    writer.init_report(report, {"task_id": "task-open-scene-merge", "asset_name": "demo", "run_dir": str(sbx)})
    blend = str(sbx / "demo.blend")
    maya = str(sbx / "demo.ma")
    writer.upsert_open_scene(report, blend, "RUNNING")
    writer.upsert_open_scene(report, blend, "SUCCESS", elapsed_sec=0.5)
    writer.upsert_open_scene(report, maya, "RUNNING")
    writer.upsert_open_scene(report, maya, "SUCCESS", elapsed_sec=2.0)
    text = report.read_text(encoding="utf-8")
    assert "blender_open_scene" in text
    assert "maya_open_scene" in text
    assert "demo.blend" in text
    assert "demo.ma" in text
    assert text.count("blender_open_scene") == 1, text
    assert text.count("maya_open_scene") == 1, text
    print(f"✓ test_open_scene_merges_across_segments → {report}")


if __name__ == "__main__":
    print("=== task_report_writer unit tests ===\n")
    test_step_upsert_no_duplicate()
    test_error_block_contains_full_detail()
    test_sections_and_report_content()
    test_compare_result_renders_action_details_without_json_dump()
    test_embedded_result_contract_stays_out_of_output_summary()
    test_file_flow_table_has_io_status_elapsed()
    test_internal_chain_history_hidden_from_step_input()
    test_file_flow_merges_across_segments()
    test_open_scene_merges_across_segments()
    print("\n✅ all pass")
