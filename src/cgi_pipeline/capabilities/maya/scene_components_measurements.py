"""Bounded Maya component-selection and measurement operations."""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from cgi_pipeline.contracts import ApiContext, make_api_receipt


_HARD_MAX_COMPONENTS = 2000
_HARD_MAX_MEASUREMENTS = 200
_COMPONENT_FLAGS = {
    "vertex": {"toVertex": True},
    "edge": {"toEdge": True},
    "face": {"toFace": True},
    "uv": {"toUV": True},
    "vertex_face": {"toVertexFace": True},
}


def _cmds(context: ApiContext):
    cmds = context.extras.get("cmds_module")
    if cmds is None:
        raise RuntimeError("Maya execution context is required")
    return cmds


def _identity(context: ApiContext, fallback: str) -> tuple[str, str]:
    return (
        str(context.extras.get("_api_id") or fallback),
        str(context.extras.get("_api_version") or "1.0.0"),
    )


def _receipt(
    context: ApiContext,
    fallback: str,
    started: float,
    params: dict[str, Any],
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    api_id, version = _identity(context, fallback)
    try:
        return make_api_receipt(
            api_id, version, "SUCCESS", started,
            input_data=dict(params), output=operation(),
        )
    except Exception as exc:
        return make_api_receipt(
            api_id, version, "ERROR", started,
            input_data=dict(params),
            error_code="MAYA_SCENE_OPERATION_FAILED",
            error=str(exc),
            recovery_hint="Use explicit long paths, supported actions and smaller bounds.",
        )


def _bounded_limit(params: dict[str, Any], default: int, hard_max: int) -> int:
    maximum = params.get("max_items", default)
    if isinstance(maximum, bool) or not isinstance(maximum, int):
        raise ValueError("max_items must be an integer")
    if maximum < 1 or maximum > hard_max:
        raise ValueError(f"max_items must be between 1 and {hard_max}")
    return maximum


def _flatten(cmds, values: list[str]) -> list[str]:
    return list(cmds.ls(values, long=True, flatten=True) or [])


def _resolve_component_sources(cmds, values: list[Any]) -> list[str]:
    resolved: list[str] = []
    for raw_value in values:
        if not isinstance(raw_value, str) or not raw_value:
            raise ValueError("Each source must be a non-empty string")
        matches = list(cmds.ls(raw_value, long=True) or [])
        if not matches:
            raise ValueError(f"Source does not exist: {raw_value}")
        owners = {str(match).split(".", 1)[0] for match in matches}
        if len(owners) > 1 and not raw_value.startswith("|"):
            raise ValueError(
                f"Source name is ambiguous; use a long path: {raw_value}"
            )
        resolved.extend(_flatten(cmds, [str(match) for match in matches]))
    return list(dict.fromkeys(resolved))


def _restore_selection(cmds, before: list[str], original: Exception) -> None:
    try:
        if before:
            cmds.select(before, replace=True)
        else:
            cmds.select(clear=True)
    except Exception as restore_exc:
        raise RuntimeError(
            "Selection operation failed and restore also failed; "
            f"original={original}; restore={restore_exc}"
        ) from original
    raise RuntimeError(
        f"Selection operation failed; previous selection restored: {original}"
    ) from original


def select_components(params: dict[str, Any], context: ApiContext) -> dict[str, Any]:
    """Convert explicit scene items to components and update Maya selection."""
    started = time.perf_counter()
    cmds = _cmds(context)

    def run() -> dict[str, Any]:
        maximum = _bounded_limit(params, 500, _HARD_MAX_COMPONENTS)
        action = str(params.get("action") or "convert_select")
        before = list(cmds.ls(selection=True, long=True, flatten=True) or [])
        if action == "clear":
            try:
                cmds.select(clear=True)
                after = list(
                    cmds.ls(selection=True, long=True, flatten=True) or []
                )
                return {
                    "action": action, "before": before, "after": after,
                    "items": [], "count": 0, "truncated": False,
                    "recovery": {"selection": before},
                }
            except Exception as exc:
                _restore_selection(cmds, before, exc)
        if action != "convert_select":
            raise ValueError(f"Unsupported component action: {action}")

        source = params.get("source") or []
        if not isinstance(source, list) or not source:
            raise ValueError("source must be a non-empty array")
        resolved_source = _resolve_component_sources(cmds, source)

        component_type = str(params.get("component_type") or "vertex")
        flags = _COMPONENT_FLAGS.get(component_type)
        if flags is None:
            raise ValueError(f"Unsupported component_type: {component_type}")
        converted = cmds.polyListComponentConversion(resolved_source, **flags) or []
        flattened = _flatten(cmds, converted)
        if not flattened:
            raise ValueError("Component conversion returned no results")
        bounded = flattened[:maximum]
        mode = str(params.get("selection_mode") or "replace")
        if mode not in {"replace", "add", "remove"}:
            raise ValueError(f"Unsupported selection_mode: {mode}")
        try:
            if mode == "replace":
                cmds.select(bounded, replace=True)
            elif mode == "add":
                cmds.select(bounded, add=True)
            else:
                cmds.select(bounded, deselect=True)
            after = list(cmds.ls(selection=True, long=True, flatten=True) or [])
            return {
                "action": action,
                "component_type": component_type,
                "selection_mode": mode,
                "source": resolved_source,
                "items": bounded,
                "count": len(bounded),
                "truncated": len(flattened) > len(bounded),
                "before": before,
                "after": after,
                "recovery": {"selection": before},
            }
        except Exception as exc:
            _restore_selection(cmds, before, exc)

    return _receipt(context, "maya.scene.components.select", started, params, run)


def _long(cmds, value: str) -> str:
    matches = list(cmds.ls(value, long=True) or [])
    if not matches:
        raise ValueError(f"Node does not exist: {value}")
    if len(matches) > 1 and not str(value).startswith("|"):
        raise ValueError(f"Node name is ambiguous; use a long path: {value}")
    return str(matches[0])


def _point(cmds, value: Any) -> tuple[list[float], str]:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return _finite_vector(value, 3, "point"), "point"
    if isinstance(value, str) and value:
        node = _long(cmds, value)
        point = cmds.xform(
            node, query=True, worldSpace=True, rotatePivot=True
        ) or []
        if len(point) != 3:
            raise ValueError(f"Could not query world rotate pivot: {node}")
        return _finite_vector(point, 3, f"world rotate pivot for {node}"), node
    raise ValueError("Distance endpoints must be a node path or a three-number point")


def _finite_vector(value: Any, length: int, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{label} must contain exactly {length} numbers")
    result: list[float] = []
    for axis in value:
        if isinstance(axis, bool) or not isinstance(axis, (int, float)):
            raise ValueError(f"{label} must contain only finite numbers")
        number = float(axis)
        if not math.isfinite(number):
            raise ValueError(f"{label} must contain only finite numbers")
        result.append(number)
    return result


def inspect_measurements(params: dict[str, Any], context: ApiContext) -> dict[str, Any]:
    """Inspect bounded distances and world-space bounding boxes."""
    started = time.perf_counter()
    cmds = _cmds(context)

    def run() -> dict[str, Any]:
        maximum = _bounded_limit(params, 50, _HARD_MAX_MEASUREMENTS)
        requests = params.get("items") or []
        if not isinstance(requests, list) or not requests:
            raise ValueError("items must be a non-empty array")
        rows: list[dict[str, Any]] = []
        for item in requests[:maximum]:
            if not isinstance(item, dict):
                raise ValueError("Each measurement item must be an object")
            action = str(item.get("action") or "")
            if action == "distance":
                a, a_source = _point(cmds, item.get("a"))
                b, b_source = _point(cmds, item.get("b"))
                delta = _finite_vector(
                    [b[index] - a[index] for index in range(3)],
                    3, "distance delta",
                )
                distance = math.sqrt(sum(axis * axis for axis in delta))
                if not math.isfinite(distance):
                    raise ValueError("distance result must be finite")
                rows.append({
                    "action": action, "a": a, "b": b,
                    "a_source": a_source, "b_source": b_source,
                    "delta": delta,
                    "distance": distance,
                })
            elif action == "bounding_box":
                node = _long(cmds, str(item.get("node") or ""))
                raw_bbox = cmds.exactWorldBoundingBox(node) or []
                try:
                    bbox = _finite_vector(
                        raw_bbox, 6, f"world bounding box for {node}"
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"Could not query finite world bounding box: {node}; {exc}"
                    ) from exc
                low, high = bbox[:3], bbox[3:]
                center = _finite_vector(
                    [(low[i] + high[i]) / 2.0 for i in range(3)],
                    3, "bounding-box center",
                )
                size = _finite_vector(
                    [high[i] - low[i] for i in range(3)],
                    3, "bounding-box size",
                )
                rows.append({
                    "action": action, "node": node,
                    "min": low, "max": high,
                    "center": center,
                    "size": size,
                })
            else:
                raise ValueError(f"Unsupported measurement action: {action}")
        return {
            "items": rows,
            "count": len(rows),
            "truncated": len(requests) > len(rows),
            "space": "world",
        }

    return _receipt(context, "maya.scene.measurements.inspect", started, params, run)
