"""Read a bounded Maya DAG hierarchy without mutating the scene."""

from cgi_pipeline.capabilities.runtime import execute_operation


def _implementation(payload):
    import maya.cmds as cmds
    params = payload.get("parameters", {}) or {}
    root = str(params.get("root") or "|").strip() or "|"
    depth = int(params.get("depth", -1))
    include_shapes = bool(params.get("include_shapes", False))
    if root != "|" and not cmds.objExists(root):
        raise ValueError(f"节点不存在: {root}")
    if root == "|":
        nodes = cmds.ls(assemblies=True, long=True) or []
        descendants = []
        for node in nodes:
            descendants.extend(cmds.listRelatives(node, allDescendents=True, fullPath=True) or [])
        nodes.extend(descendants)
    else:
        nodes = [cmds.ls(root, long=True)[0]]
        nodes.extend(cmds.listRelatives(root, allDescendents=True, fullPath=True) or [])
    if depth >= 0:
        # A DAG path's leading ``|`` is not a hierarchy level: ``|root`` is
        # depth 0 below the world root.  Keep depth semantics identical for a
        # world query and a named-root query.
        base_depth = 1 if root == "|" else root.count("|")
        nodes = [n for n in nodes if n.count("|") - base_depth <= depth]
    if not include_shapes:
        nodes = [n for n in nodes if cmds.objectType(n, isAType="transform")]
    return {
        "status": "SUCCESS",
        "output": {"root": root, "depth": depth, "include_shapes": include_shapes,
                   "nodes": [{"path": n, "type": cmds.nodeType(n)} for n in nodes]},
        "summary": f"读取层级 {root}，返回 {len(nodes)} 个节点",
    }


def execute(params, context):
    return execute_operation(_implementation, params, context)
