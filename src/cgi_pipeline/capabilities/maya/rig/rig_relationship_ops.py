"""Canonical Maya constraints, driven-key, and IK relationship operations."""

from __future__ import annotations

import math
from typing import Any

from cgi_pipeline.capabilities.maya.rig.rig_ops_common import (
    assert_close,
    cmds_from,
    finite,
    leaf_name,
    long_path,
    plug_path,
    receipt,
    started,
    undo_mutation,
    vector,
)


_CONSTRAINTS = {
    "parent": ("parentConstraint", "parentConstraint"),
    "point": ("pointConstraint", "pointConstraint"),
    "orient": ("orientConstraint", "orientConstraint"),
    "scale": ("scaleConstraint", "scaleConstraint"),
    "aim": ("aimConstraint", "aimConstraint"),
    "pole_vector": ("poleVectorConstraint", "poleVectorConstraint"),
}
_CONSTRAINT_NODE_TYPES = {node_type for _command, node_type in _CONSTRAINTS.values()}
_TANGENTS = {"linear", "smooth", "flat", "step"}
_IK_SOLVERS = {"ikRPsolver", "ikSCsolver"}


def _constraint(cmds, node: str) -> tuple[str, str, Any]:
    path = long_path(cmds, node)
    node_type = str(cmds.nodeType(path))
    if node_type not in _CONSTRAINT_NODE_TYPES:
        raise ValueError(f"Node is not a supported constraint: {path}")
    kind = next(name for name, (_command, expected) in _CONSTRAINTS.items() if expected == node_type)
    return path, kind, getattr(cmds, _CONSTRAINTS[kind][0])


def _constraint_create(cmds, params: dict[str, Any]) -> dict[str, Any]:
    drivers = params.get("drivers")
    if isinstance(drivers, str):
        drivers = [drivers]
    if not isinstance(drivers, list) or not 1 <= len(drivers) <= 32:
        raise ValueError("drivers must contain 1 to 32 node paths")
    resolved = [long_path(cmds, node) for node in drivers]
    if len(set(resolved)) != len(resolved):
        raise ValueError("drivers must be unique")
    driven = long_path(cmds, params["driven"])
    if driven in resolved:
        raise ValueError("driven node cannot also be a driver")
    kind = params.get("constraint_type", "parent")
    if kind not in _CONSTRAINTS:
        raise ValueError("Unsupported constraint_type")
    if kind == "pole_vector" and len(resolved) != 1:
        raise ValueError("pole_vector requires exactly one driver")
    maintain = params.get("maintain_offset", True)
    if not isinstance(maintain, bool):
        raise ValueError("maintain_offset must be boolean")
    weight = finite(params.get("weight", 1.0), "weight", minimum=0.0)
    command = getattr(cmds, _CONSTRAINTS[kind][0])
    kwargs = {"weight": weight}
    if kind != "pole_vector":
        kwargs["maintainOffset"] = maintain
    if params.get("name"):
        kwargs["name"] = leaf_name(params["name"], "name")
    if kind == "aim":
        kwargs["aimVector"] = vector(params.get("aim_vector", [1, 0, 0]), "aim_vector")
        kwargs["upVector"] = vector(params.get("up_vector", [0, 1, 0]), "up_vector")

    def create():
        result = command(*(resolved + [driven]), **kwargs) or []
        if len(result) != 1:
            raise RuntimeError("Constraint command did not return exactly one node")
        return long_path(cmds, result[0])

    def verify(node):
        if cmds.nodeType(node) != _CONSTRAINTS[kind][1]:
            raise RuntimeError(f"Constraint type readback mismatch: {node}")
        targets = command(node, query=True, targetList=True) or []
        actual = [long_path(cmds, target) for target in targets]
        if actual != resolved:
            raise RuntimeError(f"Constraint target readback mismatch: expected {resolved}, got {actual}")

    node = undo_mutation(cmds, "maya.rig.constraints.manage", create, verify)
    return {"action": "create", "constraint": node, "constraint_type": kind,
            "drivers": resolved, "driven": driven, "maintain_offset": maintain, "weight": weight}


