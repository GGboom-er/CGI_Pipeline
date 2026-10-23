"""Bounded Maya timeline and keyframe operations."""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from cgi_pipeline.contracts import ApiContext, make_api_receipt

_HARD_MAX = 2000
_MAX_KEY_TARGETS = 65536


def _cmds(context: ApiContext):
    cmds = context.extras.get("cmds_module")
    if cmds is None:
        raise RuntimeError("Maya execution context is required")
    return cmds


def _identity(context: ApiContext, fallback: str) -> tuple[str, str]:
    return (str(context.extras.get("_api_id") or fallback),
            str(context.extras.get("_api_version") or "1.0.0"))


def _receipt(context: ApiContext, fallback: str, started: float,
             params: dict[str, Any], operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    api_id, version = _identity(context, fallback)
    try:
        return make_api_receipt(api_id, version, "SUCCESS", started,
                                input_data=dict(params), output=operation())
    except Exception as exc:
        return make_api_receipt(
            api_id, version, "ERROR", started, input_data=dict(params),
            error_code="MAYA_ANIMATION_OPERATION_FAILED", error=str(exc),
            recovery_hint="Verify the action, long node paths, attributes, and frame values.",
        )


def _long(cmds, node: str) -> str:
    matches = cmds.ls(node, long=True) or []
    if not matches:
        raise ValueError(f"Node does not exist: {node}")
    if len(matches) > 1 and not str(node).startswith("|"):
        raise ValueError(f"Node name is ambiguous; use a long path: {node}")
    return str(matches[0])


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _limit(params: dict[str, Any]) -> int:
    value = params.get("max_items", 200)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _HARD_MAX:
        raise ValueError(f"max_items must be between 1 and {_HARD_MAX}")
    return value


def _timeline(cmds) -> dict[str, Any]:
    return {
        "current_frame": float(cmds.currentTime(query=True)),
        "playback_start": float(cmds.playbackOptions(query=True, minTime=True)),
        "playback_end": float(cmds.playbackOptions(query=True, maxTime=True)),
        "animation_start": float(cmds.playbackOptions(query=True, animationStartTime=True)),
        "animation_end": float(cmds.playbackOptions(query=True, animationEndTime=True)),
        "time_unit": str(cmds.currentUnit(query=True, time=True)),
    }


def _mutate(cmds, name: str, operation: Callable[[], Any]) -> Any:
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        result = operation()
        cmds.undoInfo(closeChunk=True)
        return result
    except Exception:
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
        raise


def manage_time(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)

    def run():
        action = params["action"]
        before = _timeline(cmds)
        if action == "query":
            return {"action": action, "timeline": before}
        if action == "set_current":
            frame = _number(params.get("current_frame"), "current_frame")
            try:
                cmds.currentTime(frame, update=True)
                timeline = _timeline(cmds)
            except Exception as exc:
                try:
                    cmds.currentTime(before["current_frame"], update=True)
                except Exception as restore_exc:
                    raise RuntimeError(
                        f"Current-frame update failed and restoring frame "
                        f"{before['current_frame']} also failed: {restore_exc}"
                    ) from exc
                raise
        elif action == "set_range":
            supplied = {name: params.get(name) for name in (
                "playback_start", "playback_end", "animation_start", "animation_end"
            )}
            if (supplied["playback_start"] is None) != (supplied["playback_end"] is None):
                raise ValueError("playback_start and playback_end must be supplied together")
            if (supplied["animation_start"] is None) != (supplied["animation_end"] is None):
                raise ValueError("animation_start and animation_end must be supplied together")
            if all(value is None for value in supplied.values()):
                raise ValueError("set_range requires a playback or animation range")
            values = {name: _number(value, name) for name, value in supplied.items() if value is not None}
            for start_name, end_name in (("playback_start", "playback_end"),
                                         ("animation_start", "animation_end")):
                if start_name in values and values[start_name] > values[end_name]:
                    raise ValueError(f"{start_name} must not exceed {end_name}")
            flags = {}
            aliases = {"playback_start": "minTime", "playback_end": "maxTime",
                       "animation_start": "animationStartTime", "animation_end": "animationEndTime"}
            for name, value in values.items():
                flags[aliases[name]] = value
            def apply_range():
                cmds.playbackOptions(**flags)
                return _timeline(cmds)
            timeline = _mutate(cmds, "cgi_animation_time", apply_range)
        else:
            raise ValueError(f"Unsupported time action: {action}")
        return {"action": action, "before": before, "timeline": timeline}

    return _receipt(context, "maya.animation.time.manage", started, params, run)


def manage_keyframes(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)

    def run():
        nodes = params.get("nodes") or []
        maximum = _limit(params)
        if not nodes:
            raise ValueError("nodes must contain at least one node")
        if len(nodes) > maximum:
            raise ValueError("nodes exceeds max_items")
        resolved = [_long(cmds, node) for node in nodes]
        if len(set(resolved)) != len(resolved):
            raise ValueError("nodes must be unique")
        attributes = params.get("attributes") or []
        if not all(isinstance(item, str) and item for item in attributes):
            raise ValueError("attributes must contain non-empty strings")
        if len(attributes) > maximum or len(set(attributes)) != len(attributes):
            raise ValueError("attributes must be unique and not exceed max_items")
        if attributes and len(resolved) * len(attributes) > _MAX_KEY_TARGETS:
            raise ValueError(f"nodes times attributes must not exceed {_MAX_KEY_TARGETS}")
        action = params["action"]
        start = params.get("start_time"); end = params.get("end_time")
        if start is not None:
            start = _number(start, "start_time")
        if end is not None:
            end = _number(end, "end_time")
        if start is not None and end is not None and start > end:
            raise ValueError("start_time must not exceed end_time")

        def query(node):
            kwargs = {"query": True, "timeChange": True}
            if attributes:
                kwargs["attribute"] = attributes
            if start is not None or end is not None:
                kwargs["time"] = (start if start is not None else end,
                                  end if end is not None else start)
            values = cmds.keyframe(node, **kwargs) or []
            times = sorted(set(float(value) for value in values))
            return {"node": node, "times": times, "count": len(times)}

        if action == "query":
            return {"action": action, "items": [query(node) for node in resolved], "count": len(resolved)}

        def apply():
            rows = []
            if action == "set":
                frame = params.get("time")
                frame = _number(frame, "time") if frame is not None else None
                value = params.get("value")
                if value is not None:
                    value = _number(value, "value")
                    if len(resolved) != 1 or len(attributes) != 1:
                        raise ValueError("value requires exactly one node and one attribute")
                    cmds.setAttr(f"{resolved[0]}.{attributes[0]}", value)
                for node in resolved:
                    kwargs = {}
                    if attributes:
                        kwargs["attribute"] = attributes
                    if frame is not None:
                        kwargs["time"] = frame
                    changed = int(cmds.setKeyframe(node, **kwargs) or 0)
                    if changed <= 0:
                        raise RuntimeError(f"Maya did not set a keyframe on: {node}")
                    rows.append({"node": node, "changed": changed})
            elif action == "delete":
                for node in resolved:
                    kwargs = {"clear": True}
                    if attributes:
                        kwargs["attribute"] = attributes
                    if start is not None or end is not None:
                        kwargs["time"] = (start if start is not None else end,
                                          end if end is not None else start)
                    rows.append({"node": node, "changed": int(cmds.cutKey(node, **kwargs) or 0)})
            else:
                raise ValueError(f"Unsupported keyframe action: {action}")
            return rows

        rows = _mutate(cmds, "cgi_animation_keyframes", apply)
        return {"action": action, "items": rows, "count": len(rows)}

    return _receipt(context, "maya.animation.keyframes.manage", started, params, run)
