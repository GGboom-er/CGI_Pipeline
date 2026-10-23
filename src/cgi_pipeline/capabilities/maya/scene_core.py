"""Canonical bounded Maya scene CRUD operations."""

from __future__ import annotations

import time
from typing import Any, Callable

from cgi_pipeline.contracts import ApiContext, make_api_receipt

_HARD_MAX = 2000


def _identity(context: ApiContext, fallback: str) -> tuple[str, str]:
    return (str(context.extras.get("_api_id") or fallback),
            str(context.extras.get("_api_version") or "1.0.0"))


def _cmds(context: ApiContext):
    cmds = context.extras.get("cmds_module")
    if cmds is None:
        raise RuntimeError("Maya execution context is required")
    return cmds


def _receipt(context: ApiContext, fallback: str, started: float,
             params: dict[str, Any], operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    api_id, version = _identity(context, fallback)
    try:
        output = operation()
        return make_api_receipt(api_id, version, "SUCCESS", started,
                                input_data=dict(params), output=output)
    except Exception as exc:
        return make_api_receipt(
            api_id, version, "ERROR", started, input_data=dict(params),
            error_code="MAYA_SCENE_CORE_OPERATION_FAILED", error=str(exc),
            recovery_hint="Verify bounded node names and operation parameters in Maya.",
        )


def _limit(params: dict[str, Any]) -> int:
    value = int(params.get("max_items", 200))
    if value < 1 or value > _HARD_MAX:
        raise ValueError(f"max_items must be between 1 and {_HARD_MAX}")
    return value


def _long(cmds, node: str, *, required: bool = True) -> str:
    matches = cmds.ls(node, long=True) or []
    if not matches:
        if required:
            raise ValueError(f"Node does not exist: {node}")
        return ""
    if len(matches) > 1 and not str(node).startswith("|"):
        raise ValueError(f"Node name is ambiguous; use a long path: {node}")
    return str(matches[0])


def _bounded(values: list[Any], maximum: int) -> tuple[list[Any], bool]:
    return values[:maximum], len(values) > maximum


def _rows(items: list[Any], maximum: int, callback: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    bounded, truncated = _bounded(items, maximum)
    rows = []
    for item in bounded:
        row = callback(item)
        row.setdefault("status", "SUCCESS")
        rows.append(row)
    return {"items": rows, "count": len(rows), "truncated": truncated}


def _transform(cmds, node: str, world: bool) -> dict[str, Any]:
    flags = {"query": True, "worldSpace": world, "objectSpace": not world}
    return {
        "translation": list(cmds.xform(node, translation=True, **flags)),
        "rotation": list(cmds.xform(node, rotation=True, **flags)),
        "scale": list(cmds.xform(node, scale=True, **flags)),
    }


def find_nodes(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        maximum = _limit(params); found, seen = [], set()
        query_kwargs = {"long": True}
        if params.get("node_type"):
            query_kwargs["type"] = params["node_type"]
        for pattern in params.get("patterns") or ["*"]:
            for node in cmds.ls(pattern, **query_kwargs) or []:
                if node not in seen:
                    seen.add(node); found.append(node)
        bounded, truncated = _bounded(found, maximum)
        return {"items": [{"path": node, "type": cmds.nodeType(node), "status": "SUCCESS"}
                          for node in bounded], "count": len(bounded), "truncated": truncated}
    return _receipt(context, "maya.scene.nodes.find", started, params, run)


def create_nodes(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        def create(item):
            parent = _long(cmds, item["parent"]) if item.get("parent") else ""
            kwargs = {"name": item["name"]} if item.get("name") else {}
            if parent: kwargs["parent"] = parent
            created = cmds.createNode(item["node_type"], **kwargs)
            after = _long(cmds, created)
            return {"before": None, "after": after, "node_type": cmds.nodeType(after)}
        return _rows(params["items"], _limit(params), create)
    return _receipt(context, "maya.scene.nodes.create", started, params, run)


def rename_nodes(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        def rename(item):
            before = _long(cmds, item["node"])
            after = _long(cmds, cmds.rename(before, item["new_name"]))
            return {"before": before, "after": after}
        return _rows(params["items"], _limit(params), rename)
    return _receipt(context, "maya.scene.nodes.rename", started, params, run)


def delete_nodes(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        def delete(node):
            before = _long(cmds, node); cmds.delete(before)
            return {"before": before, "after": None}
        return _rows(params["nodes"], _limit(params), delete)
    return _receipt(context, "maya.scene.nodes.delete", started, params, run)


def duplicate_nodes_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        def duplicate(item):
            before = _long(cmds, item["node"])
            kwargs = {"name": item["name"]} if item.get("name") else {}
            created = (cmds.duplicate(before, **kwargs) or [""])[0]
            return {"before": before, "after": _long(cmds, created)}
        return _rows(params["items"], _limit(params), duplicate)
    return _receipt(context, "maya.scene.nodes.duplicate_many", started, params, run)


def edit_hierarchy_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        def edit(item):
            before = _long(cmds, item["node"]); action = item["action"]
            if action == "parent":
                parent = _long(cmds, item.get("parent") or "")
                changed = (cmds.parent(before, parent) or [""])[0]
            elif action == "unparent":
                changed = (cmds.parent(before, world=True) or [""])[0]
            else:
                raise ValueError(f"Unsupported hierarchy action: {action}")
            return {"action": action, "before": before, "after": _long(cmds, changed)}
        return _rows(params["items"], _limit(params), edit)
    return _receipt(context, "maya.scene.hierarchy.edit_many", started, params, run)


def get_selection(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        items = list(cmds.ls(selection=True, long=True) or [])
        bounded, truncated = _bounded(items, _limit(params))
        return {"items": bounded, "count": len(bounded), "truncated": truncated}
    return _receipt(context, "maya.scene.selection.get", started, params, run)


def set_selection(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        maximum = _limit(params); nodes, truncated = _bounded(params.get("nodes") or [], maximum)
        resolved = [_long(cmds, node) for node in nodes]
        before = list(cmds.ls(selection=True, long=True) or [])
        mode = params.get("mode", "replace")
        if mode == "clear": cmds.select(clear=True)
        elif mode == "replace": cmds.select(resolved, replace=True)
        elif mode == "add": cmds.select(resolved, add=True)
        elif mode == "remove": cmds.select(resolved, deselect=True)
        else: raise ValueError(f"Unsupported selection mode: {mode}")
        after = list(cmds.ls(selection=True, long=True) or [])
        return {"before": before, "after": after, "status": "SUCCESS", "truncated": truncated}
    return _receipt(context, "maya.scene.selection.set", started, params, run)


def get_transforms(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        world = params.get("space", "world") == "world"
        def get(node):
            path = _long(cmds, node)
            return {"path": path, "space": "world" if world else "object", **_transform(cmds, path, world)}
        return _rows(params["nodes"], _limit(params), get)
    return _receipt(context, "maya.scene.transforms.get", started, params, run)


def set_transforms_many(params: dict, context: ApiContext) -> dict:
    if "observation" in params:
        from cgi_pipeline.capabilities.maya.scene_edit_guard import execute
        return execute(params, context, "transforms")
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        world = params.get("space", "world") == "world"
        def set_transform(item):
            path = _long(cmds, item["node"]); before = _transform(cmds, path, world)
            flags = {"worldSpace": world, "objectSpace": not world}
            for key in ("translation", "rotation", "scale"):
                if key in item: cmds.xform(path, **flags, **{key: item[key]})
            return {"path": path, "before": before, "after": _transform(cmds, path, world)}
        return _rows(params["items"], _limit(params), set_transform)
    return _receipt(context, "maya.scene.transforms.set_many", started, params, run)