def _constraint_set_weight(cmds, params: dict[str, Any]) -> dict[str, Any]:
    node, kind, command = _constraint(cmds, params["constraint"])
    target = long_path(cmds, params["target"])
    targets = command(node, query=True, targetList=True) or []
    aliases = command(node, query=True, weightAliasList=True) or []
    if len(targets) != len(aliases):
        raise RuntimeError(f"Constraint target/weight alias count mismatch: {node}")
    resolved_targets = [long_path(cmds, item) for item in targets]
    if target not in resolved_targets:
        raise ValueError(f"Target is not connected to constraint: {target}")
    alias = str(aliases[resolved_targets.index(target)])
    plug = f"{node}.{alias}"
    value = finite(params["weight"], "weight", minimum=0.0)
    before = float(cmds.getAttr(plug))

    def mutate():
        cmds.setAttr(plug, value)

    def verify(_result):
        assert_close(value, cmds.getAttr(plug), "constraint weight")

    undo_mutation(cmds, "maya.rig.constraints.manage", mutate, verify)
    return {"action": "set_weight", "constraint": node, "constraint_type": kind,
            "target": target, "weight_alias": alias, "before": before, "after": value}


def manage_constraints(params: dict, context) -> dict:
    began = started()
    cmds = cmds_from(context)

    def run():
        action = params.get("action")
        if action == "create":
            return _constraint_create(cmds, params)
        if action == "set_weight":
            return _constraint_set_weight(cmds, params)
        if action == "delete":
            node, kind, _command = _constraint(cmds, params["constraint"])

            def mutate():
                cmds.delete(node)

            def verify(_result):
                if cmds.ls(node, long=True) or []:
                    raise RuntimeError(f"Constraint still exists after deletion: {node}")

            undo_mutation(cmds, "maya.rig.constraints.manage", mutate, verify)
            return {"action": action, "constraint_type": kind, "deleted": node}
        raise ValueError("action must be create, set_weight, or delete")

    return receipt(
        context, "maya.rig.constraints.manage", began, params,
        "MAYA_RIG_CONSTRAINTS_MANAGE_FAILED",
        "Use existing long paths, a supported constraint type, and a connected target.", run,
    )


def _normalize_plug(cmds, plug: str) -> str:
    try:
        return plug_path(cmds, plug)
    except ValueError:
        return plug


def _driven_curves(cmds, driver: str, driven: str) -> list[str]:
    curves = cmds.listConnections(
        driven, source=True, destination=False, type="animCurve"
    ) or []
    matches = []
    for curve in curves:
        path = long_path(cmds, curve)
        if not str(cmds.nodeType(path)).startswith("animCurve"):
            continue
        inputs = cmds.listConnections(
            f"{path}.input", source=True, destination=False, plugs=True
        ) or []
        if any(_normalize_plug(cmds, str(item)) == driver for item in inputs):
            matches.append(path)
    return sorted(set(matches))


def _curve_snapshot(cmds, curve: str) -> dict[str, Any]:
    drivers = cmds.listConnections(
        f"{curve}.input", source=True, destination=False, plugs=True
    ) or []
    driven = cmds.listConnections(
        f"{curve}.output", source=False, destination=True, plugs=True
    ) or []
    inputs = [float(value) for value in (cmds.keyframe(curve, query=True, floatChange=True) or [])]
    values = [float(value) for value in (cmds.keyframe(curve, query=True, valueChange=True) or [])]
    if len(inputs) != len(values) or any(not math.isfinite(value) for value in inputs + values):
        raise RuntimeError(f"Invalid driven-key curve data: {curve}")
    return {"curve": curve, "driver": str(drivers[0]) if drivers else "",
            "driven": str(driven[0]) if driven else "", "driver_values": inputs,
            "driven_values": values, "key_count": len(inputs)}


