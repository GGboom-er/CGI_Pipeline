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
    assert "<details" not in text
    step_block = text.split("report:block:start step:main:0:pipeline_compare_asset", 1)[1]
    step_block = step_block.split("report:block:end step:main:0:pipeline_compare_asset", 1)[0]
    assert "RUNNING" not in step_block, text
    assert "SUCCESS" in text
    assert "| 节点 | Skill | 输入 | 输出 | 状态 | 耗时 |" in text
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
    assert "<details" not in text
    assert "#### 错误与恢复建议" in text
    assert "绑定目标缺少 ShapeOrig" in text
    assert "Traceback line 2" in text
    assert "先运行命名修复" in text
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
    assert "通过配对 | 73 项" in text
    assert "bodyShape" in text
    assert "几何差异 | 0 项" in text
    assert "旧格式完整对比报告" not in text
    print(f"✓ test_sections_and_report_content → {report}")


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
    assert "| 节点 | 参数 | 输入 | 输出 | 状态 | 耗时 |" in text
    assert "资产路径解析" in text
    assert "文件备份" in text
    assert "ciweiguai" in text
    assert "已备份" in text
    print(f"✓ test_file_flow_table_has_io_status_elapsed → {report}")


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
    assert "打开Blender场景" in text
    assert "打开Maya场景" in text
    assert "demo.blend" in text
    assert "demo.ma" in text
    assert text.count("打开Blender场景") == 1, text
    assert text.count("打开Maya场景") == 1, text
    print(f"✓ test_open_scene_merges_across_segments → {report}")


if __name__ == "__main__":
    print("=== task_report_writer unit tests ===\n")
    test_step_upsert_no_duplicate()
    test_error_block_contains_full_detail()
    test_sections_and_report_content()
    test_file_flow_table_has_io_status_elapsed()
    test_file_flow_merges_across_segments()
    test_open_scene_merges_across_segments()
    print("\n✅ all pass")
