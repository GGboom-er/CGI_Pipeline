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


_REPORT_SKILL_NAME_BY_ID = {
    "copy_files": "pipeline_stage_file_to_sandbox",
    "resolve_asset_files": "pipeline_resolve_tex_rig_paths",
    "pipeline_compare_asset": "pipeline_compare_geometry_sources",
    "maya_compare_asset_in_scene": "maya_compare_abc_to_scene_geometry",
    "maya_sync_rig_incremental": "maya_sync_abc_to_rig_cache",
    "maya_build_asset_info": "maya_collect_scene_geometry_info",
    "blender_build_asset_info": "blender_collect_cache_geometry_info",
    "maya_export_abc": "maya_export_cache_to_abc",
    "blender_export_abc": "blender_export_cache_to_abc",
    "maya_import_abc": "maya_import_abc_to_scene",
    "blender_extract_materials": "blender_extract_cache_materials",
    "save_scene": "maya_save_scene_as_next_version",
    "rename_asset": "pipeline_rename_asset_file",
    "maya_master_cleanup": "maya_cleanup_scene",
    "maya_clean_skinweights": "maya_clean_skin_weights",
    "maya_fix_shape_names": "maya_fix_cache_shape_names",
    "maya_conform_normals": "maya_conform_cache_normals",
    "maya_freeze_transforms": "maya_freeze_scene_transforms",
    "maya_apply_materials": "maya_apply_materials_to_cache",
    "maya_build_mesh_from_abc": "maya_build_scene_meshes_from_abc",
    "maya_check_textures": "maya_check_scene_textures",
    "maya_check_asset_hierarchy": "maya_check_rig_geometry_layout",
    "maya_fix_asset_hierarchy": "maya_fix_rig_geometry_layout",
    "check_uvsets": "maya_check_cache_uv_sets",
    "simplify_uvsets": "maya_simplify_cache_uv_sets",
    "validate_publish": "maya_validate_publish_scene",
    "maya_get_scene_info": "maya_collect_scene_overview",
    "maya_get_object_info": "maya_collect_object_info",
    "maya_capture_viewport": "maya_capture_viewport_image",
    "blender_capture_viewport": "blender_capture_viewport_image",
    "ping": "pipeline_ping_worker",
    "exec_code": "maya_execute_python_code",
    "blender_exec_code": "blender_execute_python_code",
}

