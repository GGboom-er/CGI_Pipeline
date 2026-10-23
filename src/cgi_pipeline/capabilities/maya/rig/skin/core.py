"""Bounded Maya skin binding and weight mutation operations."""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from cgi_pipeline.contracts import ApiContext, make_api_receipt

_MAX_VERTICES = 4096
_MAX_INFLUENCES = 256
_MAX_WEIGHT_VALUES = 65536


def _cmds(context: ApiContext):
    cmds = context.extras.get("cmds_module")
    if cmds is None:
        raise RuntimeError("Maya execution context is required")
    return cmds


def _receipt(context: ApiContext, fallback: str, started: float,
             params: dict[str, Any], operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    api_id = str(context.extras.get("_api_id") or fallback)
    version = str(context.extras.get("_api_version") or "1.0.0")
    try:
        return make_api_receipt(api_id, version, "SUCCESS", started,
                                input_data=dict(params), output=operation())
    except Exception as exc:
        return make_api_receipt(
            api_id, version, "ERROR", started, input_data=dict(params),
            error_code="MAYA_SKIN_OPERATION_FAILED", error=str(exc),
            recovery_hint="Verify the mesh, skinCluster, influences, and bounded vertex rows.",
        )


def _long(cmds, node: str) -> str:
    matches = cmds.ls(node, long=True) or []
    if not matches:
        raise ValueError(f"Node does not exist: {node}")
    if len(matches) > 1 and not str(node).startswith("|"):
        raise ValueError(f"Node name is ambiguous; use a long path: {node}")
    return str(matches[0])


def _mesh(cmds, node: str) -> tuple[str, str]:
    path = _long(cmds, node)
    if cmds.nodeType(path) == "mesh":
        parents = cmds.listRelatives(path, parent=True, fullPath=True) or []
        if not parents:
            raise ValueError(f"Mesh shape has no transform: {path}")
        return _long(cmds, parents[0]), path
    shapes = cmds.listRelatives(path, shapes=True, noIntermediate=True, fullPath=True) or []
    meshes = cmds.ls(shapes, long=True, type="mesh") or []
    if len(meshes) != 1:
        raise ValueError(f"Expected one non-intermediate mesh shape: {path}")
    return path, str(meshes[0])


def _skin_cluster(cmds, shape: str, explicit: str = "", *, required: bool = True) -> str:
    history = cmds.ls(cmds.listHistory(shape, pruneDagObjects=True) or [], long=True) or []
    if explicit:
        cluster = _long(cmds, explicit)
        if cmds.nodeType(cluster) != "skinCluster":
            raise ValueError(f"Node is not a skinCluster: {cluster}")
        if cluster not in history:
            raise ValueError(f"skinCluster does not deform the target mesh: {cluster}")
        return cluster
    clusters = cmds.ls(history, long=True, type="skinCluster") or []
    if not clusters and not required:
        return ""
    if len(clusters) != 1:
        raise ValueError(f"Expected exactly one skinCluster on: {shape}")
    return str(clusters[0])


def _read_weights(cmds, cluster: str, shape: str, vertex: int,
                  influences: list[str]) -> list[float]:
    values = cmds.skinPercent(cluster, f"{shape}.vtx[{vertex}]", query=True, value=True) or []
    if len(values) != len(influences):
        raise RuntimeError(f"Weight count mismatch on vertex {vertex}")
    result = [float(value) for value in values]
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in result):
        raise RuntimeError(f"Invalid native weight on vertex {vertex}")
    return result


def _undo_mutation(cmds, name: str, operation: Callable[[], Any], verify: Callable[[], None] | None = None):
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        result = operation()
        if verify is not None:
            verify()
        cmds.undoInfo(closeChunk=True)
        return result
    except Exception:
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
        raise


