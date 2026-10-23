"""Bounded Maya attribute declarations, without reading or evaluating values."""

from __future__ import annotations

import time
from fnmatch import fnmatchcase

from cgi_pipeline.contracts import ApiContext
from .scene_batch import _bounded_nodes, _cmds, _receipt

_HARD_MAX = 2000
_FLAGS = ("readable", "writable", "connectable", "multi", "keyable", "hidden")
_LIMITS = (("minimum", "minExists", "minimum"),
           ("maximum", "maxExists", "maximum"),
           ("soft_minimum", "softMinExists", "softMin"),
           ("soft_maximum", "softMaxExists", "softMax"))


def _validate_request(params: dict) -> tuple[int, int]:
    limits = []
    for name, default in (("max_nodes", 200), ("max_attributes", 500)):
        value = params.get(name, default)
        if type(value) is not int or not 1 <= value <= _HARD_MAX:
            raise ValueError(f"{name} must be an integer between 1 and {_HARD_MAX}")
        limits.append(value)
    nodes = params.get("nodes")
    if (not isinstance(nodes, list) or len(nodes) > _HARD_MAX
            or any(not isinstance(node, str) or not node.strip() for node in nodes)):
        raise ValueError(f"nodes must be an explicit list of up to {_HARD_MAX} nonempty names or patterns")
    pattern = params.get("attribute_pattern", "*")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("attribute_pattern must be a nonempty glob string")
    for name in ("user_defined", "keyable"):
        if name in params and type(params[name]) is not bool:
            raise ValueError(f"{name} must be boolean when supplied")
    return limits[0], limits[1]


def _query_bool(cmds, node: str, name: str, flag: str) -> bool:
    result = cmds.attributeQuery(name, node=node, **{flag: True})
    if type(result) not in (bool, int) or result not in (0, 1):
        raise ValueError(f"Invalid {flag} schema for {node}.{name}: {result!r}")
    return bool(result)


def _schema(cmds, node: str, name: str, user_defined: bool) -> dict:
    attr_type = cmds.attributeQuery(name, node=node, attributeType=True)
    if isinstance(attr_type, (list, tuple)) and len(attr_type) == 1:
        attr_type = attr_type[0]
    if not isinstance(attr_type, str) or not attr_type:
        raise ValueError(f"Missing attribute type for {node}.{name}")
    row = {"name": name, "plug": f"{node}.{name}", "type": attr_type,
           "user_defined": user_defined}
    row.update({flag: _query_bool(cmds, node, name, flag) for flag in _FLAGS})
    row["children"] = cmds.attributeQuery(name, node=node, listChildren=True) or []
    row["enum"] = (cmds.attributeQuery(name, node=node, listEnum=True) or []
                   if attr_type == "enum" else [])
    row["limits"] = {
        key: cmds.attributeQuery(name, node=node, **{query: True})
        for key, exists, query in _LIMITS if _query_bool(cmds, node, name, exists)
    }
    return row


def _attribute_names(cmds, node: str, params: dict) -> tuple[list[str], set[str]]:
    names = list(dict.fromkeys(cmds.listAttr(node) or []))
    user_defined = set(cmds.listAttr(node, userDefined=True) or [])
    if "user_defined" in params:
        names = [name for name in names if (name in user_defined) == params["user_defined"]]
    if "keyable" in params:
        keyable = set(cmds.listAttr(node, keyable=True) or [])
        names = [name for name in names if (name in keyable) == params["keyable"]]
    pattern = params.get("attribute_pattern", "*")
    return [name for name in names if fnmatchcase(name, pattern)], user_defined


def _inspect_node(cmds, node: str, params: dict, remaining: int) -> dict:
    uuids = cmds.ls(node, uuid=True) or []
    if len(uuids) != 1 or not isinstance(uuids[0], str) or not uuids[0]:
        raise ValueError(f"Cannot establish unique node identity: {node}")
    names, user_defined = _attribute_names(cmds, node, params)
    attributes = [_schema(cmds, node, name, name in user_defined)
                  for name in names[:remaining]]
    return {"path": node, "uuid": uuids[0], "type": cmds.nodeType(node),
            "attributes": attributes, "count": len(attributes),
            "truncated": len(names) > remaining}


def inspect_attributes(params: dict, context: ApiContext) -> dict:
    """Inspect declarations with one total attribute budget across resolved nodes."""
    started = time.perf_counter()

    def run():
        max_nodes, max_attributes = _validate_request(params)
        cmds = _cmds(context)
        nodes, nodes_truncated = _bounded_nodes(cmds, params["nodes"], max_nodes)
        rows, count = [], 0
        for node in nodes:
            row = _inspect_node(cmds, node, params, max_attributes - count)
            rows.append(row)
            count += row["count"]
        attributes_truncated = any(row["truncated"] for row in rows)
        return {"nodes": rows, "node_count": len(rows), "attribute_count": count,
                "nodes_truncated": nodes_truncated,
                "attributes_truncated": attributes_truncated,
                "truncated": nodes_truncated or attributes_truncated}

    return _receipt(context, "maya.scene.attributes.inspect", started, params, run)