_HIDDEN_INPUT_KEYS = {
    "_chain_history",
    "_open_elapsed_sec",
    "output_path",
    "abc_path",
    "compare_result",
}
_HIDDEN_OUTPUT_KEYS = {
    "compare_result",
    "report_content",
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


def _display_skill_name(skill_id: str) -> str:
    return _REPORT_SKILL_NAME_BY_ID.get(skill_id, skill_id or "unknown_skill")


def _is_hidden_key(key: str, section: str) -> bool:
    if str(key).startswith("_"):
        return True
    if section == "input":
        return key in _HIDDEN_INPUT_KEYS
    if section == "output":
        return key in _HIDDEN_OUTPUT_KEYS
    return False


def _short_dag(dag: Any) -> str:
    text = str(dag or "")
    parts = text.strip("|").split("|")
    leaf = parts[-1] if parts else text
    return leaf.split(":")[-1]


def _normalize_report_io(values: Dict[str, Any], section: str) -> Dict[str, Any]:
    if not isinstance(values, dict):
        return {}
    result = values.get("result")
    if section == "output" and isinstance(result, dict):
        if len(values) == 1:
            if result.get("source_path") or result.get("rig_path"):
                values = {
                    key: result.get(key)
                    for key in ("source_path", "rig_path", "source_version", "rig_version")
                    if result.get(key) not in (None, "")
                }
            else:
                values = dict(result)
        elif set(values.keys()).issubset({"output_path", "result"}):
            values = {key: value for key, value in values.items() if key != "result"}
            values.update(result)
        else:
            values = dict(values)
    else:
        values = dict(values)
    if section == "output":
        output_path = values.get("output_path")
        if output_path:
            if values.get("abc_path") == output_path or values.get("materials_path") == output_path:
                values.pop("output_path", None)
            elif any(key in values for key in ("matched_same", "matched_different", "only_source", "only_target")):
                values["compare_result_path"] = output_path
                values.pop("output_path", None)
            elif len(values) == 1 and str(output_path).lower().endswith((".ma", ".mb", ".blend")):
                values["scene_path"] = output_path
                values.pop("output_path", None)
    clean: Dict[str, Any] = {}
    result_is_embedded_contract = (
        section == "output"
        and isinstance(values.get("result"), dict)
        and len(values) > 1
    )
    for key, value in values.items():
        if _is_hidden_key(str(key), section):
            continue
        if key == "result" and isinstance(value, dict):
            if result_is_embedded_contract:
                continue
            for sub_key, sub_value in value.items():
                if not _is_hidden_key(str(sub_key), section):
                    clean[str(sub_key)] = sub_value
            continue
        clean[str(key)] = _report_safe_value(value)
    return clean


def _md_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", " / ").replace("\n", " / ")
    if _looks_like_path(text):
        if "|" in text:
            return text.replace("|", "&#124;").replace("`", "\\`")
        escaped = text.replace("`", "\\`")
        return f"`{escaped}`"
    return text.replace("|", "&#124;").replace("`", "\\`")


def _render_md_table(headers: List[str], rows: List[List[Any]]) -> List[str]:
    clean_headers = [_md_cell(header) for header in headers]
    lines = [
        "| " + " | ".join(clean_headers) + " |",
        "| " + " | ".join("---" for _ in clean_headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(value) for value in row) + " |")
    return lines


def _render_md_list(items: Iterable[Any]) -> List[str]:
    return [f"- {_md_cell(item)}" for item in items]


def _render_md_pre(text: str) -> List[str]:
    safe_text = str(text).replace("```", "` ` `")
    return ["```text", safe_text, "```"]


def _render_md_subsection(title: str, body_lines: List[str]) -> List[str]:
    return [
        f"##### {title}",
        *body_lines,
    ]


def _wrap_summary_body(summary_line: str, body_lines: List[str]) -> str:
    return "\n".join([
        f"### {summary_line}",
        *body_lines,
        "",
    ])


def _render_items_details(items: Any) -> List[str]:
    if not isinstance(items, list) or not items:
        return []
    rows = []
    for item in items:
        if isinstance(item, dict):
            rows.append([
                item.get("name", ""),
                item.get("detail", ""),
                _format_elapsed(item.get("elapsed_min")) if isinstance(item.get("elapsed_min"), (int, float)) else "",
            ])
        else:
            rows.append([str(item), "", ""])
    return _render_md_subsection(
        f"ITEMS ({len(rows)})",
        _render_md_table(["name", "detail", "elapsed"], rows),
    )


def _render_output_field_details(outputs: Dict[str, Any]) -> List[str]:
    clean = _normalize_report_io(outputs, "output")
    lines: List[str] = []
    scalar_rows: List[List[Any]] = []
    for key, value in clean.items():
        if isinstance(value, list) and value:
            if all(isinstance(item, dict) for item in value):
                keys: List[str] = []
                for item in value:
                    for item_key in item.keys():
                        if item_key not in keys:
                            keys.append(item_key)
                rows = [[item.get(item_key, "") for item_key in keys] for item in value]
                body = _render_md_table(keys, rows)
            else:
                body = _render_md_list(value)
            lines.extend(_render_md_subsection(f"{key} ({len(value)})", body))
        elif isinstance(value, dict) and value:
            rows = [[sub_key, sub_value] for sub_key, sub_value in value.items()]
            lines.extend(_render_md_subsection(
                f"{key} ({len(value)} fields)",
                _render_md_table(["field", "value"], rows),
            ))
        elif value not in (None, "", [], {}):
            scalar_rows.append([key, value])
    if scalar_rows:
        lines[0:0] = _render_md_subsection(
            "result",
            _render_md_table(["field", "value"], scalar_rows),
        )
    return lines


def _report_safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("schema_version") == "compare_result.v1":
            compare = value.get("compare") if isinstance(value.get("compare"), dict) else {}
            groups = compare.get("pairing_groups") if isinstance(compare.get("pairing_groups"), list) else []
            return f"<compare_result.v1 groups={len(groups)}>"
        safe = {}
        for key, item in value.items():
            if key == "compare_result" and isinstance(item, dict):
                safe[key] = _report_safe_value(item)
            else:
                safe[key] = _report_safe_value(item)
        return safe
    if isinstance(value, list):
        return [_report_safe_value(item) for item in value]
    return value


def _skill_label(skill_id: str) -> str:
    return _display_skill_name(skill_id)


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
        f"<!-- report:block:start {safe} -->",
        f"<!-- report:block:end {safe} -->",
    )