def _driven_create(cmds, params: dict[str, Any]) -> dict[str, Any]:
    driver = plug_path(cmds, params["driver_attr"])
    driven_raw = params.get("driven_attrs")
    if not isinstance(driven_raw, list) or not 1 <= len(driven_raw) <= 32:
        raise ValueError("driven_attrs must contain 1 to 32 attribute paths")
    driven = [plug_path(cmds, plug) for plug in driven_raw]
    if len(set(driven)) != len(driven) or driver in driven:
        raise ValueError("driver and driven attributes must be distinct and driven_attrs unique")
    driver_values = params.get("driver_values")
    if not isinstance(driver_values, list) or not 1 <= len(driver_values) <= 64:
        raise ValueError("driver_values must contain 1 to 64 values")
    driver_values = [finite(value, "driver_values") for value in driver_values]
    if any(right <= left for left, right in zip(driver_values, driver_values[1:])):
        raise ValueError("driver_values must be strictly increasing")
    rows = params.get("driven_values")
    if not isinstance(rows, list) or len(rows) != len(driver_values):
        raise ValueError("driven_values must have one row per driver value")
    values = []
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(driven):
            raise ValueError(f"driven_values[{index}] must have one value per driven attribute")
        values.append([finite(value, f"driven_values[{index}]") for value in row])
    tangent = params.get("tangent_type", "linear")
    if tangent not in _TANGENTS:
        raise ValueError("tangent_type must be linear, smooth, flat, or step")

    def mutate():
        for row_index, driver_value in enumerate(driver_values):
            for column, driven_plug in enumerate(driven):
                cmds.setDrivenKeyframe(
                    driven_plug, currentDriver=driver, driverValue=driver_value,
                    value=values[row_index][column], inTangentType=tangent,
                    outTangentType=tangent,
                )

    snapshots = []

    def verify(_result):
        for index, driven_plug in enumerate(driven):
            curves = _driven_curves(cmds, driver, driven_plug)
            if len(curves) != 1:
                raise RuntimeError(f"Expected one driven-key curve for {driven_plug}, got {len(curves)}")
            row = _curve_snapshot(cmds, curves[0])
            if row["driver_values"] != driver_values:
                raise RuntimeError(f"Driven-key input readback mismatch: {driven_plug}")
            expected = [value_row[index] for value_row in values]
            if len(expected) != len(row["driven_values"]) or any(
                not math.isclose(left, right, rel_tol=1e-7, abs_tol=1e-7)
                for left, right in zip(expected, row["driven_values"])
            ):
                raise RuntimeError(f"Driven-key value readback mismatch: {driven_plug}")
            snapshots.append(row)

    undo_mutation(cmds, "maya.rig.driven_keys.manage", mutate, verify)
    return {"action": "create", "driver_attr": driver, "driven_attrs": driven,
            "curves": snapshots, "curve_count": len(snapshots),
            "keys_set": len(driver_values) * len(driven), "tangent_type": tangent}


def manage_driven_keys(params: dict, context) -> dict:
    began = started()
    cmds = cmds_from(context)

    def run():
        action = params.get("action")
        if action == "create":
            return _driven_create(cmds, params)
        driver = plug_path(cmds, params["driver_attr"])
        driven_raw = params.get("driven_attrs")
        if not isinstance(driven_raw, list) or not 1 <= len(driven_raw) <= 32:
            raise ValueError("driven_attrs must contain 1 to 32 attribute paths")
        driven = [plug_path(cmds, plug) for plug in driven_raw]
        rows = []
        for driven_plug in driven:
            rows.extend(_curve_snapshot(cmds, curve) for curve in _driven_curves(cmds, driver, driven_plug))
        unique = {row["curve"]: row for row in rows}
        rows = [unique[name] for name in sorted(unique)]
        if action == "query":
            return {"action": action, "driver_attr": driver, "driven_attrs": driven,
                    "curves": rows, "curve_count": len(rows)}
        if action == "remove":
            if not rows:
                raise ValueError("No driven-key relationships matched the requested attributes")
            nodes = [row["curve"] for row in rows]

            def mutate():
                cmds.delete(nodes)

            def verify(_result):
                for driven_plug in driven:
                    if _driven_curves(cmds, driver, driven_plug):
                        raise RuntimeError(f"Driven-key curve still exists for {driven_plug}")

            undo_mutation(cmds, "maya.rig.driven_keys.manage", mutate, verify)
            return {"action": action, "driver_attr": driver, "driven_attrs": driven,
                    "removed_curves": nodes, "curve_count": len(nodes)}
        raise ValueError("action must be create, query, or remove")

    return receipt(
        context, "maya.rig.driven_keys.manage", began, params,
        "MAYA_RIG_DRIVEN_KEYS_MANAGE_FAILED",
        "Use existing numeric plugs, a rectangular finite value matrix, and explicit attributes.", run,
    )


