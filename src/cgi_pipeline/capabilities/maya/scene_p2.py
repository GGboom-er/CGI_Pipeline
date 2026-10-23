"""Shared Maya scene schema, identity, and node-state operations."""

from __future__ import annotations

import math
import re
import time
from typing import Any, Callable

from cgi_pipeline.contracts import ApiContext, make_api_receipt

_HARD_MAX = 500
_ATTRIBUTE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRS_CHANNELS = tuple(
    f"{group}{axis}"
    for group in ("translate", "rotate", "scale")
    for axis in "XYZ"
)


def _cmds(context: ApiContext):
    cmds = context.extras.get("cmds_module")
    if cmds is None:
        raise RuntimeError("Maya execution context is required")
    return cmds


def _limit(params: dict[str, Any]) -> int:
    value = params.get("max_items", 100)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _HARD_MAX:
        raise ValueError(f"max_items must be an integer between 1 and {_HARD_MAX}")
    return value


def _items(params: dict[str, Any]) -> list[dict[str, Any]]:
    values = params.get("items")
    maximum = _limit(params)
    if not isinstance(values, list) or not 1 <= len(values) <= maximum:
        raise ValueError(f"items must contain 1 to {maximum} entries")
    if any(not isinstance(item, dict) for item in values):
        raise ValueError("every item must be an object")
    return values


def _long(cmds, node: str) -> str:
    if not isinstance(node, str) or not node:
        raise ValueError("node must be a non-empty string")
    matches = cmds.ls(node, long=True) or []
    if not matches:
        raise ValueError(f"Node does not exist: {node}")
    if len(matches) > 1 and not node.startswith("|"):
        raise ValueError(f"Node name is ambiguous; use a long path: {node}")
    return str(matches[0])


