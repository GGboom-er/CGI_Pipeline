import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import task_report_writer
from cli import _is_workflow_wait_terminal
from skills.resolve_asset_files.resolve_asset_files import execute as resolve_asset_files


def _ok(condition, message):
    assert condition, message
    print(f"  [PASS] {message}")


def test_resolve_missing_inputs_are_structured():
    print("\n=== Test 1: resolve 缺输入返回结构化失败 ===")
    receipt = resolve_asset_files({
        "project": "ysj",
        "asset_name": "__missing_asset_for_contract__",
        "parameters": {"category": "chr"},
    })
    _ok(receipt["status"] == "ERROR", "缺 tex/rig 时 resolve 返回 ERROR")

    output = receipt.get("output") or {}
    result = output.get("result") or {}
    missing = result.get("missing_inputs") or output.get("missing_inputs") or []
    roles = {item.get("role") for item in missing}

    _ok(roles == {"source", "rig"}, "missing_inputs 明确 source 和 rig")
    _ok(all(item.get("path") for item in missing), "missing_inputs 写出实际搜索目录")
    _ok(any("texMaster" in item.get("path", "") for item in missing), "source 缺失目录指向 texMaster")
    _ok(any("rigMaster" in item.get("path", "") for item in missing), "rig 缺失目录指向 rigMaster")
    _ok(bool(result.get("searched_paths")), "失败时仍返回 searched_paths 供报告和排障")
    _ok("report_sections" not in receipt, "失败回执不再依赖 report_sections")
    _ok(bool(output.get("missing_inputs")), "失败报告明细来自 output.missing_inputs")


def test_report_keeps_structured_error_without_raw_json_dump():
    print("\n=== Test 2: 报告保留结构化错误而不展开原始 JSON ===")
    receipt = resolve_asset_files({
        "project": "ysj",
        "asset_name": "__missing_asset_for_report__",
        "parameters": {"category": "chr"},
    })

    sbx = ROOT / "projects" / "_test_report_writer" / "missing_inputs"
    if sbx.exists():
        shutil.rmtree(sbx)
    sbx.mkdir(parents=True, exist_ok=True)
    report = sbx / "REPORT.md"

    context = {
        "task_id": "test-missing-inputs",
        "asset_name": "__missing_asset_for_report__",
        "project": "ysj",
        "workflow_id": "tex_to_rig_verify_and_sync",
        "run_dir": str(sbx),
    }
    task_report_writer.init_report(report, context)
    step_context = {
        "step_index": 0,
        "step_total": 12,
        "skill_id": "resolve_asset_files",
        "parameters": {"category": "chr"},
    }
    raw = json.dumps(receipt, ensure_ascii=False, default=str)
    task_report_writer.upsert_step_finished(
        report,
        step_context,
        receipt,
        worker_status="ERROR",
        raw_detail=raw,
    )
    task_report_writer.finalize_report(report, context, "WORKFLOW_ABORTED", error=receipt.get("error", ""))

    content = report.read_text(encoding="utf-8")
    _ok("Step 1/12 | pipeline_resolve_tex_rig_paths | ERROR" in content,
        "失败 step 标题保留 ERROR")
    _ok("missing_inputs" in content and "searched_paths" in content, "报告展示缺失输入和搜索路径")
    _ok("__missing_asset_for_report__" in content, "报告保留资产名上下文")
    _ok("Raw Detail" not in content, "已解析 receipt 不再追加原始 JSON")
    _ok("report:block" not in content and "<details>" not in content and "<table" not in content,
        "最终报告无内部 marker 和 HTML")


def test_chain_preserves_structured_error_receipt():
    print("\n=== Test 3: chain 不覆盖结构化错误回执 ===")
    source = (ROOT / "core" / "tasks.py").read_text(encoding="utf-8")
    _ok("raw_detail = translated_err" not in source,
        "调度层不把结构化 detail 覆盖成纯文本错误")
    _ok("result['error'] = translated_err" in source,
        "翻译错误只写 error 字段")
    _ok("receipt['error'] = translated_err" in source,
        "非标准错误仍补入 receipt.error")
    _ok("if is_subchain:\n            return" in source,
        "workflow 子段不自行 finalize 报告")


def test_cli_wait_does_not_stop_on_intermediate_status():
    print("\n=== Test 4: CLI wait 只在终态退出 ===")
    for status in ("WORKFLOW_STARTED", "WORKFLOW_SEGMENTED", "SEGMENT_START", "SEGMENT_SUCCESS", "PROGRESS", "NOT_FOUND"):
        _ok(not _is_workflow_wait_terminal(status), f"{status} 不是终态")
    for status in ("WORKFLOW_SUCCESS", "WORKFLOW_ABORTED", "WORKFLOW_ERROR", "CHAIN_ABORTED", "TIMEOUT"):
        _ok(_is_workflow_wait_terminal(status), f"{status} 是终态")


if __name__ == "__main__":
    print("=== workflow input contract tests ===")
    test_resolve_missing_inputs_are_structured()
    test_report_keeps_structured_error_without_raw_json_dump()
    test_chain_preserves_structured_error_receipt()
    test_cli_wait_does_not_stop_on_intermediate_status()
    print("\nALL WORKFLOW INPUT CONTRACT TESTS PASSED!")
