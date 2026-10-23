"""Create a bounded polygon primitive in the active Maya scene.

The operation is intentionally small and deterministic: it creates one mesh
transform, optionally parents it, and returns the resolved long paths.  It is
used as a reusable building block for scene setup and smoke tests; higher
level asset workflows should compose it rather than emit ad-hoc ``cmds``.
"""

from __future__ import annotations

import time

import maya.cmds as cmds

from cgi_pipeline.core.receipt import make_receipt


_ALIASES = {"box": "cube", "torus": "torus"}


def _long(node: str) -> str:
    matches = cmds.ls(node, long=True) or []
    if not matches:
        raise RuntimeError(f"创建后无法解析节点: {node}")
    return str(matches[0])


def execute(payload: dict) -> dict:
    started = time.time()
    params = payload.get("parameters") or {}
    primitive = str(params.get("primitive_type") or params.get("type") or "cube").lower()
    primitive = _ALIASES.get(primitive, primitive)
    name = str(params.get("name") or "primitive")
    parent = str(params.get("parent") or "").strip()
    if primitive not in {"cube", "sphere", "cylinder", "cone", "plane", "torus"}:
        return make_receipt("maya_create_primitive", "ERROR", started,
                            error=f"不支持的 primitive_type: {primitive}")

    dimensions = params.get("dimensions") or {}
    try:
        if parent:
            parent_matches = cmds.ls(parent, long=True) or []
            if not parent_matches:
                return make_receipt("maya_create_primitive", "ERROR", started,
                                    error=f"父节点不存在: {parent}")
            parent = parent_matches[0]

        kwargs = {"name": name}
        if primitive == "cube":
            size = float(params.get("size", dimensions.get("size", 1.0)))
            created = cmds.polyCube(width=size, height=size, depth=size, **kwargs)
        elif primitive == "sphere":
            created = cmds.polySphere(radius=float(params.get("radius", 1.0)), **kwargs)
        elif primitive == "cylinder":
            created = cmds.polyCylinder(
                radius=float(params.get("radius", 1.0)),
                height=float(params.get("height", 2.0)), **kwargs)
        elif primitive == "cone":
            created = cmds.polyCone(
                radius=float(params.get("radius", 1.0)),
                height=float(params.get("height", 2.0)), **kwargs)
        elif primitive == "plane":
            created = cmds.polyPlane(
                width=float(params.get("width", 1.0)),
                height=float(params.get("height", 1.0)), **kwargs)
        else:
            created = cmds.polyTorus(
                majorRadius=float(params.get("major_radius", 1.0)),
                minorRadius=float(params.get("minor_radius", 0.25)), **kwargs)
        transform = _long((created or [name])[0])
        if parent:
            transform = _long((cmds.parent(transform, parent) or [transform])[0])
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True,
                                    type="mesh") or []
        return make_receipt(
            "maya_create_primitive", "SUCCESS", started,
            input=params,
            output={"transform": transform, "shapes": shapes,
                    "primitive_type": primitive},
            summary_input=primitive, summary_action="create", summary_count=1,
        )
    except Exception as exc:
        return make_receipt("maya_create_primitive", "ERROR", started,
                            input=params, error=str(exc),
                            recovery_hint="检查 primitive 参数、父节点和 Maya 建模上下文。")