def _legacy_block_markers(block_id: str) -> tuple[str, str]:
    safe = str(block_id).replace("\n", " ").strip()
    return (
        f"[//]: # (report:block:start {safe})",
        f"[//]: # (report:block:end {safe})",
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


def _strip_block_markers(report_path: str | Path) -> None:
    """最终报告不保留内部 upsert marker，避免 Markdown 查看器显示实现细节。"""
    path = Path(report_path)
    if not path.exists():
        return
    marker_re = re.compile(
        r"^\s*(?:<!--\s*report:block:(?:start|end)\s+.*?-->|"
        r"\[//\]:\s*#\s*\(report:block:(?:start|end)\s+.*?\))\s*$"
    )
    text = path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not marker_re.match(line)]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def init_report(report_path: str | Path, task_context: Dict[str, Any]) -> str:
    """初始化 REPORT.md。重复调用不会清空已有 step block。"""
    path = Path(report_path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        asset = task_context.get("asset_name") or "untitled"
        anchor_start, anchor_end = _block_markers("execution_anchor")
        path.write_text(
            f"# {asset} Task Report\n\n{anchor_start}\n## Execution\n{anchor_end}\n",
            encoding="utf-8",
        )
    upsert_block(path, "header", render_header(task_context, "RUNNING"),
                 before_block_id="execution_anchor")
    upsert_block(path, "final", render_final(task_context, "RUNNING"),
                 before_block_id="__never__")
    return str(path)


def render_header(task_context: Dict[str, Any], status: str) -> str:
    lines = [
        "| Field | Value |",
        "|---|---|",
        f"| Status | {_status_icon(status)} {status} |",
        f"| Task ID | `{_escape_md(task_context.get('task_id', ''))}` |",
        f"| Asset | {_escape_md(task_context.get('asset_name', 'untitled'))} |",
    ]
    if task_context.get("project"):
        lines.append(f"| Project | {_escape_md(task_context.get('project'))} |")
    if task_context.get("workflow_id"):
        lines.append(f"| Workflow | `{_escape_md(task_context.get('workflow_id'))}` |")
    if task_context.get("source_path"):
        lines.append(f"| Source File | {_fmt_path(task_context.get('source_path'))} |")
    if task_context.get("run_dir"):
        lines.append(f"| Sandbox | {_fmt_path(task_context.get('run_dir'))} |")
    lines.append(f"| Updated At | {now_str()} |")
    return "\n".join(lines)


def render_final(task_context: Dict[str, Any], status: str,
                 elapsed_min: Optional[float] = None,
                 error: str = "",
                 traceback_text: str = "") -> str:
    lines = [
        "## Final Status",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Status | {_status_icon(status)} {status} |",
        f"| Updated At | {now_str()} |",
    ]
    if elapsed_min is not None:
        lines.append(f"| Total Elapsed | {_format_elapsed(elapsed_min)} |")
    if task_context.get("run_dir"):
        lines.append(f"| Sandbox | {_fmt_cell_path(task_context.get('run_dir'))} |")
    if error:
        lines.extend(["", "**Error**", "", "```text", str(error), "```"])
    if traceback_text:
        lines.extend(["", "**Traceback**", "", "```text", str(traceback_text), "```"])
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
    _strip_block_markers(report_path)


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
    summary_line = f"Step {idx}/{total} | {label} | RUNNING | -"
    return _wrap_summary_body(summary_line, [])


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
    elapsed_sec = receipt.get("elapsed_sec")
    if not isinstance(elapsed_sec, (int, float)):
        elapsed_min = receipt.get("elapsed_min")
        elapsed_sec = float(elapsed_min) * 60.0 if isinstance(elapsed_min, (int, float)) else None
    elapsed_text = _fmt_elapsed_sec(elapsed_sec)

    outputs = receipt.get("output") if isinstance(receipt.get("output"), dict) else None
    if outputs is None:
        outputs = receipt.get("outputs", {}) or {}
    summary_line = f"Step {idx}/{total} | {label} | {status} | {elapsed_text}"
    body_lines: List[str] = []

    detail_lines: List[str] = []
    detail_lines.extend(_render_output_field_details(outputs))
    if not detail_lines:
        detail_lines.extend(_render_items_details(receipt.get("items")))
    if detail_lines:
        body_lines.extend(["", "#### Details"])
        body_lines.extend(detail_lines)

    error = receipt.get("error", "")
    tb = receipt.get("traceback", "") or receipt.get("traceback_text", "")
    if error or tb or (status not in ("SUCCESS", "RUNNING") and raw_detail):
        body_lines.extend(["", "#### Error Detail"])
        if error:
            body_lines.extend(_render_md_pre(str(error)))
        if tb:
            body_lines.extend(["#### Traceback", *_render_md_pre(str(tb))])
        elif raw_detail and status != "SUCCESS" and not receipt.get("_parsed", True):
            body_lines.extend(["#### Raw Detail", *_render_md_pre(str(raw_detail))])

    if raw_detail and not receipt.get("_parsed", True) and status == "SUCCESS":
        body_lines.extend([
            "",
            "#### Raw Detail",
            *_render_md_pre(str(raw_detail)),
        ])

    return _wrap_summary_body(summary_line, body_lines)


def upsert_step_finished(report_path: str | Path, step_context: Dict[str, Any],
                         receipt: Dict[str, Any], worker_status: str = "",
                         memory_gb: Optional[float] = None,
                         raw_detail: str = "") -> None:
    upsert_block(
        report_path,
        _step_block_id(step_context),
        render_step_finished(step_context, receipt, worker_status, memory_gb, raw_detail),
    )


def extract_receipt(detail: Any, skill_id: str = "", status: str = "") -> Dict[str, Any]:
    """把 worker wrapper detail 或直接 receipt 统一转成 receipt-like dict。"""
    def _normalize(rc: Dict[str, Any]) -> Dict[str, Any]:
        rc = dict(rc)
        rc.setdefault("skill_id", skill_id or rc.get("skill_id") or rc.get("skill") or "unknown")
        rc.setdefault("skill", rc.get("skill_id", "unknown"))
        rc.setdefault("status", status or rc.get("status", "UNKNOWN"))
        if not isinstance(rc.get("output"), dict):
            rc["output"] = rc.get("outputs", {}) if isinstance(rc.get("outputs"), dict) else {}
        rc.setdefault("outputs", rc.get("output", {}))
        if not isinstance(rc.get("input"), dict):
            rc["input"] = {}
        if not isinstance(rc.get("elapsed_sec"), (int, float)):
            elapsed_min = rc.get("elapsed_min")
            if isinstance(elapsed_min, (int, float)):
                rc["elapsed_sec"] = round(float(elapsed_min) * 60.0, 3)
        rc["_parsed"] = True
        return rc

    raw = detail
    if isinstance(detail, dict):
        if any(k in detail for k in ("summary", "outputs", "output", "skill_id", "skill")):
            return _normalize(detail)
        raw = json.dumps(detail, ensure_ascii=False, default=str)

    if isinstance(detail, str) and detail:
        payload = detail
        if " [mem=" in payload:
            payload = payload.rsplit(" [mem=", 1)[0]
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return _normalize(parsed)
        except Exception:
            pass

    rc = {
        "skill_id": skill_id or "unknown",
        "status": status or "UNKNOWN",
        "elapsed_min": 0,
        "summary": {"action": "非标准返回"},
        "items": [],
        "input": {},
        "output": {},
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
        "skill": skill_id,
        "skill_id": skill_id,
        "status": status,
        "elapsed_sec": 0,
        "elapsed_min": 0,
        "summary": {"action": "执行异常"},
        "items": [],
        "input": {},
        "output": {},
        "outputs": {},
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": _traceback.format_exc(),
    }
