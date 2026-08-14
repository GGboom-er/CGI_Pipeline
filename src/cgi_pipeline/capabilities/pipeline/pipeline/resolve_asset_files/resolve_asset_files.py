# -*- coding: utf-8 -*-
"""资产文件解析节点。

本节点是 workflow 的输入适配层：只做路径解析和存在性校验，不复制文件、
不写中间产物、不启动 DCC。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from cgi_pipeline.core.asset_resolver import AssetResolver
from cgi_pipeline.core.config_loader import load_project_config
from cgi_pipeline.core.receipt import make_item, make_receipt


API_ID = "resolve_asset_files"
_VERSION_RE = re.compile(r"_v(\d{3,4})(?=\.|_|$)")


def _clean_optional(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.startswith("{{") and text.endswith("}}"):
        return ""
    return text


def _split_exts(value: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _clean_optional(value)
    if not raw:
        return default
    exts = []
    for part in raw.split(","):
        ext = part.strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        exts.append(ext)
    return tuple(exts or default)


def _version_num(path: Path) -> int:
    match = _VERSION_RE.search(path.name)
    return int(match.group(1)) if match else 0


def _as_existing_path(path_value: str, allowed_exts: tuple[str, ...], role: str) -> tuple[Path | None, str]:
    value = _clean_optional(path_value)
    if not value:
        return None, ""
    path = Path(value)
    if not path.exists():
        return None, f"{role} 显式路径不存在: {path}"
    if path.suffix.lower() not in allowed_exts:
        return None, f"{role} 显式路径扩展名不符合要求: {path.suffix}，允许 {', '.join(allowed_exts)}"
    return path, ""


def _find_latest_by_stage(
    resolver: AssetResolver,
    category: str,
    asset_name: str,
    stage: str,
    task: str,
    allowed_exts: tuple[str, ...],
) -> tuple[Path | None, list[dict]]:
    stage_dir = resolver._resolve_stage_dir(category, asset_name, stage, task or None)
    searched = [{"stage": stage, "task": task or resolver._get_primary_task(stage), "path": str(stage_dir), "exists": stage_dir.exists()}]
    if not stage_dir.exists():
        return None, searched

    candidates = []
    for item in stage_dir.iterdir():
        if not item.is_file() or item.suffix.lower() not in allowed_exts:
            continue
        candidates.append(item)
    if not candidates:
        return None, searched
    candidates.sort(key=lambda p: (_version_num(p), p.stat().st_mtime, p.name), reverse=True)
    return candidates[0], searched


def _result_for_path(
    path: Path,
    role: str,
    stage: str,
    task: str,
    explicit: bool,
) -> dict:
    prefix = "source" if role == "source" else "rig"
    return {
        f"{prefix}_path": str(path),
        f"{prefix}_stem": path.stem,
        f"{prefix}_stage": stage,
        f"{prefix}_task": task,
        f"{prefix}_version": _version_num(path),
        f"explicit_{prefix}": explicit,
    }


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get("parameters", {}) or {}
    extra = payload.get("extra_params", {}) or {}
    project = _clean_optional(payload.get("project")) or "default"
    asset_name = (
        _clean_optional(params.get("asset_name"))
        or _clean_optional(extra.get("asset_name"))
        or _clean_optional(payload.get("asset_name"))
    )

    category = _clean_optional(params.get("category")) or _clean_optional(extra.get("category")) or "chr"
    source_stage = _clean_optional(params.get("source_stage")) or _clean_optional(extra.get("source_stage")) or "tex"
    rig_stage = _clean_optional(params.get("rig_stage")) or _clean_optional(extra.get("rig_stage")) or "rig"
    source_task = _clean_optional(params.get("source_task")) or _clean_optional(extra.get("source_task"))
    rig_task = _clean_optional(params.get("rig_task")) or _clean_optional(extra.get("rig_task"))
    source_exts = _split_exts(params.get("source_extensions", ""), (".blend",))
    rig_exts = _split_exts(params.get("rig_extensions", ""), (".ma", ".mb"))

    explicit_source_value = (
        _clean_optional(params.get("source_path"))
        or _clean_optional(extra.get("source_path"))
        or _clean_optional(payload.get("source_path"))
    )
    explicit_rig_value = _clean_optional(params.get("rig_path")) or _clean_optional(extra.get("rig_path"))

    if not asset_name:
        return make_receipt(API_ID, "ERROR", t0, error="缺少 asset_name，无法解析资产文件")

    errors = []
    items = []
    report_items = []
    searched_paths = []
    missing_inputs = []

    source_path, err = _as_existing_path(explicit_source_value, source_exts, "source")
    if err:
        errors.append(err)
    rig_path, err = _as_existing_path(explicit_rig_value, rig_exts, "rig")
    if err:
        errors.append(err)

    try:
        config = load_project_config(project)
        resolver = AssetResolver(config)
    except Exception as exc:
        if not (source_path and rig_path):
            return make_receipt(
                API_ID,
                "ERROR",
                t0,
                error=f"读取项目配置失败，且未提供完整显式路径: {type(exc).__name__}: {exc}",
            )
        config = {}
        resolver = None

    if resolver and not source_path:
        source_path, searched = _find_latest_by_stage(
            resolver, category, asset_name, source_stage, source_task, source_exts
        )
        searched_paths.extend({"role": "source", **x} for x in searched)
        if not source_path:
            search = searched[-1] if searched else {}
            source_task_final = search.get("task") or source_task or resolver._get_primary_task(source_stage)
            errors.append(
                f"未找到 source 文件: project={project}, category={category}, asset={asset_name}, "
                f"stage={source_stage}, task={source_task or '<primary>'}, ext={','.join(source_exts)}"
            )
            missing_inputs.append({
                "role": "source",
                "stage": source_stage,
                "task": source_task_final,
                "path": str(search.get("path", "")),
                "exists": bool(search.get("exists", False)),
                "allowed_extensions": ",".join(source_exts),
            })

    if resolver and not rig_path:
        rig_path, searched = _find_latest_by_stage(
            resolver, category, asset_name, rig_stage, rig_task, rig_exts
        )
        searched_paths.extend({"role": "rig", **x} for x in searched)
        if not rig_path:
            search = searched[-1] if searched else {}
            rig_task_final = search.get("task") or rig_task or resolver._get_primary_task(rig_stage)
            errors.append(
                f"未找到 rig 文件: project={project}, category={category}, asset={asset_name}, "
                f"stage={rig_stage}, task={rig_task or '<primary>'}, ext={','.join(rig_exts)}"
            )
            missing_inputs.append({
                "role": "rig",
                "stage": rig_stage,
                "task": rig_task_final,
                "path": str(search.get("path", "")),
                "exists": bool(search.get("exists", False)),
                "allowed_extensions": ",".join(rig_exts),
            })

    if errors:
        result = {
            "project": project,
            "asset_name": asset_name,
            "category": category,
            "source_label": source_stage,
            "rig_label": rig_stage,
            "target_label": rig_stage,
            "missing_inputs": missing_inputs,
            "searched_paths": searched_paths,
            "errors": errors,
        }
        return make_receipt(
            API_ID,
            "ERROR",
            t0,
            input={
                "project": project,
                "asset_name": asset_name,
                "category": category,
                "source_stage": source_stage,
                "rig_stage": rig_stage,
            },
            output={
                "result": result,
                "missing_inputs": missing_inputs,
                "searched_paths": searched_paths,
                "error_count": len(errors),
            },
            summary_input=f"{project}/{category}/{asset_name}",
            summary_action="解析失败",
            summary_count=len(errors),
            summary_label="问题",
            items=[make_item("路径解析", e) for e in errors],
            error="\n".join(errors),
            recovery_hint="检查资产名、项目配置、服务器挂载，或显式传入 source_path/rig_path。",
        )

    assert source_path is not None
    assert rig_path is not None

    source_task_final = source_task or (resolver._get_primary_task(source_stage) if resolver else "")
    rig_task_final = rig_task or (resolver._get_primary_task(rig_stage) if resolver else "")

    result = {
        "project": project,
        "asset_name": asset_name,
        "category": category,
        "source_label": source_stage,
        "rig_label": rig_stage,
        "target_label": rig_stage,
        "searched_paths": searched_paths,
    }
    result.update(_result_for_path(source_path, "source", source_stage, source_task_final, bool(explicit_source_value)))
    result.update(_result_for_path(rig_path, "rig", rig_stage, rig_task_final, bool(explicit_rig_value)))

    items.append(make_item("source_path", str(source_path)))
    items.append(make_item("rig_path", str(rig_path)))
    report_items.extend([
        {
            "role": "source",
            "stage": source_stage,
            "task": source_task_final,
            "path": str(source_path),
            "version": result["source_version"],
            "explicit": result["explicit_source"],
        },
        {
            "role": "rig",
            "stage": rig_stage,
            "task": rig_task_final,
            "path": str(rig_path),
            "version": result["rig_version"],
            "explicit": result["explicit_rig"],
        },
    ])

    return make_receipt(
        api_id=API_ID,
        status="SUCCESS",
        start_time=t0,
        summary_input=f"{project}/{category}/{asset_name}",
        summary_action="解析 source/rig 文件",
        summary_count=2,
        summary_label="路径",
        items=items,
        output={
            "result": result,
            "resolved_files": report_items,
            "searched_paths": searched_paths,
        },
    )
