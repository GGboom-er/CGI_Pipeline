"""Bounded Maya animation-curve inspection and tangent editing."""

from __future__ import annotations

import time
from typing import Any

from cgi_pipeline.capabilities.maya.animation_core import (
    _cmds, _limit, _long, _mutate, _number, _receipt,
)
from cgi_pipeline.contracts import ApiContext


_MAX_TARGETS = 65536
_MAX_KEYS = 65536
_TANGENT_TYPES = {"auto", "clamped", "flat", "linear", "plateau", "spline", "step", "stepnext"}


def _targets(cmds, params: dict) -> list[tuple[str, str, str]]:
    maximum = _limit(params)
    raw_nodes = params.get("nodes") or []
    attributes = params.get("attributes") or []
    if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= maximum:
        raise ValueError("nodes must be a non-empty array within max_items")
    if not isinstance(attributes, list) or not 1 <= len(attributes) <= maximum:
        raise ValueError("attributes must be a non-empty array within max_items")
    if not all(isinstance(value, str) and value and not value.startswith(".") for value in attributes):
        raise ValueError("attributes must contain non-empty relative plug names")
    if len(set(attributes)) != len(attributes):
        raise ValueError("attributes must be unique")
    nodes = [_long(cmds, node) for node in raw_nodes]
    if len(set(nodes)) != len(nodes):
        raise ValueError("nodes must be unique")
    if len(nodes) * len(attributes) > _MAX_TARGETS:
        raise ValueError(f"nodes times attributes must not exceed {_MAX_TARGETS}")
    rows = []
    for node in nodes:
        for attribute in attributes:
            plug = f"{node}.{attribute}"
            if not cmds.objExists(plug):
                raise ValueError(f"Attribute does not exist: {plug}")
            rows.append((node, attribute, plug))
    return rows


def _time_filter(params: dict, *, required: bool) -> tuple[tuple[float, float] | None, list[float]]:
    start = params.get("start_time")
    end = params.get("end_time")
    if (start is None) != (end is None):
        raise ValueError("start_time and end_time must be supplied together")
    time_range = None
    if start is not None:
        time_range = (_number(start, "start_time"), _number(end, "end_time"))
        if time_range[0] > time_range[1]:
            raise ValueError("start_time must not exceed end_time")
    raw_times = params.get("times") or []
    if not isinstance(raw_times, list):
        raise ValueError("times must be an array")
    times = [_number(value, "times") for value in raw_times]
    if len(set(times)) != len(times):
        raise ValueError("times must be unique")
    if time_range and times:
        raise ValueError("Use either a time range or explicit times, not both")
    if required and not time_range and not times:
        raise ValueError("Tangent edits require a time range or explicit times")
    return time_range, sorted(times)


def _curves(cmds, plug: str) -> list[str]:
    names = cmds.keyframe(plug, query=True, name=True) or []
    curves = [_long(cmds, name) for name in names]
    if len(set(curves)) != len(curves):
        raise RuntimeError(f"Duplicate animation curves returned for: {plug}")
    if any(not str(cmds.nodeType(curve)).startswith("animCurve") for curve in curves):
        raise RuntimeError(f"Non-animation curve returned for: {plug}")
    return sorted(curves)


def _curve_keys(cmds, curve: str, time_range: tuple[float, float] | None,
                times: list[float]) -> list[dict[str, Any]]:
    windows = [(value, value) for value in times] if times else [time_range]
    rows: dict[float, dict[str, Any]] = {}
    for window in windows:
        kwargs = {"time": window} if window else {}
        count = int(cmds.keyframe(curve, query=True, keyframeCount=True, **kwargs) or 0)
        if count <= 0:
            continue
        key_times = list(cmds.keyframe(curve, query=True, timeChange=True, **kwargs) or [])
        values = list(cmds.keyframe(curve, query=True, valueChange=True, **kwargs) or [])
        incoming = list(cmds.keyTangent(curve, query=True, inTangentType=True, **kwargs) or [])
        outgoing = list(cmds.keyTangent(curve, query=True, outTangentType=True, **kwargs) or [])
        if not (len(key_times) == len(values) == len(incoming) == len(outgoing) == count):
            raise RuntimeError(f"Animation-curve readback length mismatch: {curve}")
        for frame, value, in_type, out_type in zip(key_times, values, incoming, outgoing):
            frame = float(frame)
            rows[frame] = {
                "time": frame, "value": float(value),
                "in_tangent": str(in_type), "out_tangent": str(out_type),
            }
    return [rows[frame] for frame in sorted(rows)]


