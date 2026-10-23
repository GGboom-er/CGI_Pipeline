"""Bounded Maya scene graph operations for high-frequency rig investigation."""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Iterator

from cgi_pipeline.contracts import ApiContext, make_api_receipt


def _identity(context: ApiContext, fallback: str) -> tuple[str, str]:
    return (str(context.extras.get("_api_id") or fallback),
            str(context.extras.get("_api_version") or "1.0.0"))


def _cmds(context: ApiContext):
    cmds = context.extras.get("cmds_module")
    if cmds is None:
        raise RuntimeError("Maya execution context is required")
    return cmds


def _receipt(context, fallback, started, params, fn):
    api_id, version = _identity(context, fallback)
    try:
        return make_api_receipt(api_id, version, "SUCCESS", started,
                                input_data=dict(params), output=fn())
    except Exception as exc:
        return make_api_receipt(api_id, version, "ERROR", started,
                                input_data=dict(params), error_code="MAYA_BATCH_OPERATION_FAILED",
                                error=str(exc), recovery_hint="Verify bounded node and plug names in Maya.")


def _bounded_nodes(cmds, values: list[str], maximum: int) -> tuple[list[str], bool]:
    if maximum < 1:
        raise ValueError("max_nodes must be positive")
    found = []
    seen = set()
    for value in values:
        for node in cmds.ls(value, long=True) or []:
            if node not in seen:
                if len(found) == maximum:
                    return found, True
                seen.add(node); found.append(node)
    return found, False


def inspect_nodes_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        nodes, truncated = _bounded_nodes(cmds, params["nodes"], params["max_nodes"])
        rows = []
        for node in nodes:
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            rows.append({"path": node, "type": cmds.nodeType(node),
                         "uuid": (cmds.ls(node, uuid=True) or [""])[0],
                         "parent": parents[0] if parents else ""})
        return {"nodes": rows, "count": len(rows), "truncated": truncated}
    return _receipt(context, "maya.scene.nodes.inspect_many", started, params, run)


def get_attributes_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        rows = []
        for item in params["items"][:params["max_items"]]:
            plug = f"{item['node']}.{item['attribute']}"
            if not cmds.objExists(plug):
                rows.append({"plug": plug, "exists": False}); continue
            rows.append({"plug": plug, "exists": True, "value": cmds.getAttr(plug),
                         "type": cmds.getAttr(plug, type=True),
                         "locked": bool(cmds.getAttr(plug, lock=True))})
        return {"items": rows, "count": len(rows),
                "truncated": len(params["items"]) > len(rows)}
    return _receipt(context, "maya.scene.attributes.get_many", started, params, run)


def set_attributes_many(params: dict, context: ApiContext) -> dict:
    if "observation" in params:
        from cgi_pipeline.capabilities.maya.scene_edit_guard import execute
        return execute(params, context, "attributes")
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        rows = []
        for item in params["items"][:params["max_items"]]:
            plug = f"{item['node']}.{item['attribute']}"
            if not cmds.objExists(plug):
                raise ValueError(f"Plug does not exist: {plug}")
            old = cmds.getAttr(plug)
            value = item.get("value")
            value_type = item.get("type") or ""
            if value_type:
                cmds.setAttr(plug, value, type=value_type)
            elif isinstance(value, (list, tuple)):
                cmds.setAttr(plug, *value)
            else:
                cmds.setAttr(plug, value)
            rows.append({"plug": plug, "before": old, "after": cmds.getAttr(plug)})
        return {"items": rows, "count": len(rows)}
    return _receipt(context, "maya.scene.attributes.set_many", started, params, run)


def _connection_directions(direction: str) -> tuple[bool, ...]:
    queries = {"both": (True, False), "upstream": (True,), "downstream": (False,)}
    if direction not in queries:
        raise ValueError("direction must be both, upstream, or downstream")
    return queries[direction]