def bind_skin(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)

    def run():
        transform, shape = _mesh(cmds, params["mesh"])
        action = params["action"]
        if action == "unbind":
            cluster = _skin_cluster(cmds, shape, params.get("skin_cluster") or "")
            influences = list(cmds.skinCluster(cluster, query=True, influence=True) or [])
            _undo_mutation(cmds, "cgi_skin_unbind",
                           lambda: cmds.skinCluster(cluster, edit=True, unbind=True))
            return {"action": action, "mesh": transform, "shape": shape,
                    "skin_cluster": cluster, "influences": influences}
        if action != "bind":
            raise ValueError(f"Unsupported skin bind action: {action}")
        joints = params.get("joints") or []
        if not 1 <= len(joints) <= _MAX_INFLUENCES:
            raise ValueError(f"joints must contain 1 to {_MAX_INFLUENCES} items")
        resolved = [_long(cmds, joint) for joint in joints]
        if len(set(resolved)) != len(resolved):
            raise ValueError("joints must be unique")
        if any(cmds.nodeType(joint) != "joint" for joint in resolved):
            raise ValueError("every influence must be a Maya joint")
        if _skin_cluster(cmds, shape, required=False):
            raise ValueError(f"Mesh is already bound: {transform}")
        maximum = params.get("max_influences", 4)
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= _MAX_INFLUENCES:
            raise ValueError(f"max_influences must be between 1 and {_MAX_INFLUENCES}")
        method = params.get("bind_method", 0)
        if method not in (0, 1, 2):
            raise ValueError("bind_method must be 0, 1, or 2; method 3 requires geomBind")
        kwargs = {"toSelectedBones": True, "maximumInfluences": maximum, "bindMethod": method}
        if params.get("name"):
            kwargs["name"] = params["name"]
        def create():
            result = cmds.skinCluster(*(resolved + [transform]), **kwargs) or []
            if not result:
                raise RuntimeError("skinCluster did not return the created node")
            return _long(cmds, result[0])
        cluster = _undo_mutation(cmds, "cgi_skin_bind", create)
        return {"action": action, "mesh": transform, "shape": shape,
                "skin_cluster": cluster, "influences": resolved,
                "max_influences": maximum, "bind_method": method}

    return _receipt(context, "maya.rig.skin.bind", started, params, run)


def set_weights(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); cmds = _cmds(context)

    def run():
        _transform, shape = _mesh(cmds, params["mesh"])
        cluster = _skin_cluster(cmds, shape, params.get("skin_cluster") or "")
        influences = [
            _long(cmds, value)
            for value in (cmds.skinCluster(cluster, query=True, influence=True) or [])
        ]
        if not 1 <= len(influences) <= _MAX_INFLUENCES:
            raise ValueError("skinCluster has an invalid influence count")
        rows = params.get("vertices") or []
        if not 1 <= len(rows) <= _MAX_VERTICES:
            raise ValueError(f"vertices must contain 1 to {_MAX_VERTICES} rows")
        if len(rows) * len(influences) > _MAX_WEIGHT_VALUES:
            raise ValueError(f"requested readback exceeds {_MAX_WEIGHT_VALUES} weight values")
        tolerance = params.get("normalization_tolerance", 0.0001)
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
            raise ValueError("normalization_tolerance must be a number")
        tolerance = float(tolerance)
        if not math.isfinite(tolerance) or not 0 < tolerance <= 0.1:
            raise ValueError("normalization_tolerance must be greater than 0 and at most 0.1")
        vertex_count = int(cmds.polyEvaluate(shape, vertex=True))
        allowed = set(influences); seen = set(); validated = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"vertex", "weights"}:
                raise ValueError("each vertex row must contain only vertex and weights")
            vertex = row["vertex"]
            if isinstance(vertex, bool) or not isinstance(vertex, int) or vertex in seen:
                raise ValueError("vertex indices must be unique integers")
            if vertex < 0 or vertex >= vertex_count:
                raise ValueError(f"Vertex index out of range: {vertex}")
            seen.add(vertex)
            weights = row["weights"]
            if not isinstance(weights, dict) or not weights:
                raise ValueError("weights must be a non-empty influence-to-weight object")
            if not set(weights) <= allowed:
                raise ValueError("every weight influence must belong to the skinCluster")
            pairs = []
            for influence, raw in weights.items():
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    raise ValueError("weights must be numbers")
                value = float(raw)
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("weights must be finite values between 0 and 1")
                pairs.append((influence, value))
            if abs(sum(value for _name, value in pairs) - 1.0) > tolerance:
                raise ValueError("each complete weight row must sum to 1")
            validated.append((vertex, pairs))
        for vertex, _pairs in validated:
            _read_weights(cmds, cluster, shape, vertex, influences)

        def apply():
            for vertex, pairs in validated:
                cmds.skinPercent(cluster, f"{shape}.vtx[{vertex}]", transformValue=pairs,
                                 normalize=True, zeroRemainingInfluences=True)

        output_rows = []

        def verify():
            for vertex, pairs in validated:
                values = _read_weights(cmds, cluster, shape, vertex, influences)
                expected = dict(pairs)
                for influence, value in zip(influences, values):
                    if abs(value - expected.get(influence, 0.0)) > tolerance:
                        raise RuntimeError(f"Weight verification failed on vertex {vertex}: {influence}")
                output_rows.append({"vertex": vertex, "weights": {
                    influence: value for influence, value in zip(influences, values)
                    if value > tolerance
                }})

        _undo_mutation(cmds, "cgi_skin_set_weights", apply, verify)
        return {"shape": shape, "skin_cluster": cluster, "influences": influences,
                "rows": output_rows, "count": len(output_rows)}

    return _receipt(context, "maya.rig.skin.set_weights", started, params, run)
