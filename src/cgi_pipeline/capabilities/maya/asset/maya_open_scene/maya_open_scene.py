"""Open Maya scenes only from CGI task sandboxes."""

from __future__ import annotations

import time
from pathlib import Path

from cgi_pipeline.core.path_guard import is_protected_path
from cgi_pipeline.core.receipt import make_receipt
from cgi_pipeline.paths import REPOSITORY_ROOT


_SCENE_SUFFIXES = {".ma", ".mb"}


def _maya_cmds():
    import maya.cmds as cmds

    return cmds


def _is_under_project_sandbox(path: Path) -> bool:
    resolved = path.resolve()
    root = REPOSITORY_ROOT.resolve()
    return resolved.is_relative_to(root / "projects") or resolved.is_relative_to(root / "runs")


def _scene_state(cmds) -> dict:
    return {
        "scene": cmds.file(query=True, sceneName=True) or "",
        "modified": bool(cmds.file(query=True, modified=True)),
        "mesh_count": len(cmds.ls(type="mesh", long=True) or []),
        "joint_count": len(cmds.ls(type="joint", long=True) or []),
    }


def _error(started_at: float, message: str) -> dict:
    return make_receipt(
        "maya_open_scene",
        "ERROR",
        started_at,
        summary_input="scene",
        error=message,
    )


def execute(payload: dict) -> dict:
    """Open one sandboxed Maya file, or create a new empty scene."""
    started_at = time.time()
    params = payload.get("parameters") or {}
    file_path = str(params.get("file_path") or payload.get("source_path") or "").strip()
    new_scene = bool(params.get("new_scene", False))
    force = bool(params.get("force", True))
    cmds = _maya_cmds()

    if new_scene:
        action = "已创建空 Maya 场景"
        target = "<new_scene>"
        try:
            cmds.file(new=True, force=force)
        except Exception as exc:
            return _error(started_at, str(exc))
    else:
        if not file_path:
            return _error(started_at, "file_path 不能为空，除非 new_scene=true。")

        target_path = Path(file_path)
        if not target_path.is_absolute():
            return _error(started_at, "file_path 必须是任务沙盒内的绝对路径。")
        if target_path.suffix.lower() not in _SCENE_SUFFIXES:
            return _error(started_at, "仅支持 .ma 和 .mb Maya 场景文件。")

        target_path = target_path.resolve()
        target = target_path.as_posix()
        if is_protected_path(target) or not _is_under_project_sandbox(target_path):
            return make_receipt(
                "maya_open_scene",
                "BLOCKED",
                started_at,
                summary_input=target_path.name,
                summary_action="打开被拦截 - 场景不在任务沙盒",
                error=f'场景 "{target}" 不在 CGI projects/ 或 runs/ 任务沙盒内。',
                output={"blocked_target": target},
            )
        if not target_path.is_file():
            return _error(started_at, f'场景文件不存在："{target}"。')

        action = "已打开 Maya 任务沙盒场景"
        try:
            cmds.file(target, open=True, force=force)
        except Exception as exc:
            return _error(started_at, str(exc))

    state = _scene_state(cmds)
    return make_receipt(
        "maya_open_scene",
        "SUCCESS",
        started_at,
        summary_input=target,
        summary_action=action,
        output=state,
    )
