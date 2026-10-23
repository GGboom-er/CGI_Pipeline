"""Select Maya objects through an explicit, bounded operation contract."""

from __future__ import annotations

import time

import maya.cmds as cmds

from cgi_pipeline.core.receipt import make_receipt


def _execute_legacy(payload: dict) -> dict:
    started = time.time()
    params = payload.get("parameters") or {}
    values = params.get("objects", params.get("nodes", params.get("patterns", [])))
    values = [str(v) for v in (values or [])]
    mode = str(params.get("mode") or "replace").lower()
    maximum = int(params.get("max_items", 200))
    if maximum < 1 or maximum > 2000:
        return make_receipt("maya_select_objects", "ERROR", started,
                            input=params, error="max_items 必须在 1..2000 之间")
    if mode not in {"replace", "add", "remove", "clear"}:
        return make_receipt("maya_select_objects", "ERROR", started,
                            input=params, error=f"不支持的 mode: {mode}")
    try:
        before = list(cmds.ls(selection=True, long=True) or [])
        if mode == "clear":
            cmds.select(clear=True)
            resolved = []
        else:
            resolved, seen = [], set()
            for value in values:
                for node in cmds.ls(value, long=True) or []:
                    if node not in seen:
                        seen.add(node); resolved.append(node)
                        if len(resolved) >= maximum:
                            break
                if len(resolved) >= maximum:
                    break
            if not resolved and values:
                return make_receipt("maya_select_objects", "ERROR", started,
                                    input=params, error="没有解析到可选择的对象")
            if mode == "replace": cmds.select(resolved, replace=True)
            elif mode == "add": cmds.select(resolved, add=True)
            else: cmds.select(resolved, deselect=True)
        if params.get("frame"):
            cmds.viewFit(all=True) if not resolved else cmds.viewFit(resolved, all=False)
        after = list(cmds.ls(selection=True, long=True) or [])
        return make_receipt("maya_select_objects", "SUCCESS", started,
                            input=params,
                            output={"before": before, "after": after,
                                    "resolved": resolved, "mode": mode,
                                    "truncated": len(values) > maximum},
                            summary_action="select", summary_count=len(after))
    except Exception as exc:
        return make_receipt("maya_select_objects", "ERROR", started,
                            input=params, error=str(exc),
                            recovery_hint="检查对象模式、选择模式和当前 Maya 视图。")


def _execute_explicit(params: dict) -> dict:
    from cgi_pipeline.capabilities.maya import scene_view

    started = time.time()
    try:
        capsule = params.get("restore_view")
        if capsule:
            targets = params.get("objects") or params.get("nodes") or params.get("patterns")
            if targets or params.get("frame") or params.get("near_clip_plane") is not None:
                raise scene_view.ViewError("restore_view 不可与 objects/nodes/patterns、frame=true 或 near_clip_plane 组合。")
            output = scene_view.restore(cmds, params.get("panel"), capsule)
        else:
            output = scene_view.frame(cmds, params)
        return make_receipt("maya_select_objects", "SUCCESS", started, input=params, output=output,
                            summary_action="restore_view" if capsule else "select/frame",
                            summary_count=len(output.get("after") or []))
    except scene_view.ViewError as error:
        return make_receipt("maya_select_objects", "ERROR", started, input=params,
                            output=error.details, error=str(error), error_code=error.code,
                            recovery_hint="保留当前状态；按错误说明选择独立可写相机或重新观察。")
    except (RuntimeError, TypeError, ValueError, KeyError) as error:
        return make_receipt("maya_select_objects", "ERROR", started, input=params, error=str(error),
                            error_code="VIEW_CONTRACT_ERROR", recovery_hint="核对显式 panel、完整路径及原恢复 capsule。")


def execute(payload: dict) -> dict:
    params = payload.get("parameters") or {}
    if params.get("panel") or params.get("restore_view") or params.get("near_clip_plane") is not None:
        return _execute_explicit(params)
    return _execute_legacy(payload)
