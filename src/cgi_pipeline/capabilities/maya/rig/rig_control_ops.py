"""Create bounded animator controls and editable guide curves in Maya."""

from __future__ import annotations

import math
from typing import Any

from cgi_pipeline.capabilities.maya.rig.rig_ops_common import (
    cmds_from,
    finite,
    integer,
    leaf_name,
    long_path,
    receipt,
    started,
    undo_mutation,
    vector,
)


_SHAPES = {"circle", "square", "diamond", "cross", "cube"}
_LOCKABLE = {"tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz", "v"}


def _shape_points(shape: str, size: float) -> list[list[float]]:
    half = size * 0.5
    if shape == "square":
        return [[-half, 0, -half], [half, 0, -half], [half, 0, half],
                [-half, 0, half], [-half, 0, -half]]
    if shape == "diamond":
        return [[0, 0, -half], [half, 0, 0], [0, 0, half], [-half, 0, 0], [0, 0, -half]]
    if shape == "cross":
        return [[-half, 0, 0], [half, 0, 0], [0, 0, 0], [0, 0, -half], [0, 0, half]]
    if shape == "cube":
        return [
            [-half, -half, -half], [half, -half, -half], [half, -half, half],
            [-half, -half, half], [-half, -half, -half], [-half, half, -half],
            [half, half, -half], [half, -half, -half], [half, half, -half],
            [half, half, half], [half, -half, half], [half, half, half],
            [-half, half, half], [-half, -half, half], [-half, half, half],
            [-half, half, -half],
        ]
    raise ValueError(f"Unsupported control shape: {shape}")


def _unused_name(cmds, value: Any, field: str) -> str:
    name = leaf_name(value, field)
    if cmds.ls(name, long=True) or []:
        raise ValueError(f"{field} already exists: {name}")
    return name


def _matrix(cmds, node: str) -> list[float]:
    values = cmds.xform(node, query=True, worldSpace=True, matrix=True) or []
    if len(values) != 16 or any(not math.isfinite(float(value)) for value in values):
        raise RuntimeError(f"Invalid world matrix readback: {node}")
    return [float(value) for value in values]


def _resolve_created(cmds, name: str, expected_type: str = "transform") -> str:
    path = long_path(cmds, name)
    if cmds.nodeType(path) != expected_type:
        raise RuntimeError(f"Created node type mismatch for {path}: {cmds.nodeType(path)}")
    return path