def _ik_handle(cmds, node: str) -> str:
    path = long_path(cmds, node)
    if cmds.nodeType(path) != "ikHandle":
        raise ValueError(f"Node is not an ikHandle: {path}")
    return path


def manage_ik(params: dict, context) -> dict:
    began = started()
    cmds = cmds_from(context)

    def run():
        action = params.get("action")
        if action == "create":
            start_joint = long_path(cmds, params["start_joint"])
            end_joint = long_path(cmds, params["end_joint"])
            if start_joint == end_joint or any(
                cmds.nodeType(node) != "joint" for node in (start_joint, end_joint)
            ):
                raise ValueError("start_joint and end_joint must be distinct joints")
            descendants = cmds.listRelatives(
                start_joint, allDescendents=True, fullPath=True, type="joint"
            ) or []
            if end_joint not in [long_path(cmds, node) for node in descendants]:
                raise ValueError("end_joint must be a descendant of start_joint")
            solver = params.get("solver", "ikRPsolver")
            if solver not in _IK_SOLVERS:
                raise ValueError("solver must be ikRPsolver or ikSCsolver")
            kwargs = {"startJoint": start_joint, "endEffector": end_joint, "solver": solver}
            if params.get("name"):
                kwargs["name"] = leaf_name(params["name"], "name")

            def create():
                result = cmds.ikHandle(**kwargs) or []
                if len(result) < 2:
                    raise RuntimeError("ikHandle did not return a handle and effector")
                return long_path(cmds, result[0]), long_path(cmds, result[1])

            def verify(nodes):
                if cmds.nodeType(nodes[0]) != "ikHandle" or cmds.nodeType(nodes[1]) != "ikEffector":
                    raise RuntimeError("IK handle/effector type readback mismatch")

            handle, effector = undo_mutation(cmds, "maya.rig.ik.manage", create, verify)
            return {"action": action, "handle": handle, "effector": effector,
                    "start_joint": start_joint, "end_joint": end_joint, "solver": solver}
        if action == "set_blend":
            handle = _ik_handle(cmds, params["ik_handle"])
            attribute = leaf_name(params.get("attribute", "ikBlend"), "attribute")
            plug = plug_path(cmds, f"{handle}.{attribute}")
            value = finite(params.get("blend", 1.0), "blend", minimum=0.0, maximum=1.0)
            before = float(cmds.getAttr(plug))

            def mutate():
                cmds.setAttr(plug, value)

            def verify(_result):
                assert_close(value, cmds.getAttr(plug), "IK blend")

            undo_mutation(cmds, "maya.rig.ik.manage", mutate, verify)
            return {"action": action, "ik_handle": handle, "attribute": attribute,
                    "before": before, "after": value}
        if action == "delete":
            handle = _ik_handle(cmds, params["ik_handle"])

            def mutate():
                cmds.delete(handle)

            def verify(_result):
                if cmds.ls(handle, long=True) or []:
                    raise RuntimeError(f"IK handle still exists after deletion: {handle}")

            undo_mutation(cmds, "maya.rig.ik.manage", mutate, verify)
            return {"action": action, "deleted": handle}
        raise ValueError("action must be create, set_blend, or delete")

    return receipt(
        context, "maya.rig.ik.manage", began, params,
        "MAYA_RIG_IK_MANAGE_FAILED",
        "Use a valid joint chain or existing ikHandle and a blend value from 0 to 1.", run,
    )