def _connection_pairs(cmds, node: str, directions: tuple[bool, ...]) -> Iterator[dict[str, str]]:
    for incoming in directions:
        raw = cmds.listConnections(node, source=incoming, destination=not incoming,
                                   connections=True, plugs=True, fullNodeName=True,
                                   skipConversionNodes=False) or []
        for i in range(0, len(raw) - 1, 2):
            # Maya puts the queried object's plug first, including for incoming edges.
            source, destination = (raw[i + 1], raw[i]) if incoming else (raw[i], raw[i + 1])
            yield {"source": source, "destination": destination}


def inspect_connections_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        directions = _connection_directions(params.get("direction", "both"))
        nodes, nodes_truncated = _bounded_nodes(cmds, params["nodes"], params["max_nodes"])
        if params["max_edges"] < 1:
            raise ValueError("max_edges must be positive")
        edges, truncated = {}, nodes_truncated
        for node in nodes:
            for edge in _connection_pairs(cmds, node, directions):
                key = (edge["source"], edge["destination"])
                if key in edges:
                    continue
                if len(edges) == params["max_edges"]:
                    truncated = True
                    break
                edges[key] = edge
            else:
                continue
            break
        return {"nodes": nodes, "edges": list(edges.values()),
                "node_count": len(nodes), "edge_count": len(edges),
                "truncated": truncated}
    return _receipt(context, "maya.scene.connections.inspect_many", started, params, run)


def edit_connections_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        rows = []
        for item in params["items"][:params["max_items"]]:
            source, destination = item["source"], item["destination"]
            if item["action"] == "connect":
                cmds.connectAttr(source, destination, force=bool(item.get("force", False)))
            elif item["action"] == "disconnect":
                cmds.disconnectAttr(source, destination)
            else:
                raise ValueError(f"Unsupported connection action: {item['action']}")
            rows.append({"action": item["action"], "source": source,
                         "destination": destination})
        return {"items": rows, "count": len(rows)}
    return _receipt(context, "maya.scene.connections.edit_many", started, params, run)


def snapshot_graph_bounded(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)
    def run():
        directions = _connection_directions(params.get("direction", "both"))
        roots, truncated = _bounded_nodes(cmds, params["roots"], params["max_nodes"])
        if params["max_edges"] < 1:
            raise ValueError("max_edges must be positive")
        queue, queued, seen, edges = deque(roots), set(roots), set(), {}
        while queue:
            if len(seen) == params["max_nodes"]:
                truncated = True
                break
            node = queue.popleft()
            seen.add(node)
            for edge in _connection_pairs(cmds, node, directions):
                key = (edge["source"], edge["destination"])
                if key in edges:
                    continue
                if len(edges) == params["max_edges"]:
                    truncated = True
                    break
                edges[key] = edge
                for plug in edge.values():
                    other = plug.split(".", 1)[0]
                    if other not in queued:
                        queue.append(other); queued.add(other)
            else:
                continue
            break
        node_rows = [{"path": n, "type": cmds.nodeType(n)} for n in sorted(seen)]
        return {"nodes": node_rows, "edges": list(edges.values()),
                "node_count": len(node_rows), "edge_count": len(edges),
                "truncated": truncated}
    return _receipt(context, "maya.scene.graph.snapshot_bounded", started, params, run)


def validate_graph_delta(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter()
    def run():
        before, after = params["before"], params["after"]
        bn = {x["path"] for x in before.get("nodes", [])}; an = {x["path"] for x in after.get("nodes", [])}
        be = {(x["source"], x["destination"]) for x in before.get("edges", [])}
        ae = {(x["source"], x["destination"]) for x in after.get("edges", [])}
        return {"added_nodes": sorted(an - bn), "removed_nodes": sorted(bn - an),
                "added_edges": [{"source": a, "destination": b} for a, b in sorted(ae - be)],
                "removed_edges": [{"source": a, "destination": b} for a, b in sorted(be - ae)],
                "changed": bn != an or be != ae}
    return _receipt(context, "maya.scene.graph.validate_delta", started, params, run)
