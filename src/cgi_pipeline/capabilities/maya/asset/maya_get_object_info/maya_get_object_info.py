"""Read a compact, stable description of one Maya DAG object."""

from __future__ import annotations

import time

import maya.cmds as cmds

from cgi_pipeline.core.receipt import make_receipt


def _resolve(value: str) -> str:
    matches = cmds.ls(value, long=True) or []
    if not matches:
        raise ValueError(f"对象不存在: {value}")
    if len(matches) > 1 and not value.startswith("|"):
        raise ValueError(f"对象名不唯一，请使用长路径: {value}")
    return str(matches[0])


def _query(call, field, errors):
    try:
        return call()
    except Exception as exc:
        errors.append({"field": field, "error_type": type(exc).__name__, "error": str(exc)})
        return None


def _read_fields(fields, prefix, errors):
    return {name: _query(call, f"{prefix}.{name}", errors)
            for name, call in fields.items()}


def _materials(mesh):
    engines = cmds.listConnections(mesh, type="shadingEngine") or []
    return sorted(set(
        material for engine in engines
        for material in (cmds.listConnections(engine + ".surfaceShader", source=True,
                                               destination=False) or [])
    ))


def execute(payload: dict) -> dict:
    started = time.time()
    params = payload.get("parameters") or {}
    value = str(params.get("node") or params.get("object") or "").strip()
    if not value:
        return make_receipt("maya_get_object_info", "ERROR", started,
                            error="缺少必填参数: node")
    try:
        node = _resolve(value)
        node_type = cmds.nodeType(node)
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
        candidates = ([node] if node_type in {"mesh", "camera"} else
                      cmds.listRelatives(node, shapes=True, noIntermediate=True,
                                         fullPath=True) or [])
        mesh = next((s for s in candidates if cmds.nodeType(s) == "mesh"), None)
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        errors = []
        info = {
            "path": node,
            "name": node.rsplit("|", 1)[-1],
            "type": node_type,
            "uuid": (cmds.ls(node, uuid=True) or [""])[0],
            "parent": parents[0] if parents else "",
            "shapes": shapes,
        }
        if node_type in {"transform", "joint"}:
            info["transform"] = _read_fields({
                "translation": lambda: list(cmds.xform(node, q=True, ws=True, t=True)),
                "rotation": lambda: list(cmds.xform(node, q=True, ws=True, ro=True)),
                "scale": lambda: list(cmds.xform(node, q=True, r=True, s=True)),
                "matrix": lambda: list(cmds.xform(node, q=True, ws=True, matrix=True)),
            }, "transform", errors)
        if mesh:
            info["mesh"] = {"shape": mesh, **_read_fields({
                "vertices": lambda: int(cmds.polyEvaluate(mesh, vertex=True)),
                "edges": lambda: int(cmds.polyEvaluate(mesh, edge=True)),
                "faces": lambda: int(cmds.polyEvaluate(mesh, face=True)),
                "triangles": lambda: int(cmds.polyEvaluate(mesh, triangle=True)),
                "uv_sets": lambda: list(cmds.polyUVSet(mesh, q=True, allUVSets=True) or []),
                "bounding_box": lambda: list(cmds.exactWorldBoundingBox(mesh)),
            }, "mesh", errors)}
            info["materials"] = _query(lambda: _materials(mesh), "materials", errors)
        info["history"] = _query(lambda: list(cmds.listHistory(node) or []), "history", errors)
        if node_type == "joint":
            info["joint"] = _read_fields({
                "joint_orient": lambda: list(cmds.getAttr(node + ".jointOrient") or []),
                "rotate_axis": lambda: list(cmds.getAttr(node + ".rotateAxis") or []),
            }, "joint", errors)
        camera = next((s for s in candidates if cmds.nodeType(s) == "camera"), None)
        if camera:
            info["camera"] = {
                "shape": camera,
                "focal_length": _query(lambda: cmds.getAttr(camera + ".focalLength"),
                                       "camera.focal_length", errors),
            }
        info["query_errors"] = errors
        return make_receipt("maya_get_object_info", "PARTIAL" if errors else "SUCCESS", started,
                            input=params, output=info,
                            error=f"{len(errors)} 个字段读取失败，详见 query_errors。" if errors else "",
                            summary_input=node, summary_action="inspect", summary_count=1)
    except Exception as exc:
        return make_receipt("maya_get_object_info", "ERROR", started,
                            input=params, error=str(exc),
                            recovery_hint="确认 node 为唯一的 Maya 长路径。")
