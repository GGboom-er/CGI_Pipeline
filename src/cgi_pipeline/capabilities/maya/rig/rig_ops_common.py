"""Shared validation and transaction helpers for canonical Maya rig APIs."""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from cgi_pipeline.contracts import ApiContext, make_api_receipt


def cmds_from(context: ApiContext):
    cmds = context.extras.get("cmds_module")
    if cmds is None:
        raise RuntimeError("Maya execution context is required")
    return cmds


def receipt(
    context: ApiContext,
    fallback: str,
    started: float,
    params: dict[str, Any],
    error_code: str,
    recovery_hint: str,
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    api_id = str(context.extras.get("_api_id") or fallback)
    version = str(context.extras.get("_api_version") or "1.0.0")
    try:
        return make_api_receipt(
            api_id, version, "SUCCESS", started,
            input_data=dict(params), output=operation(),
        )
    except Exception as exc:
        return make_api_receipt(
            api_id, version, "ERROR", started,
            input_data=dict(params), error_code=error_code,
            error=str(exc), recovery_hint=recovery_hint,
        )


def started() -> float:
    return time.perf_counter()


def long_path(cmds, node: str) -> str:
    if not isinstance(node, str) or not node:
        raise ValueError("Node path must be a non-empty string")
    if any(token in node for token in "*?[]"):
        raise ValueError(f"Node path must be exact, not a pattern: {node}")
    matches = cmds.ls(node, long=True) or []
    if not matches:
        raise ValueError(f"Node does not exist: {node}")
    if len(matches) > 1 and not node.startswith("|"):
        raise ValueError(f"Node name is ambiguous; use a long path: {node}")
    return str(matches[0])


def mesh(cmds, node: str) -> tuple[str, str]:
    path = long_path(cmds, node)
    if cmds.nodeType(path) == "mesh":
        parents = cmds.listRelatives(path, parent=True, fullPath=True) or []
        if len(parents) != 1:
            raise ValueError(f"Mesh shape must have exactly one transform: {path}")
        return long_path(cmds, parents[0]), path
    shapes = cmds.listRelatives(path, shapes=True, noIntermediate=True, fullPath=True) or []
    meshes = cmds.ls(shapes, long=True, type="mesh") or []
    if len(meshes) != 1:
        raise ValueError(f"Expected exactly one non-intermediate mesh shape: {path}")
    return path, str(meshes[0])


def plug_path(cmds, plug: str) -> str:
    if not isinstance(plug, str) or "." not in plug:
        raise ValueError(f"Attribute must use node.attribute syntax: {plug}")
    node, attribute = plug.rsplit(".", 1)
    if not attribute or any(token in attribute for token in "*?[]"):
        raise ValueError(f"Attribute name must be exact: {plug}")
    resolved_node = long_path(cmds, node)
    requested = f"{resolved_node}.{attribute}"
    if not cmds.objExists(requested):
        raise ValueError(f"Attribute does not exist: {requested}")
    long_name = cmds.attributeQuery(attribute, node=resolved_node, longName=True)
    if not isinstance(long_name, str) or not long_name:
        raise RuntimeError(f"Could not resolve the long attribute name: {requested}")
    return f"{resolved_node}.{long_name}"


def finite(value: Any, name: str, *, minimum: float | None = None,
           maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return result


def integer(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def vector(value: Any, name: str, *, minimum: float | None = None,
           maximum: float | None = None) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must contain exactly three numbers")
    return [
        finite(item, f"{name}[{index}]", minimum=minimum, maximum=maximum)
        for index, item in enumerate(value)
    ]


def leaf_name(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a non-empty leaf name")
    if any(token in value for token in "|*?[]"):
        raise ValueError(f"{name} must be a literal leaf name without DAG or pattern tokens")
    return value


def undo_mutation(cmds, chunk_name: str, operation: Callable[[], Any],
                  verify: Callable[[Any], None] | None = None):
    opened = False
    try:
        cmds.undoInfo(openChunk=True, chunkName=chunk_name)
        opened = True
        result = operation()
        if verify is not None:
            verify(result)
        cmds.undoInfo(closeChunk=True)
        opened = False
        return result
    except Exception:
        if opened:
            cmds.undoInfo(closeChunk=True)
            cmds.undo()
        raise


def assert_close(expected: float, actual: Any, label: str, tolerance: float = 1e-7) -> float:
    value = finite(actual, label)
    if not math.isclose(expected, value, rel_tol=tolerance, abs_tol=tolerance):
        raise RuntimeError(f"{label} readback mismatch: expected {expected}, got {value}")
    return value