def _create_rig_control(cmds, params: dict[str, Any]) -> dict[str, Any]:
    name = _unused_name(cmds, params["name"], "name")
    shape = params.get("shape", "circle")
    if shape not in _SHAPES:
        raise ValueError("shape must be circle, square, diamond, cross, or cube")
    size = finite(params.get("size", 1.0), "size", minimum=0.001)
    target = long_path(cmds, params["target"]) if params.get("target") else ""
    parent = long_path(cmds, params["parent"]) if params.get("parent") else ""
    match_target = params.get("match_target", True)
    if not isinstance(match_target, bool):
        raise ValueError("match_target must be boolean")
    offset_count = integer(params.get("offset_groups", 1), "offset_groups", minimum=0, maximum=8)
    color = params.get("color_index")
    if color is not None:
        color = integer(color, "color_index", minimum=0, maximum=31)
    lock_attrs = params.get("lock_attrs") or []
    if not isinstance(lock_attrs, list) or len(lock_attrs) > len(_LOCKABLE):
        raise ValueError("lock_attrs must be a bounded array")
    if len(set(lock_attrs)) != len(lock_attrs) or not set(lock_attrs) <= _LOCKABLE:
        raise ValueError("lock_attrs contains duplicate or unsupported attributes")
    group_names = [f"{name}_zero" if index == 0 else f"{name}_offset{index}"
                   for index in range(offset_count)]
    for group_name in group_names:
        _unused_name(cmds, group_name, "offset group name")

    def create():
        if shape == "circle":
            result = cmds.circle(
                name=name, normal=(0.0, 1.0, 0.0), radius=size, constructionHistory=False
            )
            control = str(result[0] if isinstance(result, (list, tuple)) else result)
        else:
            control = str(cmds.curve(name=name, degree=1, point=_shape_points(shape, size)))
        control = _resolve_created(cmds, control)
        if target and match_target:
            cmds.xform(control, worldSpace=True, matrix=_matrix(cmds, target))
        shapes = cmds.listRelatives(control, shapes=True, noIntermediate=True, fullPath=True) or []
        if not shapes:
            raise RuntimeError("Created control has no curve shape")
        if color is not None:
            for curve_shape in shapes:
                cmds.setAttr(f"{curve_shape}.overrideEnabled", True)
                cmds.setAttr(f"{curve_shape}.overrideColor", color)
        for attr in lock_attrs:
            cmds.setAttr(f"{control}.{attr}", lock=True, keyable=False, channelBox=False)
        matrix = _matrix(cmds, control)
        top = control
        groups = []
        for group_name in group_names:
            group = str(cmds.group(empty=True, name=group_name))
            cmds.xform(group, worldSpace=True, matrix=matrix)
            cmds.parent(top, group, absolute=True)
            top = _resolve_created(cmds, group_name)
            groups.append(top)
        if parent:
            result = cmds.parent(top, parent, absolute=True) or []
            if not result:
                raise RuntimeError("Parenting control hierarchy returned no result")
            top = long_path(cmds, result[0])
        return {"control_name": name, "group_names": group_names, "top": top,
                "shape_count": len(shapes)}

    def verify(result):
        control = _resolve_created(cmds, result["control_name"])
        shapes = cmds.listRelatives(control, shapes=True, noIntermediate=True, fullPath=True) or []
        if len(shapes) != result["shape_count"]:
            raise RuntimeError("Control shape count changed during creation")
        result["control"] = control
        result["offset_groups"] = [_resolve_created(cmds, name) for name in result["group_names"]]
        if target and match_target:
            expected = _matrix(cmds, target)
            actual = _matrix(cmds, result["offset_groups"][-1] if result["offset_groups"] else control)
            if any(not math.isclose(left, right, rel_tol=1e-7, abs_tol=1e-7)
                   for left, right in zip(expected, actual)):
                raise RuntimeError("Control hierarchy world matrix readback mismatch")

    result = undo_mutation(cmds, "maya.rig.controls.create", create, verify)
    return {"kind": "rig_control", "control": result["control"], "top_node": result["top"],
            "offset_groups": result["offset_groups"], "shape": shape, "size": size,
            "target": target, "parent": parent, "locked_attrs": lock_attrs, "color_index": color}


def _label(value: Any, name: str, maximum: int) -> str:
    label = leaf_name(value, name)
    if len(label) > maximum or ":" in label:
        raise ValueError(f"{name} must be at most {maximum} characters without namespaces")
    if any(not (char.isascii() and (char.isalnum() or char in "_.-")) for char in label):
        raise ValueError(f"{name} may contain only ASCII letters, numbers, underscore, dash, and dot")
    return label


def _set_metadata(cmds, node: str, name: str, value: Any, kind: str) -> None:
    if not cmds.attributeQuery(name, node=node, exists=True):
        if kind == "string":
            cmds.addAttr(node, longName=name, dataType="string")
        else:
            cmds.addAttr(node, longName=name, attributeType="bool")
    if kind == "string":
        cmds.setAttr(f"{node}.{name}", value, type="string")
    else:
        cmds.setAttr(f"{node}.{name}", bool(value))


