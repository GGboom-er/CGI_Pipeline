"""Read-only Maya skin weight inspection API."""

from __future__ import annotations

import time

from cgi_pipeline.contracts import ApiContext, make_api_receipt


def _mesh_shape(cmds, mesh: str) -> str:
    direct_shapes = cmds.ls(mesh, long=True, type="mesh") or []
    if direct_shapes:
        return direct_shapes[0]
    shapes = cmds.listRelatives(mesh, shapes=True, noIntermediate=True, fullPath=True) or []
    mesh_shapes = cmds.ls(shapes, long=True, type="mesh") or []
    if not mesh_shapes:
        raise ValueError(f"No non-intermediate mesh shape found for: {mesh}")
    return mesh_shapes[0]


def execute(params: dict, context: ApiContext) -> dict:
    """Return a bounded, read-only skin weight sample from the Maya main thread."""
    started_at = time.perf_counter()
    cmds = context.extras.get("cmds_module")
    if cmds is None:
        return make_api_receipt(
            str(context.extras.get("_api_id") or "maya.rig.skin.query_weights"),
            str(context.extras.get("_api_version") or "1.0.0"),
            "ERROR",
            started_at,
            input_data=dict(params),
            error_code="MAYA_CONTEXT_MISSING",
            error="query_weights must run through a Maya API execution context.",
            recovery_hint="Use execution_mode=foreground with a Maya session or the Maya background Worker.",
        )

    try:
        shape = _mesh_shape(cmds, params["mesh"])
        history = cmds.listHistory(shape, pruneDagObjects=True) or []
        clusters = cmds.ls(history, type="skinCluster") or []
        if not clusters:
            raise ValueError(f"Mesh has no skinCluster: {shape}")
        skin_cluster = clusters[0]
        influences = cmds.skinCluster(skin_cluster, query=True, influence=True) or []
        requested = list(params["vertices"])
        vertex_count = int(cmds.polyEvaluate(shape, vertex=True))
        max_vertices = min(params["max_vertices"], vertex_count)
        vertices = requested[:max_vertices] if requested else list(range(max_vertices))
        minimum_weight = params["minimum_weight"]
        influence_filter = params["influence"]

        rows = []
        for vertex_index in vertices:
            if vertex_index < 0 or vertex_index >= vertex_count:
                raise ValueError(f"Vertex index out of range: {vertex_index}")
            component = f"{shape}.vtx[{vertex_index}]"
            values = cmds.skinPercent(skin_cluster, component, query=True, value=True) or []
            weights = {
                influence: weight
                for influence, weight in zip(influences, values)
                if weight >= minimum_weight and (not influence_filter or influence == influence_filter)
            }
            rows.append({"vertex": vertex_index, "weights": weights})

        return make_api_receipt(
            str(context.extras.get("_api_id") or "maya.rig.skin.query_weights"),
            str(context.extras.get("_api_version") or "1.0.0"),
            "SUCCESS",
            started_at,
            input_data=dict(params),
            output={
                "shape": shape,
                "skin_cluster": skin_cluster,
                "influences": influences,
                "rows": rows,
                "vertex_count": vertex_count,
                "truncated": len(requested) > len(vertices) or (not requested and vertex_count > len(vertices)),
            },
        )
    except Exception as exc:
        return make_api_receipt(
            str(context.extras.get("_api_id") or "maya.rig.skin.query_weights"),
            str(context.extras.get("_api_version") or "1.0.0"),
            "ERROR",
            started_at,
            input_data=dict(params),
            error_code="SKIN_QUERY_FAILED",
            error=str(exc),
            recovery_hint="Verify the mesh, skinCluster, vertex indices, and influence name in Maya.",
        )