def _inspect(cmds, targets: list[tuple[str, str, str]],
             time_range: tuple[float, float] | None, times: list[float],
             max_keys: int, *, truncate: bool) -> dict[str, Any]:
    items = []
    total = 0
    visible = 0
    truncated = False
    for node, attribute, plug in targets:
        curve_rows = []
        for curve in _curves(cmds, plug):
            keys = _curve_keys(cmds, curve, time_range, times)
            total += len(keys)
            if total > _MAX_KEYS:
                raise ValueError(f"matched key count exceeds hard limit {_MAX_KEYS}")
            if not truncate and total > max_keys:
                raise ValueError("affected key count exceeds max_keys")
            room = max(max_keys - visible, 0)
            shown = keys[:room] if truncate else keys
            visible += len(shown)
            truncated = truncated or len(shown) < len(keys)
            curve_rows.append({"curve": curve, "keys": shown, "key_count": len(keys)})
        items.append({"node": node, "attribute": attribute, "plug": plug, "curves": curve_rows})
    return {"items": items, "key_count": total, "returned_key_count": visible,
            "truncated": truncated}


def manage_curves(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter()
    cmds = _cmds(context)

    def run():
        action = params.get("action")
        targets = _targets(cmds, params)
        time_range, times = _time_filter(params, required=action == "set_tangent_types")
        max_keys = params.get("max_keys", 2000)
        if isinstance(max_keys, bool) or not isinstance(max_keys, int) or not 1 <= max_keys <= _MAX_KEYS:
            raise ValueError(f"max_keys must be between 1 and {_MAX_KEYS}")
        if action == "query":
            return {"action": action, **_inspect(
                cmds, targets, time_range, times, max_keys, truncate=True)}
        if action != "set_tangent_types":
            raise ValueError(f"Unsupported curve action: {action}")
        in_type = params.get("in_tangent_type") or ""
        out_type = params.get("out_tangent_type") or ""
        if not in_type and not out_type:
            raise ValueError("At least one tangent type is required")
        for value in (in_type, out_type):
            if value and value not in _TANGENT_TYPES:
                raise ValueError(f"Unsupported tangent type: {value}")
        before = _inspect(cmds, targets, time_range, times, max_keys, truncate=False)
        if before["key_count"] <= 0:
            raise ValueError("No keys matched the tangent edit")
        empty = [item["plug"] for item in before["items"]
                 if not any(curve["key_count"] for curve in item["curves"])]
        if empty:
            raise ValueError(f"No keys matched the tangent edit on: {empty[0]}")

        def apply():
            changed_curves = 0
            windows = [(value, value) for value in times] if times else [time_range]
            for _node, _attribute, plug in targets:
                for curve in _curves(cmds, plug):
                    for window in windows:
                        kwargs = {"edit": True, "time": window}
                        if in_type:
                            kwargs["inTangentType"] = in_type
                        if out_type:
                            kwargs["outTangentType"] = out_type
                        changed = int(cmds.keyTangent(curve, **kwargs) or 0)
                        if changed <= 0:
                            raise RuntimeError(f"Maya did not edit tangent types on: {curve}")
                        changed_curves += changed
            after = _inspect(cmds, targets, time_range, times, max_keys, truncate=False)
            for item in after["items"]:
                for curve in item["curves"]:
                    for key in curve["keys"]:
                        if in_type and key["in_tangent"] != in_type:
                            raise RuntimeError(f"Incoming tangent verification failed: {curve['curve']}")
                        if out_type and key["out_tangent"] != out_type:
                            raise RuntimeError(f"Outgoing tangent verification failed: {curve['curve']}")
            return changed_curves, after

        changed_curves, after = _mutate(cmds, "cgi_animation_curve_tangents", apply)
        return {"action": action, "changed_curves": changed_curves,
                "before_key_count": before["key_count"], **after}

    return _receipt(context, "maya.animation.curves.manage", started, params, run)