def _create_guide_curve(cmds, params: dict[str, Any]) -> dict[str, Any]:
    name = _unused_name(cmds, params["name"], "name")
    degree = integer(params.get("degree", 3), "degree", minimum=1, maximum=7)
    points = params.get("points")
    if not isinstance(points, list) or not degree + 1 <= len(points) <= 256:
        raise ValueError("points must contain degree + 1 through 256 positions")
    validated = [vector(point, f"points[{index}]") for index, point in enumerate(points)]
    cluster_id = _label(params["cluster_id"], "cluster_id", 64)
    color = vector(params["display_color_rgb"], "display_color_rgb", minimum=0.0, maximum=1.0)
    source_view = _label(params["source_view"], "source_view", 64) if params.get("source_view") else ""
    dominant = _label(params["dominant_clump"], "dominant_clump", 128) \
        if params.get("dominant_clump") else ""
    parent = long_path(cmds, params["parent"]) if params.get("parent") else ""

    def create():
        curve = str(cmds.curve(name=name, degree=degree, point=validated))
        curve = _resolve_created(cmds, curve)
        if parent:
            result = cmds.parent(curve, parent, absolute=True) or []
            if not result:
                raise RuntimeError("Parenting guide curve returned no result")
            curve = long_path(cmds, result[0])
        shapes = cmds.listRelatives(curve, shapes=True, noIntermediate=True, fullPath=True) or []
        if len(shapes) != 1 or cmds.nodeType(shapes[0]) != "nurbsCurve":
            raise RuntimeError("Created guide must contain exactly one NURBS curve shape")
        shape = str(shapes[0])
        cmds.setAttr(f"{shape}.overrideEnabled", True)
        cmds.setAttr(f"{shape}.overrideRGBColors", True)
        cmds.setAttr(f"{shape}.overrideColorRGB", *color)
        _set_metadata(cmds, curve, "cgiGuideClusterId", cluster_id, "string")
        _set_metadata(cmds, curve, "cgiGuideRootToTip", True, "bool")
        _set_metadata(cmds, curve, "cgiGuideSourceView", source_view, "string")
        _set_metadata(cmds, curve, "cgiGuideDominantClump", dominant, "string")
        return curve, shape, float(cmds.arclen(curve))

    def verify(result):
        curve, shape, arc_length = result
        if not math.isfinite(arc_length) or arc_length <= 0:
            raise RuntimeError("Guide curve arc length must be finite and positive")
        if cmds.getAttr(f"{curve}.cgiGuideClusterId") != cluster_id:
            raise RuntimeError("Guide cluster metadata readback mismatch")
        actual_color = cmds.getAttr(f"{shape}.overrideColorRGB")
        if isinstance(actual_color, (list, tuple)) and len(actual_color) == 1:
            actual_color = actual_color[0]
        if len(actual_color) != 3 or any(
            not math.isclose(expected, float(actual), rel_tol=1e-7, abs_tol=1e-7)
            for expected, actual in zip(color, actual_color)
        ):
            raise RuntimeError("Guide display color readback mismatch")

    curve, shape, arc_length = undo_mutation(
        cmds, "maya.rig.controls.create", create, verify
    )
    return {"kind": "guide_curve", "transform": curve, "shape": shape,
            "degree": degree, "cv_count": len(validated), "cluster_id": cluster_id,
            "display_color_rgb": color, "root_to_tip": True,
            "root_position": validated[0], "tip_position": validated[-1],
            "arc_length": arc_length, "source_view": source_view,
            "dominant_clump": dominant, "parent": parent}


def create_controls(params: dict, context) -> dict:
    began = started()
    cmds = cmds_from(context)

    def run():
        kind = params.get("kind", "rig_control")
        if kind == "rig_control":
            return _create_rig_control(cmds, params)
        if kind == "guide_curve":
            return _create_guide_curve(cmds, params)
        raise ValueError("kind must be rig_control or guide_curve")

    return receipt(
        context, "maya.rig.controls.create", began, params,
        "MAYA_RIG_CONTROLS_CREATE_FAILED",
        "Use a unique leaf name, finite shape data, and existing target/parent long paths.", run,
    )
