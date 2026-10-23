"""Bounded generic Maya rig inspection and joint editing operations."""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any

from cgi_pipeline.contracts import ApiContext, make_api_receipt


_FRAMEWORK_HARD_MAX = 2000
_JOINT_HARD_MAX = 500
_PLANES = {"yz": "mirrorYZ", "xz": "mirrorXZ", "xy": "mirrorXY"}


def _identity(context: ApiContext, fallback: str) -> tuple[str, str]:
    return (
        str(context.extras.get("_api_id") or fallback),
        str(context.extras.get("_api_version") or "1.0.0"),
    )


def _cmds(context: ApiContext):
    cmds = context.extras.get("cmds_module")
    if cmds is None:
        raise RuntimeError("Maya execution context is required")
    return cmds


def _long(cmds, node: str) -> str:
    matches = cmds.ls(node, long=True) or []
    if not matches:
        raise ValueError(f"Node does not exist: {node}")
    if len(matches) > 1 and not str(node).startswith("|"):
        raise ValueError(f"Node name is ambiguous; use a long path: {node}")
    return str(matches[0])


def _validate_limit(value: Any, *, name: str, hard_max: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    result = value
    if result < 1 or result > hard_max:
        raise ValueError(f"{name} must be between 1 and {hard_max}")
    return result


def _root_nodes(cmds, roots: list[str]) -> list[str]:
    found: set[str] = set()
    for root in roots:
        if root == "|":
            found.update(str(node) for node in (cmds.ls(assemblies=True, long=True) or []))
            continue
        if any(token in root for token in "*?[]"):
            raise ValueError(f"Framework root must be exact, not a pattern: {root}")
        matches = cmds.ls(root, long=True) or []
        if not matches:
            raise ValueError(f"Framework root does not exist: {root}")
        if len(matches) > 1 and not root.startswith("|"):
            raise ValueError(f"Framework root is ambiguous; use a long path: {root}")
        found.add(str(matches[0]))
    return sorted(found)


def _bounded_dag(cmds, roots: list[str], max_nodes: int, max_depth: int) -> tuple[list[str], bool]:
    queue = [(node, 0) for node in _root_nodes(cmds, roots)]
    seen: set[str] = set()
    ordered: list[str] = []
    truncated = False
    while queue:
        node, depth = queue.pop(0)
        if node in seen:
            continue
        if len(ordered) >= max_nodes:
            truncated = True
            break
        seen.add(node)
        ordered.append(node)
        if depth >= max_depth:
            if cmds.listRelatives(node, children=True, fullPath=True) or []:
                truncated = True
            continue
        children = sorted(cmds.listRelatives(node, children=True, fullPath=True) or [])
        queue.extend((str(child), depth + 1) for child in children)
    return sorted(ordered), truncated or bool(queue)


def _role(cmds, node: str, node_type: str) -> str:
    if node_type == "joint":
        return "joint"
    if node_type != "transform":
        return "node"
    shapes = cmds.listRelatives(node, shapes=True, noIntermediate=True, fullPath=True) or []
    if any(cmds.nodeType(shape) == "nurbsCurve" for shape in shapes):
        return "control"
    return "transform"


def _frameworks(cmds, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: dict[str, set[str]] = {}
    try:
        hik_nodes = sorted(cmds.ls(type="HIKCharacterNode", long=True) or [])[:10]
    except Exception:
        hik_nodes = []
    for node in hik_nodes:
        evidence.setdefault("HumanIK", set()).add(f"node:{node}")
    for row in rows:
        node = row["path"]
        leaf = node.split("|")[-1].split(":")[-1]
        node_type = row["type"]
        lower_type = node_type.casefold()
        if "hikcharacter" in lower_type:
            evidence.setdefault("HumanIK", set()).add(f"node_type:{node_type}")
        if leaf in {"FitSkeleton", "MotionSystem"}:
            evidence.setdefault("AdvancedSkeleton", set()).add(f"node:{node}")
        for attr in ("isGearGuide", "comp_type", "rig_name"):
            if cmds.objExists(f"{node}.{attr}"):
                evidence.setdefault("mGear", set()).add(f"attribute:{node}.{attr}")
    return [
        {"name": name, "confidence": "high" if len(values) > 1 else "candidate",
         "evidence": sorted(values)}
        for name, values in sorted(evidence.items())
    ]


def inspect_framework(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter()
    api_id, version = _identity(context, "maya.rig.framework.inspect")
    try:
        cmds = _cmds(context)
        max_nodes = _validate_limit(
            params.get("max_nodes", 500), name="max_nodes", hard_max=_FRAMEWORK_HARD_MAX
        )
        max_depth = params.get("max_depth", 8)
        if isinstance(max_depth, bool) or not isinstance(max_depth, int):
            raise ValueError("max_depth must be an integer")
        if max_depth < 0 or max_depth > 64:
            raise ValueError("max_depth must be between 0 and 64")
        roots = params["roots"] if "roots" in params else ["|"]
        if not isinstance(roots, list) or not roots or not all(
            isinstance(root, str) and root for root in roots
        ):
            raise ValueError("roots must be a non-empty string array")
        if "|" in roots and len(roots) != 1:
            raise ValueError("world root | must be queried alone")
        nodes, truncated = _bounded_dag(cmds, roots, max_nodes, max_depth)
        rows = []
        for node in nodes:
            node_type = str(cmds.nodeType(node))
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            uuids = cmds.ls(node, uuid=True) or []
            rows.append({
                "path": node,
                "uuid": str(uuids[0]) if uuids else "",
                "type": node_type,
                "role": _role(cmds, node, node_type),
                "parent": str(parents[0]) if parents else "",
                "referenced": bool(cmds.referenceQuery(node, isNodeReferenced=True)),
            })
        edges = sorted(
            ({"parent": row["parent"], "child": row["path"]} for row in rows if row["parent"]),
            key=lambda row: (row["parent"], row["child"]),
        )
        snapshot = {"nodes": rows, "parent_edges": edges}
        digest = hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        output = {
            "roots": roots,
            "frameworks": _frameworks(cmds, rows),
            "snapshot": snapshot,
            "snapshot_hash": digest,
            "node_count": len(rows),
            "truncated": truncated,
        }
        return make_api_receipt(api_id, version, "SUCCESS", started,
                                input_data=dict(params), output=output)
    except Exception as exc:
        return make_api_receipt(
            api_id, version, "ERROR", started, input_data=dict(params),
            error_code="MAYA_RIG_FRAMEWORK_INSPECT_FAILED", error=str(exc),
            recovery_hint="Use existing unambiguous roots and reduce max_nodes or max_depth.",
        )


def _vector(value: Any, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must contain exactly three numbers")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError(f"{name} must contain exactly three numbers")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{name} must contain only finite numbers")
    return result


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _read_orient(cmds, node: str) -> list[float]:
    return [float(cmds.getAttr(f"{node}.jointOrient{axis}")) for axis in "XYZ"]


def _verify_values(expected: list[float], actual: list[float], label: str) -> None:
    if len(expected) != len(actual) or any(
        not math.isclose(left, right, rel_tol=1e-7, abs_tol=1e-7)
        for left, right in zip(expected, actual)
    ):
        raise RuntimeError(f"{label} readback mismatch: expected {expected}, got {actual}")


def _create_joint(cmds, item: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("name") or "")
    if not name or "|" in name:
        raise ValueError("create name must be a non-empty joint leaf name")
    kwargs = {"name": name}
    if item.get("parent"):
        kwargs["parent"] = _long(cmds, item["parent"])
    created = _long(cmds, cmds.createNode("joint", **kwargs))
    if "position" in item:
        cmds.xform(created, worldSpace=True, translation=_vector(item["position"], "position"))
    if "joint_orient" in item:
        orient = _vector(item["joint_orient"], "joint_orient")
        for axis, value in zip("XYZ", orient):
            cmds.setAttr(f"{created}.jointOrient{axis}", value)
        actual = _read_orient(cmds, created)
        _verify_values(orient, actual, "joint_orient")
    else:
        actual = _read_orient(cmds, created)
    return {"action": "create", "after": created, "joint_orient": actual}


def _set_orient(cmds, item: dict[str, Any]) -> dict[str, Any]:
    node = _long(cmds, item["node"])
    if cmds.nodeType(node) != "joint":
        raise ValueError(f"Node is not a joint: {node}")
    before = _read_orient(cmds, node)
    orient = _vector(item["joint_orient"], "joint_orient")
    for axis, value in zip("XYZ", orient):
        cmds.setAttr(f"{node}.jointOrient{axis}", value)
    actual = _read_orient(cmds, node)
    _verify_values(orient, actual, "joint_orient")
    return {"action": "set_orient", "path": node, "before": before, "after": actual}


def _set_limit(cmds, item: dict[str, Any]) -> dict[str, Any]:
    node = _long(cmds, item["node"])
    if cmds.nodeType(node) != "joint":
        raise ValueError(f"Node is not a joint: {node}")
    limits = item.get("limits")
    if not isinstance(limits, dict) or not limits:
        raise ValueError("limits must define at least one axis")
    applied = {}
    for axis, spec in sorted(limits.items()):
        if axis not in {"x", "y", "z"} or not isinstance(spec, dict):
            raise ValueError(f"Invalid rotation limit axis: {axis}")
        if "min" not in spec or "max" not in spec:
            raise ValueError(f"Rotation limits require min and max on axis {axis}")
        low = _finite_number(spec["min"], f"limits.{axis}.min")
        high = _finite_number(spec["max"], f"limits.{axis}.max")
        if low > high:
            raise ValueError(f"Rotation limit min exceeds max on axis {axis}")
        enabled = (spec.get("min_enabled", True), spec.get("max_enabled", True))
        if not all(isinstance(value, bool) for value in enabled):
            raise ValueError(f"Rotation limit enable flags must be boolean on axis {axis}")
        flag = f"rotation{axis.upper()}"
        enable_flag = f"enable{flag[0].upper()}{flag[1:]}"
        cmds.transformLimits(node, **{flag: (low, high), enable_flag: enabled})
        actual_range = list(cmds.transformLimits(node, query=True, **{flag: True}) or [])
        actual_enabled = list(cmds.transformLimits(node, query=True, **{enable_flag: True}) or [])
        _verify_values([low, high], [float(value) for value in actual_range], f"limits.{axis}")
        if actual_enabled != list(enabled):
            raise RuntimeError(
                f"limits.{axis} enable readback mismatch: expected {list(enabled)}, got {actual_enabled}"
            )
        applied[axis] = {"min": actual_range[0], "max": actual_range[1],
                         "min_enabled": actual_enabled[0], "max_enabled": actual_enabled[1]}
    return {"action": "set_limit", "path": node, "limits": applied}


def _mirror(cmds, item: dict[str, Any]) -> dict[str, Any]:
    root = _long(cmds, item["root"])
    if cmds.nodeType(root) != "joint":
        raise ValueError(f"Node is not a joint: {root}")
    search, replace = str(item.get("search") or ""), str(item.get("replace") or "")
    if not search or not replace or search == replace or any(token in search + replace for token in "|:"):
        raise ValueError("mirror search/replace must be distinct non-empty leaf-name literals without | or :")
    leaf = root.split("|")[-1]
    if search not in leaf:
        raise ValueError(f"mirror search literal is absent from root leaf name: {leaf}")
    plane = str(item.get("mirror_plane") or "yz").lower()
    if plane not in _PLANES:
        raise ValueError("mirror_plane must be one of: yz, xz, xy")
    mirror_behavior = item.get("mirror_behavior", True)
    if not isinstance(mirror_behavior, bool):
        raise ValueError("mirror_behavior must be boolean")
    kwargs = {_PLANES[plane]: True, "mirrorBehavior": mirror_behavior,
              "searchReplace": (search, replace)}
    created = sorted(str(node) for node in (cmds.mirrorJoint(root, **kwargs) or []))
    if not created:
        raise RuntimeError("mirrorJoint returned no created joints")
    return {"action": "mirror", "source": root,
            "created": [_long(cmds, node) for node in created], "mirror_plane": plane}


def manage_joints(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter()
    api_id, version = _identity(context, "maya.rig.joints.manage")
    cmds = None
    chunk_open = False
    try:
        cmds = _cmds(context)
        maximum = _validate_limit(
            params.get("max_items", 100), name="max_items", hard_max=_JOINT_HARD_MAX
        )
        items = list(params.get("items") or [])
        if not items:
            raise ValueError("items must contain at least one joint action")
        if len(items) > maximum:
            raise ValueError(f"items exceeds max_items ({len(items)} > {maximum})")
        cmds.undoInfo(openChunk=True, chunkName="maya.rig.joints.manage")
        chunk_open = True
        rows = []
        actions = {"create": _create_joint, "set_orient": _set_orient,
                   "set_limit": _set_limit, "mirror": _mirror}
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"items[{index}] must be an object")
            action = item.get("action")
            if action not in actions:
                raise ValueError(f"Unsupported joint action: {action}")
            rows.append(actions[action](cmds, item))
        cmds.undoInfo(closeChunk=True)
        chunk_open = False
        return make_api_receipt(
            api_id, version, "SUCCESS", started, input_data=dict(params),
            output={"items": rows, "count": len(rows)},
        )
    except Exception as exc:
        rollback_error = ""
        if cmds is not None and chunk_open:
            try:
                cmds.undoInfo(closeChunk=True)
                chunk_open = False
                cmds.undo()
            except Exception as rollback_exc:
                rollback_error = f"; rollback failed: {rollback_exc}"
        return make_api_receipt(
            api_id, version, "ERROR", started, input_data=dict(params),
            error_code="MAYA_RIG_JOINT_MANAGE_FAILED", error=f"{exc}{rollback_error}",
            recovery_hint="Use long joint paths, valid action fields, and a safe mirror search/replace pair.",
        )
