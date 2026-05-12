# -*- coding: utf-8 -*-
"""运行时任务报告写入器。

调度层在任务生命周期中调用本模块，把每个 step 的运行事实 upsert 到
同一个 REPORT.md。业务 skill 只返回 receipt，不直接决定报告文件形态。
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
import time
import traceback as _traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.receipt import _format_elapsed, _status_icon


REPORT_FILENAME = "REPORT.md"
_LOCK_TIMEOUT_SEC = 30.0


_ACTION_BY_SKILL = {
    "copy_files": "取文件",
    "resolve_asset_files": "解析路径",
    "pipeline_compare_asset": "对比资产",
    "maya_compare_asset_in_scene": "场景对比",
    "maya_sync_rig_incremental": "同步拼装",
    "maya_build_asset_info": "采集信息",
    "blender_build_asset_info": "采集信息",
    "maya_export_abc": "导出ABC",
    "blender_export_abc": "导出ABC",
    "maya_import_abc": "导入ABC",
    "blender_extract_materials": "采集材质",
    "save_scene": "保存场景",
    "rename_asset": "保存场景",
    "maya_master_cleanup": "清理检查",
    "maya_clean_skinweights": "清理权重",
    "maya_fix_shape_names": "修复命名",
    "maya_conform_normals": "统一法线",
    "maya_freeze_transforms": "冻结变换",
    "maya_apply_materials": "应用材质",
    "maya_assign_udim_materials": "应用材质",
    "maya_build_mesh_from_abc": "构建网格",
    "maya_check_textures": "贴图检查",
    "check_uvsets": "检查UV",
    "simplify_uvsets": "精简UV",
    "validate_publish": "质量门禁",
    "maya_get_scene_info": "场景总览",
    "maya_get_object_info": "对象信息",
    "maya_capture_viewport": "视口截图",
    "blender_capture_viewport": "视口截图",
    "ping": "心跳检测",
    "exec_code": "执行代码",
    "blender_exec_code": "执行代码",
}


def get_report_path(run_dir: str | Path) -> str:
    return str(Path(run_dir) / REPORT_FILENAME)


def now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _escape_md(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text.replace("`", "'")


def _escape_cell(value: Any) -> str:
    text = _escape_md(value)
    return text.replace("|", "\\|").replace("\r\n", " / ").replace("\n", " / ")


def _fmt_path(value: Any) -> str:
    if not value:
        return "-"
    return f"`{_escape_md(value)}`"


def _fmt_cell_path(value: Any) -> str:
    if not value:
        return "-"
    return f"`{_escape_cell(value)}`"


def _fmt_elapsed_sec(elapsed_sec: Optional[float]) -> str:
    if not isinstance(elapsed_sec, (int, float)):
        return "-"
    return _format_elapsed(float(elapsed_sec) / 60.0)


def _looks_like_path(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    lower = value.lower()
    return (
        "/" in value or "\\" in value or
        lower.endswith((".ma", ".mb", ".blend", ".json", ".abc", ".md", ".png", ".jpg", ".jpeg"))
    )


def _brief_outputs(outputs: Dict[str, Any]) -> str:
    if not outputs:
        return "-"
    result = outputs.get("result")
    if isinstance(result, dict):
        path_parts = []
        for key in ("source_path", "rig_path", "output_path", "report_path"):
            if result.get(key):
                path_parts.append(f"result.{key}={_fmt_cell_path(result.get(key))}")
        if path_parts:
            return "；".join(path_parts[:4])
        if result:
            return f"result={len(result)} 个字段"
    preferred = ["output_path", "report_path", "result"]
    parts = []
    for key in preferred + [k for k in outputs.keys() if k not in preferred]:
        if key not in outputs:
            continue
        value = outputs.get(key)
        if _looks_like_path(value):
            parts.append(f"{key}={_fmt_cell_path(value)}")
        else:
            parts.append(f"{key}={_escape_cell(value)}")
        if len(parts) >= 4:
            break
    if len(outputs) > len(parts):
        parts.append(f"... 其他 {len(outputs) - len(parts)} 项")
    return "；".join(parts) if parts else "-"


def _short_json(value: Any, max_chars: int = 6000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = str(value)
    if len(text) > max_chars:
        return text[:max_chars] + f"\n... 已截断 {len(text) - max_chars} 字符"
    return text


def _skill_label(skill_id: str) -> str:
    if skill_id in _ACTION_BY_SKILL:
        return _ACTION_BY_SKILL[skill_id]
    if skill_id.endswith("_export_abc"):
        return "导出ABC"
    if skill_id.endswith("_build_asset_info") or skill_id.endswith("_get_scene_info"):
        return "采集信息"
    if "compare" in skill_id:
        return "对比资产"
    if "sync" in skill_id:
        return "同步拼装"
    if "material" in skill_id:
        return "材质处理"
    if "clean" in skill_id or "cleanup" in skill_id or "fix" in skill_id:
        return "清理修复"
    return "执行技能"


@contextlib.contextmanager
def _locked_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    start = time.time()
    fd = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            break
        except FileExistsError:
            if time.time() - start > _LOCK_TIMEOUT_SEC:
                raise TimeoutError(f"等待报告锁超时: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _block_markers(block_id: str) -> tuple[str, str]:
    safe = str(block_id).replace("\n", " ").strip()
    return (
        f"[//]: # (report:block:start {safe})",
        f"[//]: # (report:block:end {safe})",
    )


def _legacy_block_markers(block_id: str) -> tuple[str, str]:
    safe = str(block_id).replace("\n", " ").strip()
    return (
        f"<!-- report:block:start {safe} -->",
        f"<!-- report:block:end {safe} -->",
    )


def upsert_block(report_path: str | Path, block_id: str, markdown: str,
                 before_block_id: str = "final") -> None:
    """按 block id 替换或插入报告模块。"""
    path = Path(report_path)
    start_marker, end_marker = _block_markers(block_id)
    block = f"{start_marker}\n{markdown.rstrip()}\n{end_marker}\n"

    with _locked_file(path):
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        replaced = False
        for old_start, old_end in (start_marker, end_marker), _legacy_block_markers(block_id):
            pattern = re.compile(
                re.escape(old_start) + r".*?" + re.escape(old_end) + r"\n?",
                re.DOTALL,
            )
            if pattern.search(text):
                text = pattern.sub(lambda _m: block, text)
                replaced = True
                break
        if not replaced:
            before_start, _ = _block_markers(before_block_id)
            idx = text.find(before_start)
            if idx < 0:
                legacy_before_start, _ = _legacy_block_markers(before_block_id)
                idx = text.find(legacy_before_start)
            if idx >= 0:
                text = text[:idx].rstrip() + "\n\n" + block + "\n" + text[idx:].lstrip()
            else:
                text = text.rstrip() + "\n\n" + block if text else block
        path.write_text(text.rstrip() + "\n", encoding="utf-8")


def init_report(report_path: str | Path, task_context: Dict[str, Any]) -> str:
    """初始化 REPORT.md。重复调用不会清空已有 step block。"""
    path = Path(report_path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        asset = task_context.get("asset_name") or "untitled"
        anchor_start, anchor_end = _block_markers("execution_anchor")
        path.write_text(
            f"# {asset} 任务报告\n\n{anchor_start}\n## 执行明细\n{anchor_end}\n",
            encoding="utf-8",
        )
    upsert_block(path, "header", render_header(task_context, "RUNNING"),
                 before_block_id="execution_anchor")
    upsert_block(path, "final", render_final(task_context, "RUNNING"),
                 before_block_id="__never__")
    return str(path)


def render_header(task_context: Dict[str, Any], status: str) -> str:
    lines = [
        "| 字段 | 值 |",
        "|---|---|",
        f"| 状态 | {_status_icon(status)} {status} |",
        f"| 任务 ID | `{_escape_md(task_context.get('task_id', ''))}` |",
        f"| 资产 | {_escape_md(task_context.get('asset_name', 'untitled'))} |",
    ]
    if task_context.get("project"):
        lines.append(f"| 项目 | {_escape_md(task_context.get('project'))} |")
    if task_context.get("workflow_id"):
        lines.append(f"| 工作流 | `{_escape_md(task_context.get('workflow_id'))}` |")
    if task_context.get("source_path"):
        lines.append(f"| 来源文件 | {_fmt_path(task_context.get('source_path'))} |")
    if task_context.get("run_dir"):
        lines.append(f"| 沙盒 | {_fmt_path(task_context.get('run_dir'))} |")
    lines.append(f"| 更新时间 | {now_str()} |")
    return "\n".join(lines)


def render_final(task_context: Dict[str, Any], status: str,
                 elapsed_min: Optional[float] = None,
                 error: str = "",
                 traceback_text: str = "") -> str:
    lines = [
        "## 最终状态",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        f"| 状态 | {_status_icon(status)} {status} |",
        f"| 更新时间 | {now_str()} |",
    ]
    if elapsed_min is not None:
        lines.append(f"| 总耗时 | {_format_elapsed(elapsed_min)} |")
    if task_context.get("run_dir"):
        lines.append(f"| 沙盒 | {_fmt_cell_path(task_context.get('run_dir'))} |")
    if error:
        lines.extend(["", "**错误内容**", "", "```text", str(error), "```"])
    if traceback_text:
        lines.extend(["", "**完整 Traceback**", "", "```text", str(traceback_text), "```"])
    return "\n".join(lines)


def finalize_report(report_path: str | Path, task_context: Dict[str, Any],
                    final_status: str, elapsed_min: Optional[float] = None,
                    error: str = "", traceback_text: str = "") -> None:
    context = dict(task_context)
    upsert_block(report_path, "header", render_header(context, final_status))
    upsert_block(
        report_path,
        "final",
        render_final(context, final_status, elapsed_min, error, traceback_text),
        before_block_id="__never__",
    )


def _role_node_param(record: Dict[str, Any]) -> tuple[str, str]:
    if record.get("node") or record.get("param"):
        return str(record.get("node") or "文件流转"), str(record.get("param") or record.get("role") or "-")

    role = str(record.get("role") or "-")
    if role == "source_path":
        return "文件备份", "主场景文件"
    if role.startswith("input."):
        return "文件备份", role.split(".", 1)[1]
    if role.startswith("step."):
        parts = role.split(".")
        if len(parts) >= 3:
            return f"文件备份:{parts[1]}", ".".join(parts[2:])
    return "文件备份", role


def render_file_staged(records: Iterable[Dict[str, Any]]) -> str:
    records = list(records or [])
    lines = [
        "## 文件流转",
        "",
        f"共 {len(records)} 项文件或路径节点记录。",
        "",
        "| 节点 | 参数 | 输入 | 输出 | 状态 | 耗时 |",
        "|---|---|---|---|---|---|",
    ]
    for rec in records:
        node, param = _role_node_param(rec)
        if rec.get("status"):
            state = str(rec.get("status"))
        elif rec.get("skipped"):
            state = "已在沙盒"
        elif rec.get("reused"):
            state = "复用副本"
        elif rec.get("error"):
            state = "失败"
        else:
            state = "已备份"
        lines.append(
            f"| {_escape_cell(node)} | "
            f"`{_escape_cell(param)}` | "
            f"{_fmt_cell_path(rec.get('input', rec.get('origin', '')))} | "
            f"{_fmt_cell_path(rec.get('output', rec.get('sandbox', '')))} | "
            f"{_escape_cell(state)} | "
            f"{_fmt_elapsed_sec(rec.get('elapsed_sec'))} |"
        )
    return "\n".join(lines)


def upsert_file_staged(report_path: str | Path, records: Iterable[Dict[str, Any]]) -> None:
    upsert_block(report_path, "context:file_staged", render_file_staged(records))


def render_open_scene(source_path: str, status: str,
                      elapsed_sec: Optional[float] = None,
                      error: str = "") -> str:
    elapsed_text = _fmt_elapsed_sec(elapsed_sec)
    lines = [
        "## 打开场景",
        "",
        "| 节点 | 输入 | 输出 | 状态 | 耗时 |",
        "|---|---|---|---|---|",
        f"| 打开场景 | {_fmt_cell_path(source_path)} | 当前 DCC 会话 | {_status_icon(status)} {status} | {elapsed_text} |",
    ]
    if error:
        lines.extend(["", "**错误内容**", "", "```text", str(error), "```"])
    return "\n".join(lines)


def upsert_open_scene(report_path: str | Path, source_path: str, status: str,
                      elapsed_sec: Optional[float] = None,
                      error: str = "") -> None:
    upsert_block(report_path, "context:open_scene",
                 render_open_scene(source_path, status, elapsed_sec, error))


def _step_block_id(step_context: Dict[str, Any]) -> str:
    segment = step_context.get("segment")
    prefix = f"seg{segment}" if segment is not None and segment != "" and segment != -1 else "main"
    idx = step_context.get("step_index", 0)
    skill_id = step_context.get("skill_id", "unknown")
    return f"step:{prefix}:{idx}:{skill_id}"


def render_step_started(step_context: Dict[str, Any]) -> str:
    idx = int(step_context.get("step_index", 0)) + 1
    total = step_context.get("step_total") or "-"
    skill_id = step_context.get("skill_id", "unknown")
    label = _skill_label(skill_id)
    lines = [
        f"### Step {idx}/{total} | {label} | RUNNING | -",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        f"| 节点 | {_escape_cell(label)} |",
        f"| Skill | `{_escape_cell(skill_id)}` |",
        "| 状态 | RUNNING |",
    ]
    if step_context.get("segment") not in (None, "", -1):
        lines.append(f"| Segment | {step_context.get('segment')} |")
    if step_context.get("source_path"):
        lines.append(f"| 输入场景 | {_fmt_cell_path(step_context.get('source_path'))} |")
    params = step_context.get("parameters", {}) or {}
    lines.extend([
        "",
        "#### 输入参数",
        "",
        "```json",
        _short_json(params),
        "```",
    ])
    return "\n".join(lines)


def upsert_step_started(report_path: str | Path, step_context: Dict[str, Any]) -> None:
    upsert_block(report_path, _step_block_id(step_context), render_step_started(step_context))


def render_step_finished(step_context: Dict[str, Any], receipt: Dict[str, Any],
                         worker_status: str = "", memory_gb: Optional[float] = None,
                         raw_detail: str = "") -> str:
    idx = int(step_context.get("step_index", 0)) + 1
    total = step_context.get("step_total") or "-"
    skill_id = receipt.get("skill_id") or step_context.get("skill_id", "unknown")
    status = receipt.get("status") or worker_status or "UNKNOWN"
    label = _skill_label(skill_id)
    elapsed_min = receipt.get("elapsed_min")
    elapsed_text = _format_elapsed(elapsed_min) if isinstance(elapsed_min, (int, float)) else "-"
    summary = receipt.get("summary", {}) or {}
    action = summary.get("action") or ""
    if summary.get("output_count"):
        action = f"{action} → {summary.get('output_count')} 个 {summary.get('output_label', '项')}".strip()

    summary_line = f"Step {idx}/{total} | {label} | {status} | {elapsed_text}"
    if action:
        summary_line += f" | {_escape_md(action)}"

    outputs = receipt.get("outputs", {}) or {}
    input_summary = summary.get("input") or step_context.get("source_path") or "-"
    lines = [
        f"### {summary_line}",
        "",
        "#### 节点状态",
        "",
        "| 节点 | Skill | 输入 | 输出 | 状态 | 耗时 |",
        "|---|---|---|---|---|---|",
        (
            f"| {_escape_cell(label)} | `{_escape_cell(skill_id)}` | "
            f"{_escape_cell(input_summary)} | {_brief_outputs(outputs)} | "
            f"{_status_icon(status)} {status} | {elapsed_text} |"
        ),
    ]
    if memory_gb is not None and memory_gb >= 0:
        lines.extend(["", f"- **内存**: {memory_gb:.2f} GB"])
    if action:
        lines.append(f"- **执行摘要**: {_escape_md(action)}")

    if outputs:
        lines.extend(["", "#### 输出参数", "", "```json",
                      _short_json(outputs), "```"])

    items = receipt.get("items", []) or []
    if items:
        lines.extend(_render_items(items))

    sections = receipt.get("report_sections", []) or []
    for section in sections:
        lines.extend(_render_section(section))

    report_content = receipt.get("report_content", "")
    has_structured_sections = "report_sections" in receipt and receipt.get("report_sections") is not None
    if report_content and not has_structured_sections:
        lines.extend([
            "",
            "#### 详细报告",
            "",
            str(report_content),
        ])

    error = receipt.get("error", "")
    recovery = receipt.get("recovery_hint", "")
    tb = receipt.get("traceback", "") or receipt.get("traceback_text", "")
    if error or recovery or tb or (status not in ("SUCCESS", "RUNNING") and raw_detail):
        lines.extend(["", "#### 错误与恢复建议", ""])
        if error:
            lines.extend(["**错误内容**", "", "```text", str(error), "```", ""])
        if recovery:
            lines.extend(["**恢复建议**", "", str(recovery), ""])
        if tb:
            lines.extend(["**完整 Traceback**", "", "```text", str(tb), "```", ""])
        elif raw_detail and status != "SUCCESS":
            lines.extend(["**原始返回**", "", "```text", str(raw_detail), "```", ""])

    if raw_detail and not receipt.get("_parsed", True) and status == "SUCCESS":
        lines.extend([
            "",
            "#### 原始返回",
            "",
            "```text",
            str(raw_detail),
            "```",
        ])

    return "\n".join(lines)


def upsert_step_finished(report_path: str | Path, step_context: Dict[str, Any],
                         receipt: Dict[str, Any], worker_status: str = "",
                         memory_gb: Optional[float] = None,
                         raw_detail: str = "") -> None:
    upsert_block(
        report_path,
        _step_block_id(step_context),
        render_step_finished(step_context, receipt, worker_status, memory_gb, raw_detail),
    )


def _render_items(items: List[Dict[str, Any]]) -> List[str]:
    lines = [
        "",
        f"#### 受影响对象 | {len(items)} 项",
        "",
        "| 对象 | 详情 | 耗时 |",
        "|---|---|---|",
    ]
    for item in items:
        elapsed = item.get("elapsed_min")
        elapsed_text = _format_elapsed(elapsed) if isinstance(elapsed, (int, float)) else "-"
        lines.append(
            f"| {_escape_cell(item.get('name', ''))} | "
            f"{_escape_cell(item.get('detail', ''))} | {elapsed_text} |"
        )
    return lines


def _render_section(section: Dict[str, Any]) -> List[str]:
    title = section.get("title") or section.get("name") or "详细信息"
    summary = section.get("summary") or ""
    head = f"{title}" + (f" | {summary}" if summary else "")
    lines = ["", f"#### {_escape_md(head)}", ""]
    if section.get("description"):
        lines.extend([str(section.get("description")), ""])
    if section.get("content"):
        lines.extend([str(section.get("content")), ""])
    if section.get("markdown"):
        lines.extend([str(section.get("markdown")), ""])
    items = section.get("items") or []
    if items:
        total_items = len(items)
        shown_items = items[:20]
        if all(isinstance(x, dict) for x in items):
            keys = []
            for item in shown_items:
                for key in item.keys():
                    if key not in keys:
                        keys.append(key)
                if len(keys) >= 6:
                    break
            keys = keys[:6] or ["name", "detail"]
            lines.append("| " + " | ".join(_escape_cell(k) for k in keys) + " |")
            lines.append("|" + "|".join("---" for _ in keys) + "|")
            for item in shown_items:
                lines.append("| " + " | ".join(_escape_cell(item.get(k, "")) for k in keys) + " |")
        else:
            for item in shown_items:
                lines.append(f"- {_escape_md(item)}")
        if total_items > len(shown_items):
            lines.append(f"- ... 其他 {total_items - len(shown_items)} 项")
        lines.append("")
    return lines


def render_segment(segment_context: Dict[str, Any], status: str,
                   elapsed_min: Optional[float] = None,
                   error: str = "") -> str:
    idx = segment_context.get("segment_index", 0)
    dcc = segment_context.get("dcc", "")
    step_count = segment_context.get("step_count", 0)
    elapsed_text = _format_elapsed(elapsed_min) if elapsed_min is not None else "-"
    lines = [
        f"## Segment {idx} | {dcc} | {status} | {step_count} 步 | {elapsed_text}",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        f"| DCC | `{_escape_cell(dcc)}` |",
        f"| 步骤数 | {step_count} |",
        f"| 状态 | {_status_icon(status)} {status} |",
        f"| 耗时 | {elapsed_text} |",
    ]
    if error:
        lines.extend(["", "```text", str(error), "```"])
    return "\n".join(lines)


def upsert_segment(report_path: str | Path, segment_context: Dict[str, Any],
                   status: str, elapsed_min: Optional[float] = None,
                   error: str = "") -> None:
    block_id = f"segment:{segment_context.get('segment_index', 0)}"
    upsert_block(report_path, block_id, render_segment(segment_context, status, elapsed_min, error))


def extract_receipt(detail: Any, skill_id: str = "", status: str = "") -> Dict[str, Any]:
    """把 worker wrapper detail 或直接 receipt 统一转成 receipt-like dict。"""
    raw = detail
    if isinstance(detail, dict):
        if "summary" in detail or "outputs" in detail or "skill_id" in detail:
            rc = dict(detail)
            rc.setdefault("skill_id", skill_id or rc.get("skill_id", "unknown"))
            rc.setdefault("status", status or rc.get("status", "UNKNOWN"))
            rc["_parsed"] = True
            return rc
        raw = json.dumps(detail, ensure_ascii=False, default=str)

    if isinstance(detail, str) and detail:
        payload = detail
        if " [mem=" in payload:
            payload = payload.rsplit(" [mem=", 1)[0]
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                parsed.setdefault("skill_id", skill_id or parsed.get("skill_id", "unknown"))
                parsed.setdefault("status", status or parsed.get("status", "UNKNOWN"))
                parsed["_parsed"] = True
                return parsed
        except Exception:
            pass

    rc = {
        "skill_id": skill_id or "unknown",
        "status": status or "UNKNOWN",
        "elapsed_min": 0,
        "summary": {"action": "非标准返回"},
        "items": [],
        "outputs": {},
        "_parsed": False,
    }
    if raw:
        if status and status != "SUCCESS":
            rc["error"] = str(raw)
        else:
            rc["report_content"] = str(raw)
    return rc


def receipt_from_exception(skill_id: str, exc: BaseException,
                           status: str = "ERROR") -> Dict[str, Any]:
    return {
        "skill_id": skill_id,
        "status": status,
        "elapsed_min": 0,
        "summary": {"action": "执行异常"},
        "items": [],
        "outputs": {},
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": _traceback.format_exc(),
    }