def _receipt(context: ApiContext, fallback: str, started: float, params: dict[str, Any],
             operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    api_id = str(context.extras.get("_api_id") or fallback)
    version = str(context.extras.get("_api_version") or "1.0.0")
    try:
        return make_api_receipt(api_id, version, "SUCCESS", started,
                                input_data=dict(params), output=operation())
    except Exception as exc:
        return make_api_receipt(
            api_id, version, "ERROR", started, input_data=dict(params),
            error_code="MAYA_SCENE_P2_OPERATION_FAILED", error=str(exc),
            recovery_hint="Use explicit long paths and bounded canonical scene inputs.",
        )


def _undo(cmds, name: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        result = operation()
        cmds.undoInfo(closeChunk=True)
        return result
    except Exception:
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
        raise


def resolve_nodes(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter()
    cmds = _cmds(context)

    def run():
        nodes = params.get("nodes")
        maximum = _limit(params)
        include_instances = params.get("include_instances", False)
        if type(include_instances) is not bool:
            raise ValueError("include_instances must be boolean")
        if include_instances:
            from .scene_observation import require_main_thread
            require_main_thread()
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= maximum:
            raise ValueError(f"nodes must contain 1 to {maximum} entries")
        rows = []
        for node in nodes:
            path = _long(cmds, node)
            uuids = cmds.ls(path, uuid=True) or []
            if len(uuids) != 1:
                raise RuntimeError(f"Expected one UUID for: {path}")
            row = {"input": node, "path": path, "uuid": str(uuids[0]),
                   "node_type": str(cmds.nodeType(path))}
            if include_instances:
                from .scene_observation import node_identity
                row["identity"] = node_identity(cmds, path)
            rows.append(row)
        output = {"items": rows, "count": len(rows)}
        if include_instances:
            from .scene_observation import session_identity
            output["session"] = session_identity(cmds)
        return output

    return _receipt(context, "maya.scene.nodes.resolve", started, params, run)


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def manage_attribute_schema(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter()
    cmds = _cmds(context)

    def run():
        prepared = []
        seen_plugs = set()
        for item in _items(params):
            allowed = {"action", "node", "attribute", "attribute_type", "default",
                       "keyable", "enum_names"}
            if not set(item) <= allowed:
                raise ValueError("attribute item contains unsupported keys")
            action = item.get("action")
            if action not in {"add", "delete"}:
                raise ValueError(f"Unsupported attribute schema action: {action}")
            node = _long(cmds, item.get("node"))
            attribute = item.get("attribute")
            if not isinstance(attribute, str) or not _ATTRIBUTE_NAME.fullmatch(attribute):
                raise ValueError("attribute must be a valid long attribute name")
            exists = bool(cmds.attributeQuery(attribute, node=node, exists=True))
            plug = f"{node}.{attribute}"
            if plug in seen_plugs:
                raise ValueError(f"attribute actions must target unique plugs: {plug}")
            seen_plugs.add(plug)
            if action == "add" and exists:
                raise ValueError(f"Attribute already exists: {node}.{attribute}")
            if action == "delete":
                user_defined = set(cmds.listAttr(node, userDefined=True) or [])
                if attribute not in user_defined:
                    raise ValueError(f"Attribute is not user-defined: {node}.{attribute}")
                prepared.append({"action": action, "node": node, "attribute": attribute})
                continue
            attribute_type = item.get("attribute_type", "double")
            if attribute_type not in {"double", "long", "bool", "string", "enum"}:
                raise ValueError(f"Unsupported attribute_type: {attribute_type}")
            keyable = item.get("keyable", True)
            if not isinstance(keyable, bool):
                raise ValueError("keyable must be a boolean")
            enum_names = item.get("enum_names") or []
            if attribute_type == "enum":
                if not isinstance(enum_names, list) or not enum_names or any(
                    not isinstance(name, str) or not name or ":" in name for name in enum_names
                ):
                    raise ValueError("enum_names must contain non-empty names without colons")
            default = item.get("default")
            if default is not None and attribute_type in {"double", "long"}:
                default = _number(default, "default")
                if attribute_type == "long" and not default.is_integer():
                    raise ValueError("long default must be an integer")
                default = int(default) if attribute_type == "long" else default
            if default is not None and attribute_type == "bool" and not isinstance(default, bool):
                raise ValueError("bool default must be a boolean")
            if default is not None and attribute_type == "string" and not isinstance(default, str):
                raise ValueError("string default must be a string")
            if default is not None and attribute_type == "enum":
                if isinstance(default, bool) or not isinstance(default, int):
                    raise ValueError("enum default must be an integer index")
                if not 0 <= default < len(enum_names):
                    raise ValueError("enum default index is out of range")
            prepared.append({"action": action, "node": node, "attribute": attribute,
                             "attribute_type": attribute_type, "default": default,
                             "keyable": keyable, "enum_names": enum_names})

        def apply():
            rows = []
            for item in prepared:
                plug = f"{item['node']}.{item['attribute']}"
                if item["action"] == "delete":
                    before_type = str(cmds.getAttr(plug, type=True))
                    cmds.deleteAttr(plug)
                    if cmds.attributeQuery(item["attribute"], node=item["node"], exists=True):
                        raise RuntimeError(f"Attribute delete verification failed: {plug}")
                    rows.append({"action": "delete", "plug": plug,
                                 "before_type": before_type, "after_type": None})
                    continue
                kwargs = {"longName": item["attribute"], "keyable": item["keyable"]}
                if item["attribute_type"] == "string":
                    kwargs["dataType"] = "string"
                else:
                    kwargs["attributeType"] = item["attribute_type"]
                if item["attribute_type"] == "enum":
                    kwargs["enumName"] = ":".join(item["enum_names"])
                if item["default"] is not None and item["attribute_type"] != "string":
                    kwargs["defaultValue"] = item["default"]
                cmds.addAttr(item["node"], **kwargs)
                if item["default"] is not None and item["attribute_type"] == "string":
                    cmds.setAttr(plug, item["default"], type="string")
                if not cmds.attributeQuery(item["attribute"], node=item["node"], exists=True):
                    raise RuntimeError(f"Attribute add verification failed: {plug}")
                if item["default"] is not None:
                    if item["attribute_type"] == "string":
                        actual_default = cmds.getAttr(plug)
                    else:
                        defaults = cmds.attributeQuery(
                            item["attribute"], node=item["node"], listDefault=True
                        ) or []
                        actual_default = defaults[0] if defaults else None
                    if actual_default != item["default"]:
                        raise RuntimeError(f"Attribute default verification failed: {plug}")
                rows.append({"action": "add", "plug": plug, "before_type": None,
                             "after_type": str(cmds.getAttr(plug, type=True))})
            return {"items": rows, "count": len(rows)}

        return _undo(cmds, "maya.scene.attributes.schema", apply)

    return _receipt(context, "maya.scene.attributes.schema", started, params, run)


def manage_node_state(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter()
    cmds = _cmds(context)

    def run():
        prepared = []
        for item in _items(params):
            if not set(item) <= {"node", "visibility", "lock_trs"}:
                raise ValueError("node state item contains unsupported keys")
            node = _long(cmds, item.get("node"))
            if "visibility" not in item and "lock_trs" not in item:
                raise ValueError("node state item requires visibility or lock_trs")
            visibility = item.get("visibility")
            lock_trs = item.get("lock_trs")
            if "visibility" in item and not isinstance(visibility, bool):
                raise ValueError("visibility must be a boolean")
            if "lock_trs" in item and not isinstance(lock_trs, bool):
                raise ValueError("lock_trs must be a boolean")
            if "lock_trs" in item and cmds.nodeType(node) not in {"transform", "joint"}:
                raise ValueError(f"TRS locking requires a transform or joint: {node}")
            before = {
                "visibility": bool(cmds.getAttr(f"{node}.visibility")),
                "lock_trs": all(bool(cmds.getAttr(f"{node}.{channel}", lock=True))
                                for channel in _TRS_CHANNELS),
            }
            prepared.append((node, visibility, lock_trs, before, set(item)))

        def apply():
            rows = []
            for node, visibility, lock_trs, before, keys in prepared:
                if "visibility" in keys:
                    cmds.setAttr(f"{node}.visibility", visibility)
                if "lock_trs" in keys:
                    for channel in _TRS_CHANNELS:
                        cmds.setAttr(f"{node}.{channel}", lock=lock_trs)
                lock_values = [
                    bool(cmds.getAttr(f"{node}.{channel}", lock=True))
                    for channel in _TRS_CHANNELS
                ]
                after = {
                    "visibility": bool(cmds.getAttr(f"{node}.visibility")),
                    "lock_trs": all(lock_values),
                }
                if "visibility" in keys and after["visibility"] != visibility:
                    raise RuntimeError(f"Visibility verification failed: {node}")
                if "lock_trs" in keys:
                    verified = all(lock_values) if lock_trs else not any(lock_values)
                    if not verified:
                        raise RuntimeError(f"TRS lock verification failed: {node}")
                rows.append({"node": node, "before": before, "after": after})
            return {"items": rows, "count": len(rows)}

        return _undo(cmds, "maya.scene.nodes.state", apply)

    return _receipt(context, "maya.scene.nodes.state", started, params, run)
