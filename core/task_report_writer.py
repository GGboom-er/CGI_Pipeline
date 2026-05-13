# -*- coding: utf-8 -*-
"""运行时任务报告写入器。

调度层在任务生命周期中调用本模块，把每个 step 的运行事实 upsert 到
同一个 REPORT.md。业务 skill 只返回 receipt，不直接决定报告文件形态。
"""

from __future__ import annotations

import contextlib
import datetime
import html
import json
import os
import re
import time
import traceback as _traceback
import base64
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
    "maya_assign_udim_materials": "maya_assign_udim_materials_to_cache",
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
    "report_sections",
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


def _format_report_scalar(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if _looks_like_path(value):
        return _fmt_path(value)
    return f"`{_escape_md(value)}`" if isinstance(value, str) else str(value)


def _format_report_value(value: Any) -> str:
    if isinstance(value, dict):
        return f"{len(value)} fields"
    if isinstance(value, list):
        if not value:
            return "0 items"
        if len(value) <= 6 and all(not isinstance(x, (dict, list)) for x in value):
            return ", ".join(_format_report_scalar(x) for x in value)
        return f"{len(value)} items"
    return _format_report_scalar(value)


def _normalize_report_io(values: Dict[str, Any], section: str) -> Dict[str, Any]:
    if not isinstance(values, dict):
        return {}
    result = values.get("result")
    if section == "output" and isinstance(result, dict) and len(values) <= 2:
        if result.get("source_path") or result.get("rig_path"):
            values = {
                key: result.get(key)
                for key in ("source_path", "rig_path", "source_version", "rig_version")
                if result.get(key) not in (None, "")
            }
        else:
            values = dict(result)
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
            elif len(values) == 1:
                values["scene_path"] = output_path
                values.pop("output_path", None)
    clean: Dict[str, Any] = {}
    for key, value in values.items():
        if _is_hidden_key(str(key), section):
            continue
        if key == "result" and isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if not _is_hidden_key(str(sub_key), section):
                    clean[str(sub_key)] = sub_value
            continue
        clean[str(key)] = _report_safe_value(value)
    return clean


def _render_io_list(title: str, values: Dict[str, Any], section: str) -> List[str]:
    clean = _normalize_report_io(values, section)
    lines = ["", f"**{title}**"]
    if not clean:
        lines.append("- none")
        return lines
    for key, value in clean.items():
        lines.append(f"- `{_escape_md(key)}`: {_format_report_value(value)}")
    return lines


def _render_markdown_table(headers: List[str], rows: List[List[Any]]) -> List[str]:
    lines = [
        "| " + " | ".join(_escape_cell(h) for h in headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        cells = []
        for value in row:
            if _looks_like_path(value):
                cells.append(_fmt_cell_path(value))
            else:
                cells.append(_escape_cell(value))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _html_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", " / ").replace("\n", " / ")
    if _looks_like_path(text):
        return f"<code>{html.escape(text)}</code>"
    return html.escape(text)


def _render_html_table(headers: List[str], rows: List[List[Any]]) -> List[str]:
    lines = ["<table>", "<thead>", "<tr>"]
    for header in headers:
        lines.append(f"<th>{html.escape(str(header))}</th>")
    lines.extend(["</tr>", "</thead>", "<tbody>"])
    for row in rows:
        lines.append("<tr>")
        for value in row:
            lines.append(f"<td>{_html_cell(value)}</td>")
        lines.append("</tr>")
    lines.extend(["</tbody>", "</table>"])
    return lines


def _detail_block(summary: str, body_lines: List[str], open_by_default: bool = False) -> List[str]:
    attr = " open" if open_by_default else ""
    return [
        "",
        f"<details markdown=\"1\"{attr}>",
        f"<summary>{_escape_md(summary)}</summary>",
        "",
        *body_lines,
        "",
        "</details>",
    ]


def _mesh_names_from_dags(dags: Iterable[Any]) -> str:
    names = [_short_dag(dag) for dag in dags or []]
    return ", ".join(names) if names else "-"


def _render_compare_group_details(compare_payload: Dict[str, Any]) -> List[str]:
    if not isinstance(compare_payload, dict):
        return []
    report = compare_payload.get("compare") if isinstance(compare_payload.get("compare"), dict) else compare_payload
    if not isinstance(report, dict):
        return []

    lines: List[str] = []
    paired = report.get("paired") if isinstance(report.get("paired"), list) else []
    only_source = report.get("only_a") if isinstance(report.get("only_a"), list) else []

    groups = report.get("pairing_groups") if isinstance(report.get("pairing_groups"), list) else []
    if groups:
        by_action: Dict[str, List[Dict[str, Any]]] = {}
        for group in groups:
            if not isinstance(group, dict):
                continue
            action = str(group.get("action") or "UNKNOWN")
            by_action.setdefault(action, []).append(group)
        for action in ("ORIG_INJECT", "PAIRED", "UNPAIRED", "IDENTICAL", "TARGET_ONLY"):
            action_groups = by_action.pop(action, [])
            if not action_groups:
                continue
            rows = []
            for group in action_groups:
                rows.append([
                    group.get("group_id", ""),
                    _mesh_names_from_dags(group.get("abc_dags") or []),
                    _mesh_names_from_dags(group.get("rig_dags") or []),
                    group.get("layer_name", ""),
                ])
            body = _render_html_table(
                ["group", "abc_mesh", "rig_mesh", "layer"],
                rows,
            )
            lines.extend(_detail_block(f"{action} ({len(action_groups)})", body))
        for action, action_groups in sorted(by_action.items()):
            rows = []
            for group in action_groups:
                rows.append([
                    group.get("group_id", ""),
                    _mesh_names_from_dags(group.get("abc_dags") or []),
                    _mesh_names_from_dags(group.get("rig_dags") or []),
                    group.get("layer_name", ""),
                ])
            lines.extend(_detail_block(
                f"{action} ({len(action_groups)})",
                _render_html_table(["group", "abc_mesh", "rig_mesh", "layer"], rows),
            ))

    if not groups and paired:
        by_action: Dict[str, List[Dict[str, Any]]] = {}
        for pair in paired:
            if not isinstance(pair, dict):
                continue
            action = str(pair.get("actionability") or "PAIRED")
            by_action.setdefault(action, []).append(pair)
        for action in ("IDENTICAL", "ORIG_INJECT", "MODIFIED", "MERGE", "SPLIT"):
            pairs = by_action.pop(action, [])
            if not pairs:
                continue
            rows = [[
                pair.get("name_a") or _short_dag(pair.get("dag_a")),
                pair.get("name_b") or _short_dag(pair.get("dag_b")),
            ] for pair in pairs]
            lines.extend(_detail_block(
                f"{action} ({len(pairs)})",
                _render_html_table(["abc_mesh", "rig_mesh"], rows),
            ))

    if not groups and only_source:
        rows = [[entry.get("name") or _short_dag(entry.get("dag")), entry.get("dag", "")]
                for entry in only_source if isinstance(entry, dict)]
        lines.extend(_detail_block(
            f"UNPAIRED ({len(rows)})",
            _render_html_table(["abc_mesh", "dag"], rows),
        ))

    only_target = report.get("only_b") if isinstance(report.get("only_b"), list) else []
    target_only_dags = report.get("target_only_dags") if isinstance(report.get("target_only_dags"), list) else []
    if only_target:
        rows = [[entry.get("name") or _short_dag(entry.get("dag")), entry.get("dag", "")]
                for entry in only_target if isinstance(entry, dict)]
    else:
        rows = [[_short_dag(dag), dag] for dag in target_only_dags]
    if rows:
        lines.extend(_detail_block(
            f"TARGET_ONLY ({len(rows)})",
            _render_html_table(["rig_mesh", "dag"], rows),
        ))
    return lines


def _render_structured_sections(sections: Any) -> List[str]:
    if not isinstance(sections, list):
        return []
    lines: List[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or section.get("name") or "details")
        summary = str(section.get("summary") or "")
        heading = f"{title} ({summary})" if summary else title
        body: List[str] = []
        if section.get("content"):
            body.extend(str(section.get("content")).splitlines())
            body.append("")
        if section.get("markdown"):
            body.extend(str(section.get("markdown")).splitlines())
            body.append("")
        items = section.get("items") or []
        if items:
            if all(isinstance(item, dict) for item in items):
                keys: List[str] = []
                for item in items:
                    for key in item.keys():
                        if key not in keys:
                            keys.append(key)
                body.extend(_render_html_table(keys, [[item.get(key, "") for key in keys] for item in items]))
            else:
                body.extend(f"- {_escape_md(item)}" for item in items)
        if not body:
            body = ["- none"]
        lines.extend(_detail_block(heading, body))
    return lines


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
    return _detail_block(
        f"ITEMS ({len(rows)})",
        _render_html_table(["name", "detail", "elapsed"], rows),
    )


def _render_output_field_details(outputs: Dict[str, Any]) -> List[str]:
    clean = _normalize_report_io(outputs, "output")
    lines: List[str] = []
    for key, value in clean.items():
        if isinstance(value, list) and value:
            if all(isinstance(item, dict) for item in value):
                keys: List[str] = []
                for item in value:
                    for item_key in item.keys():
                        if item_key not in keys:
                            keys.append(item_key)
                rows = [[item.get(item_key, "") for item_key in keys] for item in value]
                body = _render_html_table(keys, rows)
            else:
                body = [f"- {_format_report_scalar(item)}" for item in value]
            lines.extend(_detail_block(f"{key} ({len(value)})", body))
        elif isinstance(value, dict) and value:
            rows = [[sub_key, _format_report_value(sub_value)] for sub_key, sub_value in value.items()]
            lines.extend(_detail_block(
                f"{key} ({len(value)} fields)",
                _render_html_table(["field", "value"], rows),
            ))
    return lines


def _short_json(value: Any, max_chars: int = 6000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = str(value)
    if len(text) > max_chars:
        return text[:max_chars] + f"\n... 已截断 {len(text) - max_chars} 字符"
    return text


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
        return [_report_safe_value(item) for item in value[:20]]
    return value


def _data_marker(kind: str, records: Any) -> str:
    raw = json.dumps(records, ensure_ascii=False, separators=(",", ":"), default=str)
    payload = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return f"[//]: # (report:data:{kind}:{payload})"


def _read_data_marker(report_path: str | Path, kind: str) -> List[Dict[str, Any]]:
    path = Path(report_path)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"\[//\]: # \(report:data:" + re.escape(kind) + r":([A-Za-z0-9+/=]+)\)"
    )
    matches = pattern.findall(text)
    if not matches:
        return []
    try:
        decoded = base64.b64decode(matches[-1].encode("ascii")).decode("utf-8")
        data = json.loads(decoded)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [dict(x) for x in data if isinstance(x, dict)]


def _merge_records(existing: Iterable[Dict[str, Any]],
                   incoming: Iterable[Dict[str, Any]],
                   key_fields: Iterable[str]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    index: Dict[tuple, int] = {}
    fields = list(key_fields)
    for rec in list(existing or []) + list(incoming or []):
        if not isinstance(rec, dict):
            continue
        key = tuple(str(rec.get(field, "")) for field in fields)
        if not any(key):
            key = (json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str),)
        clean = dict(rec)
        if key in index:
            merged[index[key]].update(clean)
        else:
            index[key] = len(merged)
            merged.append(clean)
    return merged


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


def _role_node_param(record: Dict[str, Any]) -> tuple[str, str]:
    if record.get("node") or record.get("param"):
        return str(record.get("node") or "文件流转"), str(record.get("param") or record.get("role") or "-")

    role = str(record.get("role") or "-")
    if role == "source_path":
        return "pipeline_stage_file_to_sandbox", "source_path"
    if role.startswith("input."):
        return "pipeline_stage_file_to_sandbox", role.split(".", 1)[1]
    if role.startswith("step."):
        parts = role.split(".")
        if len(parts) >= 3:
            return f"pipeline_stage_file_to_sandbox:{_display_skill_name(parts[1])}", ".".join(parts[2:])
    return "pipeline_stage_file_to_sandbox", role


def render_file_staged(records: Iterable[Dict[str, Any]]) -> str:
    records = list(records or [])
    lines = [
        "## File Staging",
        "",
        f"{len(records)} file/path records.",
        "",
        "| Node | Param | Input | Output | Status | Elapsed |",
        "|---|---|---|---|---|---|",
    ]
    for rec in records:
        node, param = _role_node_param(rec)
        if rec.get("status"):
            state = str(rec.get("status"))
        elif rec.get("skipped"):
            state = "ALREADY_IN_SANDBOX"
        elif rec.get("reused"):
            state = "REUSED"
        elif rec.get("error"):
            state = "ERROR"
        else:
            state = "STAGED"
        lines.append(
            f"| {_escape_cell(node)} | "
            f"`{_escape_cell(param)}` | "
            f"{_fmt_cell_path(rec.get('input', rec.get('origin', '')))} | "
            f"{_fmt_cell_path(rec.get('output', rec.get('sandbox', '')))} | "
            f"{_escape_cell(state)} | "
            f"{_fmt_elapsed_sec(rec.get('elapsed_sec'))} |"
        )
    lines.extend(["", _data_marker("file_staged", records)])
    return "\n".join(lines)


def upsert_file_staged(report_path: str | Path, records: Iterable[Dict[str, Any]]) -> None:
    merged = _merge_records(
        _read_data_marker(report_path, "file_staged"),
        records,
        ("role", "node", "param", "origin", "sandbox", "input", "output"),
    )
    upsert_block(report_path, "context:file_staged", render_file_staged(merged))


def _open_scene_node(source_path: str) -> str:
    lower = str(source_path or "").lower()
    if lower.endswith(".blend"):
        return "blender_open_scene"
    if lower.endswith((".ma", ".mb")):
        return "maya_open_scene"
    return "dcc_open_scene"


def render_open_scenes(records: Iterable[Dict[str, Any]]) -> str:
    records = list(records or [])
    lines = [
        "## Open Scene",
        "",
        f"{len(records)} DCC scene open records.",
        "",
        "| Node | Input | Output | Status | Elapsed |",
        "|---|---|---|---|---|",
    ]
    errors = []
    for rec in records:
        source_path = str(rec.get("source_path") or "")
        status = str(rec.get("status") or "UNKNOWN")
        lines.append(
            f"| {_escape_cell(_open_scene_node(source_path))} | "
            f"{_fmt_cell_path(source_path)} | current_dcc_session | "
            f"{_status_icon(status)} {status} | {_fmt_elapsed_sec(rec.get('elapsed_sec'))} |"
        )
        if rec.get("error"):
            errors.append((source_path, str(rec.get("error"))))
    for source_path, error in errors:
        lines.extend([
            "",
            f"**Open failed: {_escape_md(source_path)}**",
            "",
            "```text",
            error,
            "```",
        ])
    lines.extend(["", _data_marker("open_scene", records)])
    return "\n".join(lines)


def render_open_scene(source_path: str, status: str,
                      elapsed_sec: Optional[float] = None,
                      error: str = "") -> str:
    return render_open_scenes([{
        "source_path": source_path,
        "status": status,
        "elapsed_sec": elapsed_sec,
        "error": error,
    }])


def upsert_open_scene(report_path: str | Path, source_path: str, status: str,
                      elapsed_sec: Optional[float] = None,
                      error: str = "") -> None:
    merged = _merge_records(
        _read_data_marker(report_path, "open_scene"),
        [{
            "source_path": source_path,
            "status": status,
            "elapsed_sec": elapsed_sec,
            "error": error,
        }],
        ("source_path",),
    )
    upsert_block(report_path, "context:open_scene", render_open_scenes(merged))


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
        f"## Step {idx}/{total} | {label} | RUNNING | -",
        "",
        f"- `skill_id`: `{_escape_md(skill_id)}`",
        "- `status`: RUNNING",
    ]
    if step_context.get("segment") not in (None, "", -1):
        lines.append(f"- `segment`: {step_context.get('segment')}")
    if step_context.get("source_path"):
        lines.append(f"- `source_path`: {_fmt_path(step_context.get('source_path'))}")
    params = step_context.get("parameters", {}) or {}
    lines.extend(_render_io_list("Input", params, "input"))
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
    elapsed_sec = receipt.get("elapsed_sec")
    if not isinstance(elapsed_sec, (int, float)):
        elapsed_min = receipt.get("elapsed_min")
        elapsed_sec = float(elapsed_min) * 60.0 if isinstance(elapsed_min, (int, float)) else None
    elapsed_text = _fmt_elapsed_sec(elapsed_sec)

    standard_input = receipt.get("input") if isinstance(receipt.get("input"), dict) else {}
    if not standard_input:
        standard_input = {}
        if step_context.get("source_path"):
            standard_input["source_path"] = step_context.get("source_path")
        for key, value in (step_context.get("parameters", {}) or {}).items():
            if str(key).startswith("_"):
                continue
            standard_input[key] = value

    outputs = receipt.get("output") if isinstance(receipt.get("output"), dict) else None
    if outputs is None:
        outputs = receipt.get("outputs", {}) or {}
    summary_line = f"Step {idx}/{total} | {label} | {status} | {elapsed_text}"
    lines = [
        f"## {summary_line}",
        "",
        f"- `skill_id`: `{_escape_md(skill_id)}`",
        f"- `status`: {_status_icon(status)} {status}",
        f"- `elapsed`: {elapsed_text}",
    ]
    if memory_gb is not None and memory_gb >= 0:
        lines.append(f"- `memory_gb`: {memory_gb:.2f}")

    lines.extend(_render_io_list("Input", standard_input, "input"))
    lines.extend(_render_io_list("Output", outputs, "output"))

    detail_lines: List[str] = []
    has_report_sections = isinstance(receipt.get("report_sections"), list) and bool(receipt.get("report_sections"))
    if isinstance(outputs.get("compare_result"), dict):
        detail_lines.extend(_render_compare_group_details(outputs.get("compare_result")))
    else:
        if not has_report_sections:
            detail_lines.extend(_render_output_field_details(outputs))
        detail_lines.extend(_render_structured_sections(receipt.get("report_sections")))
    if not has_report_sections and not detail_lines:
        detail_lines.extend(_render_items_details(receipt.get("items")))
    if detail_lines:
        lines.extend(["", "**Details**"])
        lines.extend(detail_lines)

    error = receipt.get("error", "")
    tb = receipt.get("traceback", "") or receipt.get("traceback_text", "")
    if error or tb or (status not in ("SUCCESS", "RUNNING") and raw_detail):
        lines.extend(["", "**Error Detail**", ""])
        if error:
            lines.extend(["```text", str(error), "```", ""])
        if tb:
            lines.extend(["**Traceback**", "", "```text", str(tb), "```", ""])
        elif raw_detail and status != "SUCCESS":
            lines.extend(["**Raw Detail**", "", "```text", str(raw_detail), "```", ""])

    if raw_detail and not receipt.get("_parsed", True) and status == "SUCCESS":
        lines.extend([
            "",
            "**Raw Detail**",
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
        f"## Segment {idx} | {dcc} | {status} | {step_count} steps | {elapsed_text}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| DCC | `{_escape_cell(dcc)}` |",
        f"| Step Count | {step_count} |",
        f"| Status | {_status_icon(status)} {status} |",
        f"| Elapsed | {elapsed_text} |",
    ]
    if error:
        lines.extend(["", "```text", str(error), "```"])
    return "\n".join(lines)


def upsert_segment(report_path: str | Path, segment_context: Dict[str, Any],
                   status: str, elapsed_min: Optional[float] = None,
                   error: str = "") -> None:
    # Segment 是调度层概念，用户报告按 step 顺序阅读即可。
    # 保留函数给 core.tasks 调用，但新报告不再写 Segment 块。
    return None


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
